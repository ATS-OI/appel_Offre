"""
sources/commun.py — normalisation partagée entre les 5 sources
================================================================================

Chaque source (boamp.py, ted.py, aws_solutions.py, place.py, e_marche.py)
renvoie une liste de dicts au MÊME format :

    {
        "source": "BOAMP",                # nom de la source
        "identifiant": "24-24231",         # identifiant d'origine
        "objet": "...",
        "description": "...",              # peut être vide
        "lots": [{"identifiant","titre","description"}, ...],  # peut être vide
        "acheteur": "...",
        "departement": "974, 976",         # texte, codes séparés par virgule
        "date_parution": "2026-08-01",     # AAAA-MM-JJ ou ""
        "date_limite_reponse": "2026-09-01",
        "url": "...",
    }

Ce fichier regroupe tout ce qui est commun aux 5 sources : normalisation de
texte, fusion des doublons (un même marché republié/rectifié plusieurs fois,
y compris à cheval entre deux sources — e-marchespublics agrège lui-même
BOAMP/JOUE, donc ce cas est particulièrement fréquent entre EMP et
BOAMP/TED) et le filtre final mots-clés/lots/acheteurs suivis.

Contient aussi `assurer_navigateur_installe()`, utilisé par les 2 sources
qui ont besoin d'un navigateur headless (aws_solutions.py, e_marche.py) —
factorisé ici pour ne pas dupliquer le contournement Streamlit Cloud (voir
sa docstring) dans chaque fichier.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from difflib import SequenceMatcher

# Le bac à sable ci-dessous (bloc `__main__`) imprime des accents/emoji —
# plante sur une console Windows par défaut (cp1252) sans ce garde-fou.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Seuil de similarité (0-1, via difflib) au-dessus duquel deux objets d'avis
# (même acheteur) sont considérés comme le même dossier républié/corrigé.
SEUIL_SIMILARITE_OBJET = 0.80

_navigateur_verifie = False


def assurer_navigateur_installe() -> None:
    """S'assure que le navigateur headless Chromium (nécessaire à Playwright)
    est bien installé avant de l'utiliser — appelé par les sources qui font
    du login navigateur (aws_solutions.py, e_marche.py).

    En local/VM, l'installation se fait une fois pour toutes via `playwright
    install chromium` (voir README.md). Sur certains hébergeurs (ex.
    Streamlit Community Cloud), cette étape n'est JAMAIS exécutée
    automatiquement — seul `pip install -r requirements.txt` l'est — d'où
    l'erreur "Executable doesn't exist..." au premier lancement. On la
    déclenche donc ici, une fois par process : `playwright install` est
    déjà idempotent (quasi instantané s'il est déjà installé), donc sans
    coût perceptible les fois suivantes.

    `sys.executable -m playwright` plutôt que la commande `playwright` nue :
    cette dernière suppose que le script CLI est sur le PATH, ce qui n'est
    pas garanti selon comment le package a été installé (bug rencontré en
    pratique en local : `FileNotFoundError` alors que le package Python
    `playwright` était bien installé) — `-m` passe par l'interpréteur
    Python en cours d'exécution, toujours correct. Le tout est en best-effort
    (`try/except`) : si ça échoue quand même, le vrai lancement du
    navigateur juste après donnera de toute façon une erreur claire,
    remontée normalement par `sources/__init__.py`.
    """
    global _navigateur_verifie
    if _navigateur_verifie:
        return
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False, capture_output=True)
    except Exception:
        pass
    _navigateur_verifie = True


def normaliser_texte(texte: str) -> str:
    """Normalise un texte pour comparaison approximative (accents, casse, ponctuation)."""
    texte = texte or ""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()
    return texte


def texte_lots(lots: list[dict] | None) -> str:
    """Concatène titres + descriptions de tous les lots d'un avis en un seul texte."""
    morceaux = []
    for lot in lots or []:
        if lot.get("titre"):
            morceaux.append(lot["titre"])
        if lot.get("description"):
            morceaux.append(lot["description"])
    return " ".join(morceaux)


# =====================================================================
# Fusion des doublons (avis édités/rectifiés plusieurs fois, y compris à
# cheval entre deux sources)
# =====================================================================

def _memes_avis(a: dict, b: dict) -> bool:
    """Deux avis sont le même dossier si l'acheteur normalisé est identique
    ET si l'un des objets contient l'autre, ou si leur similarité textuelle
    dépasse `SEUIL_SIMILARITE_OBJET` (utile notamment pour TED, qui préfixe
    systématiquement le titre par "France – <catégorie CPV> – ").

    ⚠️ Il n'existe pas d'identifiant officiel commun entre les 4 sources : ce
    rapprochement est donc approximatif — à vérifier via les URLs en cas de doute.
    """
    acheteur_a = normaliser_texte(a["acheteur"])
    acheteur_b = normaliser_texte(b["acheteur"])
    if not acheteur_a or acheteur_a != acheteur_b:
        return False

    objet_a = normaliser_texte(a["objet"])
    objet_b = normaliser_texte(b["objet"])
    if not objet_a or not objet_b:
        return False
    if objet_a in objet_b or objet_b in objet_a:
        return True
    return SequenceMatcher(None, objet_a, objet_b).ratio() >= SEUIL_SIMILARITE_OBJET


class _UnionFind:
    """Petite structure union-find pour regrouper les avis liés par transitivité."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def fusionner_doublons(resultats: list[dict], liens_precis: dict[int, list[str]] | None = None) -> list[dict]:
    """Regroupe tous les avis d'un même dossier en une seule ligne.

    `liens_precis` (optionnel) : {index -> [identifiants explicitement liés]}
    — utilisé par BOAMP, qui référence l'idweb de l'avis qu'un rectificatif
    modifie (lien fiable, en plus du rapprochement heuristique acheteur+objet
    appliqué à toutes les sources).

    La ligne conservée reprend l'objet/acheteur/lots/description de l'avis
    dont la date limite de réponse est la plus tardive du groupe, mais liste
    tous les identifiants et URLs du groupe.
    """
    n = len(resultats)
    if n == 0:
        return []

    uf = _UnionFind(n)
    index_par_id = {r["identifiant"]: i for i, r in enumerate(resultats) if r["identifiant"]}

    liens_precis = liens_precis or {}
    for i, parents in liens_precis.items():
        for parent_id in parents:
            j = index_par_id.get(parent_id)
            if j is not None:
                uf.union(i, j)

    for i in range(n):
        for j in range(i + 1, n):
            if _memes_avis(resultats[i], resultats[j]):
                uf.union(i, j)

    groupes: dict[int, list[int]] = {}
    for i in range(n):
        groupes.setdefault(uf.find(i), []).append(i)

    fusionnes = []
    for indices in groupes.values():
        membres = [resultats[i] for i in indices]
        membres_tries = sorted(
            membres,
            key=lambda r: (r["date_limite_reponse"] or "", r["date_parution"] or ""),
            reverse=True,
        )
        representant = membres_tries[0]
        sources = sorted({m["source"] for m in membres})
        departements = sorted({
            d.strip() for m in membres for d in (m["departement"] or "").split(",") if d.strip()
        })
        urls = list(dict.fromkeys(m["url"] for m in membres_tries if m["url"]))

        fusionnes.append({
            "source": sources,
            "identifiants": [m["identifiant"] for m in membres_tries if m["identifiant"]],
            "objet": representant["objet"],
            "description": representant.get("description") or next(
                (m.get("description") for m in membres_tries if m.get("description")), ""
            ),
            "lots": representant.get("lots") or next(
                (m.get("lots") for m in membres_tries if m.get("lots")), []
            ),
            "acheteur": representant["acheteur"],
            "departement": ", ".join(departements),
            "date_parution": representant["date_parution"],
            "date_limite_reponse": representant["date_limite_reponse"],
            "urls": urls,
            "nb_versions": len(membres),
        })

    fusionnes.sort(key=lambda r: r["date_limite_reponse"] or "9999-99-99")
    return fusionnes


# =====================================================================
# Filtre final (mots-clés / lots / acheteurs suivis)
# =====================================================================

def trouver_correspondances(
    groupe: dict, mots_cles: list[str], mots_cles_lots: list[str], acheteurs: list[str]
) -> list[str]:
    """Renvoie les termes (texte d'origine, pas normalisé) qui ont fait
    matcher cet avis : mots-clés trouvés dans l'objet, mots-clés lots
    trouvés dans le détail des lots, acheteur trouvé dans la liste suivie —
    comparaison accent/casse-insensible. Sert à la fois au filtrage
    (`est_pertinent`) et à l'affichage ("pourquoi cet avis est proposé", voir
    app.py) — calculé une fois à la récupération, stocké tel quel (voir
    `pipeline.py::formater_pour_supabase`), pas recalculé à l'affichage
    (les listes de mots-clés changent avec le temps).
    """
    trouves: list[str] = []

    objet_normalise = normaliser_texte(groupe.get("objet") or "")
    trouves.extend(mot for mot in mots_cles if normaliser_texte(mot) in objet_normalise)

    if acheteurs:
        acheteur_normalise = normaliser_texte(groupe.get("acheteur") or "")
        trouves.extend(a for a in acheteurs if normaliser_texte(a) in acheteur_normalise)

    if mots_cles_lots:
        texte_lots_normalise = normaliser_texte(texte_lots(groupe.get("lots") or []))
        trouves.extend(mot for mot in mots_cles_lots if normaliser_texte(mot) in texte_lots_normalise)

    return trouves


def est_pertinent(groupe: dict, mots_cles: list[str], mots_cles_lots: list[str], acheteurs: list[str]) -> bool:
    """Un avis (déjà normalisé/fusionné) est pertinent s'il matche au moins
    un mot-clé/lot/acheteur (voir `trouver_correspondances`). Si les 3
    listes sont vides (aucun filtre configuré), tout est gardé.
    """
    if not mots_cles and not mots_cles_lots and not acheteurs:
        return True
    return bool(trouver_correspondances(groupe, mots_cles, mots_cles_lots, acheteurs))


def normaliser_date(valeur: str) -> str:
    """Ramène une date/datetime hétérogène à un format commun AAAA-MM-JJ."""
    if not valeur:
        return ""
    return valeur[:10]


# =====================================================================
# Bac à sable manuel — `python commun.py` (ou `python -m sources.commun`
# depuis etape7_pipeline_final/) : teste chaque fonction de ce fichier sur
# des données fabriquées, sans réseau ni Supabase.
# =====================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("commun.py — bac à sable manuel")
    print("=" * 70)

    print("\n--- normaliser_texte / normaliser_date : cas limites ---")
    for cas in ["Rénovation ÉCOLE", "  espaces   multiples  ", "", None]:
        print(f"  {cas!r:35} -> {normaliser_texte(cas)!r}")
    for cas in ["2026-08-13T10:00:00+00:00", "2026-08-13", "", None]:
        print(f"  {cas!r:35} -> {normaliser_date(cas)!r}")

    print("\n--- fusionner_doublons : 5 avis fabriqués ---")
    # 0 et 1 : même dossier BOAMP, rectificatif lié explicitement (_annonce_lie).
    # 2 : republication du même marché sur EMP (agrégateur), reformulée -> doit
    #     fusionner avec 0/1 via la similarité de texte (pas de lien explicite).
    # 3 : avis TED, acheteur différent -> reste seul.
    # 4 : avis BOAMP, objet totalement différent -> reste seul.
    avis_fabriques = [
        {"source": "BOAMP", "identifiant": "24-0001", "objet": "Rénovation école primaire Saint-Denis",
         "description": "", "lots": [], "acheteur": "Mairie de Saint-Denis",
         "departement": "974", "date_parution": "2026-08-01", "date_limite_reponse": "2026-09-01",
         "url": "https://boamp/24-0001", "_annonce_lie": []},
        {"source": "BOAMP", "identifiant": "24-0002", "objet": "Rénovation école primaire Saint-Denis (rectificatif)",
         "description": "", "lots": [], "acheteur": "Mairie de Saint-Denis",
         "departement": "974", "date_parution": "2026-08-05", "date_limite_reponse": "2026-09-10",
         "url": "https://boamp/24-0002", "_annonce_lie": ["24-0001"]},
        {"source": "EMP", "identifiant": "emp-9001", "objet": "Travaux de rénovation de l'école primaire de Saint-Denis",
         "description": "", "lots": [], "acheteur": "Mairie de Saint-Denis",
         "departement": "974", "date_parution": "2026-08-02", "date_limite_reponse": "2026-09-01",
         "url": "https://e-marchespublics/emp-9001", "_annonce_lie": []},
        {"source": "TED", "identifiant": "ted-1", "objet": "Fourniture de mobilier scolaire",
         "description": "", "lots": [], "acheteur": "Région Réunion",
         "departement": "974", "date_parution": "2026-08-01", "date_limite_reponse": "2026-08-20",
         "url": "https://ted/1", "_annonce_lie": []},
        {"source": "BOAMP", "identifiant": "24-0099", "objet": "Collecte des déchets ménagers",
         "description": "", "lots": [], "acheteur": "CIREST",
         "departement": "974", "date_parution": "2026-08-01", "date_limite_reponse": "2026-10-01",
         "url": "https://boamp/24-0099", "_annonce_lie": []},
    ]
    liens_precis = {i: a["_annonce_lie"] for i, a in enumerate(avis_fabriques) if a["_annonce_lie"]}
    groupes = fusionner_doublons(avis_fabriques, liens_precis)
    print(f"  {len(avis_fabriques)} avis fabriqués -> {len(groupes)} groupe(s) après fusion")
    for g in groupes:
        print(f"    - {g['objet']!r} (sources={g['source']}, {g['nb_versions']} version(s))")
    # ⚠️ Remarque volontaire : l'avis EMP (reformulé "Travaux de rénovation
    # de l'école primaire de Saint-Denis") ne fusionne PAS avec le dossier
    # BOAMP correspondant malgré le même acheteur — la reformulation fait
    # tomber la similarité de texte sous SEUIL_SIMILARITE_OBJET (0.80).
    # C'est exactement le genre de cas qu'une similarité par EMBEDDING
    # pourrait mieux détecter (le sens est identique, le texte diffère) —
    # voir bac_a_sable_embedding.py à la racine du dossier.

    print("\n--- trouver_correspondances / est_pertinent ---")
    avis_test = avis_fabriques[0]
    cas_filtres = [
        ("aucun filtre configuré -> tout gardé", [], [], []),
        ("mot-clé sur l'objet", ["rénovation"], [], []),
        ("mot-clé sur l'acheteur", [], [], ["Saint-Denis"]),
        ("mot-clé qui ne matche rien", ["plomberie"], [], []),
    ]
    for libelle, mc, mcl, ach in cas_filtres:
        correspondances = trouver_correspondances(avis_test, mc, mcl, ach)
        pertinent = est_pertinent(avis_test, mc, mcl, ach)
        print(f"  {libelle:40} -> pertinent={pertinent}, trouvés={correspondances}")

    print("\nTerminé.")
