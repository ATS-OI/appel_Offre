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

import json
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
    "description-lot",  # description longue de la mission (BT-24) — voir recap.md
    "title-lot",        # titre de chaque lot (BT-21) — voir _extraire_lots_ted
    "identifier-lot",   # identifiant de chaque lot (BT-137) — aligné par index avec title-lot/description-lot
]

# Seuil de similarité (0-1, via difflib) au-dessus duquel deux objets d'avis
# (une fois l'acheteur déjà vérifié identique) sont considérés comme le même
# dossier republié / corrigé — voir `_memes_avis`.
SEUIL_SIMILARITE_OBJET = 0.80


# =====================================================================
# BOAMP — https://boamp-datadila.opendatasoft.com
# =====================================================================

def _construire_where_boamp(
    departements: list[str],
    seulement_ouverts: bool,
) -> str:
    """Construit la clause ODSQL `where=` : (départements OR) AND (date limite).

    Ne filtre plus par mots-clés/acheteurs côté serveur (voir
    `recuperer_appels_offres`/`_est_pertinent`) : le filtre BOAMP `objet
    like` ne porte que sur le titre de haut niveau de l'avis, qui ne contient
    PAS le détail des lots (`donnees`/eForms) sur les marchés multi-lots —
    vérifié en direct (ex. avis BOAMP 24-24231, objet générique mais 10 lots
    aux titres très différents). Filtrer mots-clés/lots/acheteurs après coup,
    côté client, sur les données déjà récupérées, évite de rater un avis
    dont seul un lot correspond — voir recap.md "Filtrage côté client".

    NOTE : le champ `code_departement_prestation` (lieu d'exécution) existe dans le
    schéma BOAMP mais n'est en pratique quasiment jamais renseigné sur les avis
    récents (vérifié en direct : 0 résultat sur les avis 2026, alors que
    `code_departement` en compte des milliers). On utilise donc `code_departement`,
    un champ liste qui contient le(s) département(s) concerné(s) par l'avis —
    voir recap.md pour le détail.
    """
    clause_departements = " or ".join(f'code_departement="{dep}"' for dep in departements)
    clauses = [f"({clause_departements})"]

    if seulement_ouverts:
        aujourdhui = date.today().isoformat()
        clauses.append(f"datelimitereponse > date'{aujourdhui}'")

    return " and ".join(clauses)


def interroger_boamp(
    departements: list[str],
    seulement_ouverts: bool,
    limit: int,
    verbeux: bool = True,
) -> list[dict]:
    """Interroge l'API BOAMP (GET, pagination par offset) et retourne les enregistrements bruts.

    Pas de filtre mots-clés/acheteurs ici (voir `_construire_where_boamp`) —
    uniquement département + date limite.
    """
    where = _construire_where_boamp(departements, seulement_ouverts)
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
    departements: list[str],
    seulement_ouverts: bool,
) -> str:
    """Construit la requête "expert query" TED : (NUTS OR) AND (deadline).

    Ne filtre plus par mots-clés/acheteurs côté serveur (voir
    `recuperer_appels_offres`/`_est_pertinent`, même raison que côté BOAMP :
    on veut pouvoir matcher sur le texte des lots après coup, pas seulement
    sur le texte intégral de la notice au moment de la requête).
    """
    codes_nuts = [NUTS_MAP[dep] for dep in departements if dep in NUTS_MAP]
    if not codes_nuts:
        raise ValueError("Aucun code NUTS connu pour les départements demandés — complétez NUTS_MAP.")
    clause_nuts = " OR ".join(f"place-of-performance={code}" for code in codes_nuts)

    clauses = [f"({clause_nuts})"]

    if seulement_ouverts:
        clauses.append("deadline-receipt-tender-date-lot>=today()")

    return " AND ".join(clauses)


def interroger_ted(
    departements: list[str],
    seulement_ouverts: bool,
    limit: int,
    verbeux: bool = True,
) -> list[dict]:
    """Interroge l'API TED Search v3 (POST JSON) et retourne les notices brutes.

    Pas de filtre mots-clés/acheteurs ici (voir `_construire_requete_ted`) —
    uniquement NUTS + date limite.
    """
    query = _construire_requete_ted(departements, seulement_ouverts)
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


def _texte_multilingue_complet(champ_multilingue: dict | None, langues_preferees: tuple[str, ...] = ("fra", "eng")) -> str:
    """Comme `_premiere_valeur`, mais concatène TOUTES les valeurs d'une
    liste (ex. `description-lot` : une description par lot) au lieu de ne
    garder que la première — sinon on perdrait la description des lots 2, 3...
    """
    if not champ_multilingue:
        return ""
    for langue in langues_preferees:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            if isinstance(valeur, list):
                return " | ".join(str(v) for v in valeur if v)
            return str(valeur)
    valeur = next(iter(champ_multilingue.values()), "")
    if isinstance(valeur, list):
        return " | ".join(str(v) for v in valeur if v)
    return str(valeur)


def _liste_multilingue(champ_multilingue: dict | None, langues_preferees: tuple[str, ...] = ("fra", "eng")) -> list[str]:
    """Comme `_texte_multilingue_complet`, mais renvoie la LISTE de valeurs
    (une par lot, ex. `title-lot`) au lieu de les joindre en un seul texte —
    utilisé pour aligner par index avec `identifier-lot`/`description-lot`
    dans `_extraire_lots_ted`.
    """
    if not champ_multilingue:
        return []
    for langue in langues_preferees:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            return valeur if isinstance(valeur, list) else [str(valeur)]
    valeur = next(iter(champ_multilingue.values()), [])
    return valeur if isinstance(valeur, list) else [str(valeur)]


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


def _extraire_description_boamp(donnees_json: str | None) -> str:
    """Best-effort : extrait la description longue de la mission depuis le
    blob JSON brut du champ `donnees` (structure eForms — `EFORMS.<type
    d'avis>.cac:ProcurementProject.cbc:Description.#text`). Uniquement
    présent sur les avis BOAMP récents (format eForms) ; renvoie "" pour les
    avis plus anciens (autre schéma) ou en cas de structure inattendue —
    dans ce cas l'objet reste le seul texte disponible, comme avant.
    """
    if not donnees_json:
        return ""
    try:
        donnees = json.loads(donnees_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    def chercher(obj: object) -> str:
        if isinstance(obj, dict):
            projet = obj.get("cac:ProcurementProject")
            if isinstance(projet, dict):
                description = projet.get("cbc:Description")
                if isinstance(description, dict) and description.get("#text"):
                    return str(description["#text"])
            for valeur in obj.values():
                trouve = chercher(valeur)
                if trouve:
                    return trouve
        elif isinstance(obj, list):
            for item in obj:
                trouve = chercher(item)
                if trouve:
                    return trouve
        return ""

    return chercher(donnees)


def _extraire_lots_boamp(donnees_json: str | None) -> list[dict]:
    """Best-effort : extrait le détail des lots depuis le blob JSON brut du
    champ `donnees` (structure eForms — `cac:ProcurementProjectLot`, un dict
    si le marché n'a qu'un seul lot, une liste sinon — les deux formes sont
    apparues en pratique sur des avis réels). Pour chaque lot, renvoie son
    identifiant (`cbc:ID`), son titre (`cac:ProcurementProject.cbc:Name`) et
    sa description (`cbc:Description`) — c'est ce texte, souvent absent du
    champ `objet` de haut niveau sur les marchés multi-lots, qui alimente le
    filtre `mots_cles_lots` et le scoring (voir recap.md).
    """
    if not donnees_json:
        return []
    try:
        donnees = json.loads(donnees_json)
    except (json.JSONDecodeError, TypeError):
        return []

    def chercher(obj: object) -> object | None:
        if isinstance(obj, dict):
            if "cac:ProcurementProjectLot" in obj:
                return obj["cac:ProcurementProjectLot"]
            for valeur in obj.values():
                trouve = chercher(valeur)
                if trouve is not None:
                    return trouve
        elif isinstance(obj, list):
            for item in obj:
                trouve = chercher(item)
                if trouve is not None:
                    return trouve
        return None

    brut = chercher(donnees)
    if brut is None:
        return []
    lots_bruts = brut if isinstance(brut, list) else [brut]

    lots = []
    for lot in lots_bruts:
        if not isinstance(lot, dict):
            continue
        identifiant = lot.get("cbc:ID")
        identifiant = identifiant.get("#text", "") if isinstance(identifiant, dict) else (identifiant or "")
        projet = lot.get("cac:ProcurementProject") or {}
        titre = projet.get("cbc:Name")
        titre = titre.get("#text", "") if isinstance(titre, dict) else (titre or "")
        description = projet.get("cbc:Description")
        description = description.get("#text", "") if isinstance(description, dict) else (description or "")
        if identifiant or titre or description:
            lots.append({"identifiant": str(identifiant), "titre": str(titre), "description": str(description)})
    return lots


def _extraire_lots_ted(notice: dict) -> list[dict]:
    """Reconstruit la liste des lots d'une notice TED à partir des 3 champs
    parallèles `identifier-lot` (liste plate), `title-lot`/`description-lot`
    (multilingues) — alignés par index (vérifié en direct : les 3 listes ont
    toujours la même longueur sur les notices testées).
    """
    identifiants = notice.get("identifier-lot") or []
    titres = _liste_multilingue(notice.get("title-lot"))
    descriptions = _liste_multilingue(notice.get("description-lot"))

    nb_lots = max(len(identifiants), len(titres), len(descriptions))
    lots = []
    for i in range(nb_lots):
        identifiant = identifiants[i] if i < len(identifiants) else ""
        titre = titres[i] if i < len(titres) else ""
        description = descriptions[i] if i < len(descriptions) else ""
        if identifiant or titre or description:
            lots.append({"identifiant": str(identifiant), "titre": str(titre), "description": str(description)})
    return lots


def normaliser_boamp(records: list[dict]) -> list[dict]:
    resultats = []
    for f in records:
        resultats.append({
            "source": "BOAMP",
            "identifiant": f.get("idweb") or "",
            "objet": f.get("objet") or "",
            "description": _extraire_description_boamp(f.get("donnees")),
            "lots": _extraire_lots_boamp(f.get("donnees")),
            "acheteur": f.get("nomacheteur") or "",
            "departement": _normaliser_departement_boamp(f.get("code_departement")),
            "date_parution": _normaliser_date(f.get("dateparution") or ""),
            "date_limite_reponse": _normaliser_date(f.get("datelimitereponse") or ""),
            "url": f.get("url_avis") or "",
            # Champs BOAMP officiels (nomenclature de marché), utilisés comme
            # features catégorielles par le modèle B (étape 5, A/B test) —
            # voir features.py. Pas d'équivalent direct côté TED.
            "type_procedure": f.get("type_procedure") or "",
            "nature_libelle": f.get("nature_libelle") or "",
            "descripteur_libelle": f.get("descripteur_libelle") or [],
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
            "description": _texte_multilingue_complet(n.get("description-lot")),
            "lots": _extraire_lots_ted(n),
            "acheteur": _premiere_valeur(n.get("buyer-name")),
            "departement": _normaliser_departement_ted(n.get("place-of-performance") or []),
            "date_parution": _normaliser_date(date_parution),
            "date_limite_reponse": _normaliser_date(date_limite),
            "url": url,
            # Pas d'équivalent BOAMP collecté côté TED actuellement (voir
            # recap.md étape 5, A/B test) — champs vides pour ces avis.
            "type_procedure": "",
            "nature_libelle": "",
            "descripteur_libelle": [],
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
            # la description n'est pas toujours présente sur chaque version
            # (voir _extraire_description_boamp) -> on prend celle du
            # représentant, ou à défaut la première trouvée dans le groupe.
            "description": representant.get("description") or next(
                (m.get("description") for m in membres_tries if m.get("description")), ""
            ),
            # même compromis que `description` : lots du représentant du
            # groupe (version la plus à jour), sinon le premier membre qui
            # en a (une version antérieure a pu porter le détail des lots
            # que le rectificatif le plus récent n'a pas repris).
            "lots": representant.get("lots") or next(
                (m.get("lots") for m in membres_tries if m.get("lots")), []
            ),
            "acheteur": representant["acheteur"],
            "departement": ", ".join(departements),
            "date_parution": representant["date_parution"],
            "date_limite_reponse": representant["date_limite_reponse"],
            "urls": urls,
            "nb_versions": len(membres),
            "type_procedure": representant.get("type_procedure") or "",
            "nature_libelle": representant.get("nature_libelle") or "",
            "descripteur_libelle": representant.get("descripteur_libelle") or [],
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
            "description": r.get("description") or "",
            "lots": r.get("lots") or [],
            "acheteur": r["acheteur"],
            "departement": r["departement"],
            "date_parution": r["date_parution"],
            "date_limite_reponse": r["date_limite_reponse"],
            "urls": [r["url"]] if r["url"] else [],
            "nb_versions": 1,
            "type_procedure": r.get("type_procedure") or "",
            "nature_libelle": r.get("nature_libelle") or "",
            "descripteur_libelle": r.get("descripteur_libelle") or [],
        })
    sortie.sort(key=lambda r: r["date_limite_reponse"] or "9999-99-99")
    return sortie


# =====================================================================
# Filtrage côté client (mots-clés / lots / acheteurs)
# =====================================================================

def _texte_lots(lots: list[dict]) -> str:
    """Concatène titres + descriptions de tous les lots d'un avis en un seul
    texte, pour la recherche de mots-clés (`_est_pertinent`) et le scoring.
    """
    morceaux = []
    for lot in lots or []:
        if lot.get("titre"):
            morceaux.append(lot["titre"])
        if lot.get("description"):
            morceaux.append(lot["description"])
    return " ".join(morceaux)


def _est_pertinent(
    groupe: dict,
    mots_cles: list[str],
    mots_cles_lots: list[str],
    acheteurs: list[str],
) -> bool:
    """Un avis (déjà normalisé/fusionné) est pertinent si son objet matche un
    mot-clé, OU si un de ses lots matche un mot-clé "lots", OU si son
    acheteur figure dans la liste suivie — comparaison accent/casse-
    insensible (`_normaliser_texte`, déjà utilisé pour le dédoublonnage).

    Remplace l'ancien filtrage côté serveur (BOAMP `objet like`, TED `FT~`) :
    ceux-ci ne portaient que sur le titre/texte intégral de l'avis, jamais
    spécifiquement sur le détail des lots (voir `_construire_where_boamp`,
    recap.md "Filtrage côté client") — un mot-clé présent uniquement dans un
    lot ne matchait donc jamais, et l'avis était raté silencieusement.

    Si les 3 listes sont vides (aucun filtre configuré), tout est gardé
    (même comportement que `aws.py`, étape 6).
    """
    if not mots_cles and not mots_cles_lots and not acheteurs:
        return True

    objet_normalise = _normaliser_texte(groupe.get("objet") or "")
    if any(_normaliser_texte(mot) in objet_normalise for mot in mots_cles):
        return True

    if acheteurs:
        acheteur_normalise = _normaliser_texte(groupe.get("acheteur") or "")
        if any(_normaliser_texte(a) in acheteur_normalise for a in acheteurs):
            return True

    if mots_cles_lots:
        texte_lots_normalise = _normaliser_texte(_texte_lots(groupe.get("lots") or []))
        if any(_normaliser_texte(mot) in texte_lots_normalise for mot in mots_cles_lots):
            return True

    return False


# =====================================================================
# Fonction d'entrée "boîte noire"
# =====================================================================

def recuperer_appels_offres(
    mots_cles: list[str],
    departements: list[str],
    seulement_ouverts: bool = True,
    sources: dict[str, bool] | None = None,
    limit_par_source: int = 250,
    dedupliquer: bool = True,
    verbeux: bool = True,
    acheteurs: list[str] | None = None,
    mots_cles_lots: list[str] | None = None,
) -> list[dict]:
    """Point d'entrée unique de la bibliothèque : interroge BOAMP et/ou TED et
    renvoie une liste de dicts JSON-sérialisable, prête à être réutilisée par
    n'importe quel autre script du projet.

    BOAMP/TED ne sont interrogés que par département/date (voir
    `_construire_where_boamp`/`_construire_requete_ted`) — le filtrage par
    mots-clés/lots/acheteurs est fait ENSUITE, côté client, sur les
    résultats déjà récupérés et fusionnés (voir `_est_pertinent`). Ce choix
    est nécessaire pour pouvoir matcher sur le texte des LOTS (voir
    `mots_cles_lots` ci-dessous), invisible pour un filtre serveur portant
    seulement sur l'objet/le texte intégral de l'avis. Volumes vérifiés en
    direct sans aucune restriction mot-clé (974+976, avis ouverts) : ~180
    BOAMP + ~125 TED — largement gérable en un seul aller-retour paginé.

    Paramètres
    ----------
    mots_cles : liste de mots-clés combinés en OU logique (recherche sur l'objet/titre).
    departements : liste de départements combinés en OU logique (ex. ["974", "976"]).
    seulement_ouverts : si True, ne garde que les avis dont la date limite de
        réponse n'est pas encore passée.
    sources : quelles bases interroger, ex. {"boamp": True, "ted": True}.
        Par défaut, les deux sont actives.
    limit_par_source : nombre max de résultats bruts récupérés par source
        (avant filtrage — voir remarque ci-dessus sur les volumes réels).
    dedupliquer : si True (par défaut), fusionne en une seule ligne tous les
        avis d'un même dossier (rectificatifs BOAMP, republications TED,
        correspondances BOAMP<->TED) — voir `fusionner_doublons`.
    verbeux : si True, affiche la progression sur la sortie standard.
    acheteurs : liste optionnelle d'acheteurs suivis, combinés en OU avec
        `mots_cles`/`mots_cles_lots` (un avis matche si son objet contient un
        mot-clé, OU si un de ses lots contient un mot-clé "lots", OU si son
        acheteur contient l'un de ces noms) — voir recap.md.
    mots_cles_lots : liste optionnelle de mots-clés recherchés spécifiquement
        dans le titre/la description de chaque LOT de l'avis (pas seulement
        son objet global) — voir `_est_pertinent`, recap.md "Filtrage côté
        client".

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
        nb_versions (nombre d'avis fusionnés dans cette ligne),
        lots (liste de {"identifiant", "titre", "description"}, éventuellement vide).
    """
    sources = sources if sources is not None else {"boamp": True, "ted": True}
    mots_cles_lots = mots_cles_lots or []
    acheteurs = acheteurs or []

    tous_resultats: list[dict] = []

    if sources.get("boamp"):
        records_boamp = interroger_boamp(departements, seulement_ouverts, limit_par_source, verbeux)
        tous_resultats.extend(normaliser_boamp(records_boamp))

    if sources.get("ted"):
        notices_ted = interroger_ted(departements, seulement_ouverts, limit_par_source, verbeux)
        tous_resultats.extend(normaliser_ted(notices_ted))

    fusionnes = fusionner_doublons(tous_resultats) if dedupliquer else _sans_fusion(tous_resultats)

    pertinents = [g for g in fusionnes if _est_pertinent(g, mots_cles, mots_cles_lots, acheteurs)]
    if verbeux:
        print(f"[Filtrage] {len(pertinents)}/{len(fusionnes)} avis pertinent(s) "
              f"(mots-clés objet/lots/acheteurs suivis).")
    return pertinents


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
