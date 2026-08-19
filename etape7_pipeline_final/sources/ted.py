"""
sources/ted.py — TED/JOUE (https://api.ted.europa.eu)
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py). Même principe que boamp.py :
interrogation par zone géographique (codes NUTS) + date limite uniquement,
filtre mots-clés/lots fait après coup (sources/__init__.py).
"""

from __future__ import annotations

import requests

try:
    from .commun import normaliser_date
except ImportError:
    # Lancé directement (`python sources/ted.py` ou bouton "Run" de l'IDE)
    # plutôt qu'en module (`python -m sources.ted`) — voir boamp.py pour le
    # détail de ce repli.
    from commun import normaliser_date

BASE_URL = "https://api.ted.europa.eu/v3/notices/search"

# Correspondance département français d'outre-mer -> code NUTS (révision 2021).
NUTS_MAP: dict[str, str] = {
    "971": "FRY1",  # Guadeloupe
    "972": "FRY2",  # Martinique
    "973": "FRY3",  # Guyane
    "974": "FRY4",  # La Réunion
    "976": "FRY5",  # Mayotte
}
_NUTS_VERS_DEPARTEMENT: dict[str, str] = {v: k for k, v in NUTS_MAP.items()}

FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "place-of-performance",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "links",
    "description-lot",  # description longue de la mission (BT-24)
    "title-lot",        # titre de chaque lot (BT-21)
    "identifier-lot",   # identifiant de chaque lot (BT-137), aligné par index
]


def _premiere_valeur(champ_multilingue: dict | None, langues: tuple[str, ...] = ("fra", "eng")) -> str:
    if not champ_multilingue:
        return ""
    for langue in langues:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            return valeur[0] if isinstance(valeur, list) else str(valeur)
    valeur = next(iter(champ_multilingue.values()), "")
    return valeur[0] if isinstance(valeur, list) else str(valeur)


def _texte_complet(champ_multilingue: dict | None, langues: tuple[str, ...] = ("fra", "eng")) -> str:
    """Comme `_premiere_valeur`, mais concatène TOUTES les valeurs d'une liste
    (ex. une description par lot) au lieu de ne garder que la première."""
    if not champ_multilingue:
        return ""
    for langue in langues:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            return " | ".join(str(v) for v in valeur if v) if isinstance(valeur, list) else str(valeur)
    valeur = next(iter(champ_multilingue.values()), "")
    return " | ".join(str(v) for v in valeur if v) if isinstance(valeur, list) else str(valeur)


def _liste(champ_multilingue: dict | None, langues: tuple[str, ...] = ("fra", "eng")) -> list[str]:
    """Comme `_texte_complet`, mais renvoie la LISTE de valeurs (une par lot),
    utilisée pour aligner par index avec `identifier-lot`."""
    if not champ_multilingue:
        return []
    for langue in langues:
        if langue in champ_multilingue:
            valeur = champ_multilingue[langue]
            return valeur if isinstance(valeur, list) else [str(valeur)]
    valeur = next(iter(champ_multilingue.values()), [])
    return valeur if isinstance(valeur, list) else [str(valeur)]


def _normaliser_departement(places_of_performance: list[str]) -> str:
    codes = set()
    for place in places_of_performance or []:
        prefixe = place[:4]  # ex. "FRY40" -> "FRY4"
        if prefixe in _NUTS_VERS_DEPARTEMENT:
            codes.add(_NUTS_VERS_DEPARTEMENT[prefixe])
    return ", ".join(sorted(codes))


def _extraire_lots(notice: dict) -> list[dict]:
    """Reconstruit la liste des lots à partir des 3 champs parallèles
    `identifier-lot`/`title-lot`/`description-lot`, alignés par index."""
    identifiants = notice.get("identifier-lot") or []
    titres = _liste(notice.get("title-lot"))
    descriptions = _liste(notice.get("description-lot"))

    nb_lots = max(len(identifiants), len(titres), len(descriptions))
    lots = []
    for i in range(nb_lots):
        identifiant = identifiants[i] if i < len(identifiants) else ""
        titre = titres[i] if i < len(titres) else ""
        description = descriptions[i] if i < len(descriptions) else ""
        if identifiant or titre or description:
            lots.append({"identifiant": str(identifiant), "titre": str(titre), "description": str(description)})
    return lots


def _construire_requete(departements: list[str], seulement_ouverts: bool) -> str:
    codes_nuts = [NUTS_MAP[dep] for dep in departements if dep in NUTS_MAP]
    if not codes_nuts:
        raise ValueError("Aucun code NUTS connu pour les départements demandés — complétez NUTS_MAP.")
    clause_nuts = " OR ".join(f"place-of-performance={code}" for code in codes_nuts)
    clauses = [f"({clause_nuts})"]
    if seulement_ouverts:
        clauses.append("deadline-receipt-tender-date-lot>=today()")
    return " AND ".join(clauses)


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis TED normalisés (voir sources/commun.py pour le format).

    Lève une exception (réseau, HTTP) si l'API ne répond pas comme attendu —
    à charge de l'appelant (sources/__init__.py) de la catcher.
    """
    corps = {
        "query": _construire_requete(departements, seulement_ouverts),
        "fields": FIELDS,
        "limit": min(limit, 250),  # 250 = maximum accepté par page par l'API TED
        "scope": "ACTIVE",
        "paginationMode": "ITERATION",
    }
    reponse = requests.post(BASE_URL, json=corps, timeout=30)
    reponse.raise_for_status()
    notices = reponse.json().get("notices", [])[:limit]

    resultats = []
    for n in notices:
        html_links = (n.get("links") or {}).get("html") or {}
        url = html_links.get("FRA") or html_links.get("ENG") or ""

        deadlines = n.get("deadline-receipt-tender-date-lot") or []
        date_limite = deadlines[0] if deadlines else ""

        dates_parution = n.get("publication-date")
        date_parution = dates_parution[0] if isinstance(dates_parution, list) and dates_parution else (dates_parution or "")

        resultats.append({
            "source": "TED",
            "identifiant": n.get("publication-number") or "",
            "objet": _premiere_valeur(n.get("notice-title")),
            "description": _texte_complet(n.get("description-lot")),
            "lots": _extraire_lots(n),
            "acheteur": _premiere_valeur(n.get("buyer-name")),
            "departement": _normaliser_departement(n.get("place-of-performance") or []),
            "date_parution": normaliser_date(date_parution),
            "date_limite_reponse": normaliser_date(date_limite),
            "url": url,
            "_annonce_lie": [],  # pas de rectificatif chaîné côté TED
        })
    return resultats


if __name__ == "__main__":
    # Test manuel : `python -m sources.ted` OU `python sources/ted.py`
    # (depuis etape7_pipeline_final/, ou bouton "Run" de l'IDE) — les deux
    # marchent (voir le try/except d'import de `commun` en tête de fichier).
    # API publique, aucun identifiant requis — plusieurs scénarios enchaînés
    # pour balayer différentes combinaisons de paramètres.
    import json

    print("=" * 70)
    print("sources/ted.py — test manuel")
    print("=" * 70)

    scenarios = [
        ("974 seul, ouverts uniquement", ["974"], True),
        ("976 seul, ouverts uniquement", ["976"], True),
        ("974+976, tous (ouverts ou non)", ["974", "976"], False),
    ]
    for libelle, departements, seulement_ouverts in scenarios:
        resultats = recuperer(departements, seulement_ouverts=seulement_ouverts, limit=20)
        print(f"\n--- {libelle} : {len(resultats)} résultat(s) ---")
        if resultats:
            print(json.dumps(resultats[0], ensure_ascii=False, indent=2)[:500] + " ...")
