"""
recupDataBaseOfficial — bibliothèque de récupération d'appels d'offres publics
================================================================================

Version "boîte noire" du scraper BOAMP + TED (JOUE) : à importer et utiliser
depuis n'importe quel autre script du projet. Ne fait ni print ni export
fichier — une seule fonction d'entrée qui prend en paramètres les mots-clés /
acheteurs suivis / départements / options de recherche, et renvoie une
structure de données Python (liste de dict, directement sérialisable en
JSON) prête à être réutilisée.

Version étape 4 : ajoute un filtre optionnel sur les acheteurs suivis, mis en
OU avec le filtre mots-clés (un avis matche si son objet contient un mot-clé,
OU si son acheteur figure dans la liste `acheteurs`) — voir recap.md.

Usage minimal :

    from recupDataBaseOfficial import recuperer_appels_offres

    resultats = recuperer_appels_offres(
        mots_cles=["rénovation", "école"],
        departements=["974", "976"],
        acheteurs=["SIDR", "SEDRE"],
    )
    # resultats est une list[dict] — voir la docstring de la fonction pour le schéma

Le détail des champs bruts de chaque source (avant normalisation) est décrit
dans recap.md, dans ce même dossier.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from typing import Any

import requests

# =====================================================================
# Constantes partagées
# =====================================================================

# Correspondance département français d'outre-mer -> code NUTS (révision 2021)
# utilisé par l'API TED pour filtrer géographiquement (voir recap.md).
NUTS_MAP: dict[str, str] = {
    "971": "FRY1",  # Guadeloupe
    "972": "FRY2",  # Martinique
    "973": "FRY3",  # Guyane
    "974": "FRY4",  # La Réunion
    "976": "FRY5",  # Mayotte
}
_NUTS_VERS_DEPARTEMENT: dict[str, str] = {v: k for k, v in NUTS_MAP.items()}

BOAMP_BASE_URL = "https://boamp-datadila.opendatasoft.com/api/v2/catalog/datasets/boamp/records"
BOAMP_PAGE_SIZE = 100  # taille max de page acceptée par l'API Opendatasoft

TED_BASE_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "place-of-performance",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "links",
]

# Seuil de similarité (0-1, via difflib) au-dessus duquel deux objets d'avis
# (une fois l'acheteur déjà vérifié identique) sont considérés comme le même
# dossier republié / corrigé — voir `_memes_avis`.
SEUIL_SIMILARITE_OBJET = 0.80


# =====================================================================
# BOAMP — https://boamp-datadila.opendatasoft.com
# =====================================================================

def _construire_where_boamp(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool,
    acheteurs: list[str] | None = None,
) -> str:
    """Construit la clause ODSQL `where=` :
    ((motclés objet OR) OR (acheteurs suivis OR)) AND (départements OR) AND (date limite).

    NOTE : le champ `code_departement_prestation` (lieu d'exécution) existe dans le
    schéma BOAMP mais n'est en pratique quasiment jamais renseigné sur les avis
    récents (vérifié en direct : 0 résultat sur les avis 2026, alors que
    `code_departement` en compte des milliers). On utilise donc `code_departement`,
    un champ liste qui contient le(s) département(s) concerné(s) par l'avis —
    voir recap.md pour le détail.
    """
    clause_motcles = " or ".join(f'objet like "%{mot}%"' for mot in mots_cles)
    clause_recherche = f"({clause_motcles})"

    if acheteurs:
        clause_acheteurs = " or ".join(f'nomacheteur like "%{a}%"' for a in acheteurs)
        clause_recherche = f"(({clause_motcles}) or ({clause_acheteurs}))"

    clause_departements = " or ".join(f'code_departement="{dep}"' for dep in departements)

    clauses = [clause_recherche, f"({clause_departements})"]

    if seulement_ouverts:
        aujourdhui = date.today().isoformat()
        clauses.append(f"datelimitereponse > date'{aujourdhui}'")

    return " and ".join(clauses)


def interroger_boamp(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool,
    limit: int,
    verbeux: bool = True,
    acheteurs: list[str] | None = None,
) -> list[dict]:
    """Interroge l'API BOAMP (GET, pagination par offset) et retourne les enregistrements bruts."""
    where = _construire_where_boamp(mots_cles, departements, seulement_ouverts, acheteurs)
    if verbeux:
        print(f"[BOAMP] where = {where}")

    resultats: list[dict] = []
    offset = 0

    while len(resultats) < limit:
        params = {
            "where": where,
            "limit": min(BOAMP_PAGE_SIZE, limit - len(resultats)),
            "offset": offset,
            "order_by": "datelimitereponse asc",
        }
        try:
            reponse = requests.get(BOAMP_BASE_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            if verbeux:
                print(f"[BOAMP] Erreur réseau : {exc}")
            break

        if reponse.status_code != 200:
            if verbeux:
                print(f"[BOAMP] Erreur HTTP {reponse.status_code} : {reponse.text[:500]}")
            break

        donnees = reponse.json()
        page = donnees.get("records", [])
        if not page:
            break

        resultats.extend(rec["record"]["fields"] for rec in page)
        offset += len(page)

        if len(page) < params["limit"]:
            break  # dernière page atteinte

    if verbeux:
        print(f"[BOAMP] {len(resultats)} résultat(s) brut(s) récupéré(s).")
    return resultats


# =====================================================================
# TED / JOUE — https://api.ted.europa.eu
# =====================================================================

def _construire_requete_ted(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool,
    acheteurs: list[str] | None = None,
) -> str:
    """Construit la requête "expert query" TED :
    ((motclés OR) OR (acheteurs suivis OR)) AND (NUTS OR) AND (deadline).

    TED n'a pas de champ dédié filtrable pour le nom de l'acheteur en dehors
    du texte intégral (voir recap.md) : `FT~` couvre déjà tout le texte de la
    notice, y compris le nom de l'acheteur — vérifié en pratique.
    """
    clause_motcles = " OR ".join(f'FT~"{mot}"' for mot in mots_cles)
    clause_recherche = f"({clause_motcles})"

    if acheteurs:
        clause_acheteurs = " OR ".join(f'FT~"{a}"' for a in acheteurs)
        clause_recherche = f"(({clause_motcles}) OR ({clause_acheteurs}))"

    codes_nuts = [NUTS_MAP[dep] for dep in departements if dep in NUTS_MAP]
    if not codes_nuts:
        raise ValueError("Aucun code NUTS connu pour les départements demandés — complétez NUTS_MAP.")
    clause_nuts = " OR ".join(f"place-of-performance={code}" for code in codes_nuts)

    clauses = [clause_recherche, f"({clause_nuts})"]

    if seulement_ouverts:
        clauses.append("deadline-receipt-tender-date-lot>=today()")

    return " AND ".join(clauses)


def interroger_ted(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool,
    limit: int,
    verbeux: bool = True,
    acheteurs: list[str] | None = None,
) -> list[dict]:
    """Interroge l'API TED Search v3 (POST JSON) et retourne les notices brutes."""
    query = _construire_requete_ted(mots_cles, departements, seulement_ouverts, acheteurs)
    if verbeux:
        print(f"[TED] query = {query}")

    corps = {
        "query": query,
        "fields": TED_FIELDS,
        "limit": min(limit, 250),  # 250 = maximum accepté par page par l'API TED
        "scope": "ACTIVE",
        "paginationMode": "ITERATION",
    }

    try:
        reponse = requests.post(TED_BASE_URL, json=corps, timeout=30)
    except requests.RequestException as exc:
        if verbeux:
            print(f"[TED] Erreur réseau : {exc}")
        return []

    if reponse.status_code != 200:
        # On affiche la réponse brute : utile pour diagnostiquer un nom de
        # champ TED invalide si l'API évolue (voir recap.md).
        if verbeux:
            print(f"[TED] Erreur HTTP {reponse.status_code} : {reponse.text[:1000]}")
        return []

    donnees = reponse.json()
    resultats = donnees.get("notices", [])[:limit]
    if verbeux:
        print(f"[TED] {len(resultats)} résultat(s) brut(s) récupéré(s) (sur {donnees.get('totalNoticeCount', '?')} au total).")
    return resultats


# =====================================================================
# Normalisation — mise au même format quel que soit la source
# =====================================================================

def _premiere_valeur(champ_multilingue: dict | None, langues_preferees: tuple[str, ...] = ("fra", "eng")) -> str:
    """Extrait la première valeur d'un champ TED multilingue (dict langue -> liste/valeur)."""
    if not champ_multilingue:
        return ""
    for langue in langues_preferees:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            return valeur[0] if isinstance(valeur, list) else str(valeur)
    # à défaut, on prend la première langue disponible
    valeur = next(iter(champ_multilingue.values()), "")
    return valeur[0] if isinstance(valeur, list) else str(valeur)


def _normaliser_date(valeur: str) -> str:
    """Ramène une date/datetime hétérogène (BOAMP `2026-08-17T09:00:00+00:00`,
    TED `2026-08-17+03:00`) à un format commun `AAAA-MM-JJ`, trié/comparable
    directement en texte. L'heure précise n'est pas fiable côté TED (souvent
    absente), on l'ignore donc des deux côtés pour garder un format uniforme.
    """
    if not valeur:
        return ""
    return valeur[:10]


def _normaliser_departement_boamp(code_departement: Any) -> str:
    """Uniformise le champ département BOAMP (déjà des codes numériques) en
    chaîne triée séparée par virgules, ex. `"974, 976"`.
    """
    if isinstance(code_departement, list):
        codes = code_departement
    elif code_departement:
        codes = [code_departement]
    else:
        codes = []
    return ", ".join(sorted({str(c) for c in codes if c}))


def _normaliser_departement_ted(places_of_performance: list[str]) -> str:
    """Traduit les codes NUTS renvoyés par TED (ex. `FRY40`, `FRA`) vers les
    codes de département français reconnus (ex. `974`), dans le même format
    que le champ BOAMP. Les codes non reconnus (pays générique `FRA`, NUTS
    métropolitains d'un même marché national...) sont ignorés à l'affichage.
    """
    codes = set()
    for place in places_of_performance or []:
        prefixe = place[:4]  # ex. "FRY40" -> "FRY4"
        if prefixe in _NUTS_VERS_DEPARTEMENT:
            codes.add(_NUTS_VERS_DEPARTEMENT[prefixe])
    return ", ".join(sorted(codes))


def normaliser_boamp(records: list[dict]) -> list[dict]:
    resultats = []
    for f in records:
        resultats.append({
            "source": "BOAMP",
            "identifiant": f.get("idweb") or "",
            "objet": f.get("objet") or "",
            "acheteur": f.get("nomacheteur") or "",
            "departement": _normaliser_departement_boamp(f.get("code_departement")),
            "date_parution": _normaliser_date(f.get("dateparution") or ""),
            "date_limite_reponse": _normaliser_date(f.get("datelimitereponse") or ""),
            "url": f.get("url_avis") or "",
            # champs internes utilisés uniquement pour le repérage précis des
            # rectificatifs BOAMP (retirés avant le retour final) :
            "_annonce_lie": f.get("annonce_lie") or [],
        })
    return resultats


def normaliser_ted(notices: list[dict]) -> list[dict]:
    resultats = []
    for n in notices:
        links = n.get("links") or {}
        html_links = links.get("html") or {}
        url = html_links.get("FRA") or html_links.get("ENG") or ""

        deadlines = n.get("deadline-receipt-tender-date-lot") or []
        date_limite = deadlines[0] if deadlines else ""

        dates_parution = n.get("publication-date")
        if isinstance(dates_parution, list):
            date_parution = dates_parution[0] if dates_parution else ""
        else:
            date_parution = dates_parution or ""

        resultats.append({
            "source": "TED",
            "identifiant": n.get("publication-number") or "",
            "objet": _premiere_valeur(n.get("notice-title")),
            "acheteur": _premiere_valeur(n.get("buyer-name")),
            "departement": _normaliser_departement_ted(n.get("place-of-performance") or []),
            "date_parution": _normaliser_date(date_parution),
            "date_limite_reponse": _normaliser_date(date_limite),
            "url": url,
            "_annonce_lie": [],
        })
    return resultats


# =====================================================================
# Fusion des doublons (avis édités/rectifiés plusieurs fois, y compris
# à cheval entre BOAMP et TED)
# =====================================================================

def _normaliser_texte(texte: str) -> str:
    """Normalise un texte pour comparaison approximative (accents, casse, ponctuation)."""
    texte = texte or ""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()
    return texte


def _memes_avis(a: dict, b: dict) -> bool:
    """Heuristique de rapprochement entre deux avis (même source ou non).

    Deux avis sont considérés comme le même dossier (rectificatif, relance,
    republication identique dans BOAMP et TED...) si l'acheteur normalisé est
    identique ET si l'un des objets contient l'autre, ou si leur similarité
    textuelle dépasse `SEUIL_SIMILARITE_OBJET`. Ce deuxième cas couvre
    notamment TED, qui préfixe systématiquement le titre par
    "France – <catégorie CPV> – " avant de reprendre le texte de l'avis.

    ⚠️ Il n'existe pas d'identifiant officiel commun entre BOAMP et TED : ce
    rapprochement est donc approximatif (voir recap.md) — à vérifier via les
    URLs fournies en cas de doute.
    """
    acheteur_a = _normaliser_texte(a["acheteur"])
    acheteur_b = _normaliser_texte(b["acheteur"])
    if not acheteur_a or acheteur_a != acheteur_b:
        return False

    objet_a = _normaliser_texte(a["objet"])
    objet_b = _normaliser_texte(b["objet"])
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


def fusionner_doublons(resultats: list[dict]) -> list[dict]:
    """Regroupe tous les avis d'un même dossier (rectificatifs BOAMP,
    republications TED, et correspondances BOAMP<->TED) en une seule ligne
    par dossier.

    La ligne conservée reprend l'objet/acheteur/date de l'avis dont la date
    limite de réponse est la plus tardive du groupe (c'est la seule échéance
    encore pertinente), mais liste tous les identifiants et URLs du groupe.
    """
    n = len(resultats)
    if n == 0:
        return []

    uf = _UnionFind(n)
    index_par_id = {r["identifiant"]: i for i, r in enumerate(resultats) if r["identifiant"]}

    # 1) liens précis BOAMP : le champ `annonce_lie` référence explicitement
    #    l'idweb de l'avis que le présent avis modifie.
    for i, r in enumerate(resultats):
        for parent_id in r.get("_annonce_lie") or []:
            j = index_par_id.get(parent_id)
            if j is not None:
                uf.union(i, j)

    # 2) liens heuristiques (acheteur + objet), toutes sources confondues :
    #    couvre les republications TED et les correspondances BOAMP<->TED.
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
        # on garde comme représentant l'avis dont la date limite est la plus
        # tardive (les versions antérieures sont caduques) ; à date limite
        # égale ou manquante, on prend la parution la plus récente.
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
        urls = list(dict.fromkeys(m["url"] for m in membres_tries if m["url"]))  # dédoublonné, ordre conservé

        fusionnes.append({
            "source": sources,
            "identifiants": [m["identifiant"] for m in membres_tries],
            "objet": representant["objet"],
            "acheteur": representant["acheteur"],
            "departement": ", ".join(departements),
            "date_parution": representant["date_parution"],
            "date_limite_reponse": representant["date_limite_reponse"],
            "urls": urls,
            "nb_versions": len(membres),
        })

    # tri final par date limite de réponse croissante (les plus urgents d'abord)
    fusionnes.sort(key=lambda r: r["date_limite_reponse"] or "9999-99-99")
    return fusionnes


def _sans_fusion(resultats: list[dict]) -> list[dict]:
    """Convertit les résultats normalisés (source/identifiant/url au singulier)
    vers le même schéma de sortie que `fusionner_doublons`, mais sans regrouper
    (utilisé quand `dedupliquer=False`).
    """
    sortie = []
    for r in resultats:
        sortie.append({
            "source": [r["source"]],
            "identifiants": [r["identifiant"]] if r["identifiant"] else [],
            "objet": r["objet"],
            "acheteur": r["acheteur"],
            "departement": r["departement"],
            "date_parution": r["date_parution"],
            "date_limite_reponse": r["date_limite_reponse"],
            "urls": [r["url"]] if r["url"] else [],
            "nb_versions": 1,
        })
    sortie.sort(key=lambda r: r["date_limite_reponse"] or "9999-99-99")
    return sortie


# =====================================================================
# Fonction d'entrée "boîte noire"
# =====================================================================

def recuperer_appels_offres(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool = True,
    sources: dict[str, bool] | None = None,
    limit_par_source: int = 100,
    dedupliquer: bool = True,
    verbeux: bool = True,
    acheteurs: list[str] | None = None,
) -> list[dict]:
    """Point d'entrée unique de la bibliothèque : interroge BOAMP et/ou TED et
    renvoie une liste de dicts JSON-sérialisable, prête à être réutilisée par
    n'importe quel autre script du projet.

    Paramètres
    ----------
    mots_cles : liste de mots-clés combinés en OU logique (recherche sur l'objet/titre).
    departements : liste de départements combinés en OU logique (ex. ["974", "976"]).
    seulement_ouverts : si True, ne garde que les avis dont la date limite de
        réponse n'est pas encore passée.
    sources : quelles bases interroger, ex. {"boamp": True, "ted": True}.
        Par défaut, les deux sont actives.
    limit_par_source : nombre max de résultats bruts récupérés par source.
    dedupliquer : si True (par défaut), fusionne en une seule ligne tous les
        avis d'un même dossier (rectificatifs BOAMP, republications TED,
        correspondances BOAMP<->TED) — voir `fusionner_doublons`.
    verbeux : si True, affiche la progression sur la sortie standard.
    acheteurs : liste optionnelle d'acheteurs suivis, combinés en OU avec
        `mots_cles` (un avis matche si son objet contient un mot-clé, OU si
        son acheteur contient l'un de ces noms) — voir recap.md.

    Retour
    ------
    list[dict], triée par date limite de réponse croissante, avec les clés :
        source (liste, ex. ["BOAMP", "TED"]),
        identifiants (liste de tous les identifiants du groupe : idweb BOAMP
            et/ou publication-number TED),
        objet, acheteur (de la version retenue = date limite la plus tardive),
        departement (codes normalisés, ex. "974, 976"),
        date_parution, date_limite_reponse (format uniforme AAAA-MM-JJ),
        urls (liste de tous les liens du groupe),
        nb_versions (nombre d'avis fusionnés dans cette ligne).
    """
    sources = sources if sources is not None else {"boamp": True, "ted": True}

    tous_resultats: list[dict] = []

    if sources.get("boamp"):
        records_boamp = interroger_boamp(mots_cles, departements, seulement_ouverts, limit_par_source, verbeux, acheteurs)
        tous_resultats.extend(normaliser_boamp(records_boamp))

    if sources.get("ted"):
        notices_ted = interroger_ted(mots_cles, departements, seulement_ouverts, limit_par_source, verbeux, acheteurs)
        tous_resultats.extend(normaliser_ted(notices_ted))

    if dedupliquer:
        return fusionner_doublons(tous_resultats)
    return _sans_fusion(tous_resultats)


if __name__ == "__main__":
    # Petit auto-test manuel : `python recupDataBaseOfficial.py`
    exemple = recuperer_appels_offres(
        mots_cles=["rénovation", "école"],
        departements=["974", "976"],
        acheteurs=["SIDR", "SEDRE"],
        limit_par_source=20,
    )
    print(f"\n{len(exemple)} résultat(s) — exemple de structure renvoyée :")
    if exemple:
        import json
        print(json.dumps(exemple[0], ensure_ascii=False, indent=2))
