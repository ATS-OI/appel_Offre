"""
sources/commun.py — normalisation partagée entre les 4 sources
================================================================================

Chaque source (boamp.py, ted.py, aws_solutions.py, place.py) renvoie une
liste de dicts au MÊME format :

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

Ce fichier regroupe tout ce qui est commun aux 4 sources : normalisation de
texte, fusion des doublons (un même marché republié/rectifié plusieurs fois,
y compris à cheval entre deux sources) et le filtre final mots-clés/lots/
acheteurs suivis.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Seuil de similarité (0-1, via difflib) au-dessus duquel deux objets d'avis
# (même acheteur) sont considérés comme le même dossier républié/corrigé.
SEUIL_SIMILARITE_OBJET = 0.80


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
