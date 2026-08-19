"""
sources/boamp.py — BOAMP (https://boamp-datadila.opendatasoft.com)
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py pour le format). Interroge
l'API par département + date limite uniquement (pas de mots-clés côté
serveur — le filtre `objet like` de BOAMP ne porte pas sur le détail des
lots d'un marché multi-lots, on filtre donc après coup, une fois toutes les
sources récupérées, voir sources/__init__.py).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import requests

try:
    from .commun import normaliser_date
except ImportError:
    # Lancé directement (`python sources/boamp.py` ou bouton "Run" de l'IDE)
    # plutôt qu'en module (`python -m sources.boamp`) : pas de paquet parent
    # pour l'import relatif. Repli en import absolu — fonctionne car Python
    # ajoute automatiquement le dossier du script (`sources/`) à `sys.path`
    # dans ce cas, donc `commun.py` (juste à côté) est trouvable tel quel.
    from commun import normaliser_date

BASE_URL = "https://boamp-datadila.opendatasoft.com/api/v2/catalog/datasets/boamp/records"
PAGE_SIZE = 100  # taille max de page acceptée par l'API Opendatasoft


def _construire_where(departements: list[str], seulement_ouverts: bool) -> str:
    """`code_departement` est utilisé plutôt que `code_departement_prestation`
    (lieu d'exécution) : ce dernier est quasiment toujours vide sur les avis
    récents (vérifié en direct), contrairement à `code_departement`.
    """
    clause_departements = " or ".join(f'code_departement="{dep}"' for dep in departements)
    clauses = [f"({clause_departements})"]
    if seulement_ouverts:
        clauses.append(f"datelimitereponse > date'{date.today().isoformat()}'")
    return " and ".join(clauses)


def _interroger(departements: list[str], seulement_ouverts: bool, limit: int) -> list[dict]:
    where = _construire_where(departements, seulement_ouverts)
    resultats: list[dict] = []
    offset = 0

    while len(resultats) < limit:
        params = {
            "where": where,
            "limit": min(PAGE_SIZE, limit - len(resultats)),
            "offset": offset,
            "order_by": "datelimitereponse asc",
        }
        reponse = requests.get(BASE_URL, params=params, timeout=30)
        reponse.raise_for_status()

        page = reponse.json().get("records", [])
        if not page:
            break
        resultats.extend(rec["record"]["fields"] for rec in page)
        offset += len(page)
        if len(page) < params["limit"]:
            break

    return resultats


def _extraire_description(donnees_json: str | None) -> str:
    """Best-effort : description longue depuis le blob JSON eForms (uniquement
    présent sur les avis BOAMP récents)."""
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


def _extraire_lots(donnees_json: str | None) -> list[dict]:
    """Best-effort : détail des lots depuis `cac:ProcurementProjectLot` (dict
    si le marché n'a qu'un seul lot, liste sinon — les deux formes existent
    en pratique)."""
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


def _normaliser_departement(code_departement: Any) -> str:
    if isinstance(code_departement, list):
        codes = code_departement
    elif code_departement:
        codes = [code_departement]
    else:
        codes = []
    return ", ".join(sorted({str(c) for c in codes if c}))


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis BOAMP normalisés (voir sources/commun.py pour le format).

    Lève une exception (réseau, HTTP, JSON) si l'API ne répond pas comme
    attendu — à charge de l'appelant (sources/__init__.py) de la catcher.
    """
    records = _interroger(departements, seulement_ouverts, limit)
    resultats = []
    for f in records:
        resultats.append({
            "source": "BOAMP",
            "identifiant": f.get("idweb") or "",
            "objet": f.get("objet") or "",
            "description": _extraire_description(f.get("donnees")),
            "lots": _extraire_lots(f.get("donnees")),
            "acheteur": f.get("nomacheteur") or "",
            "departement": _normaliser_departement(f.get("code_departement")),
            "date_parution": normaliser_date(f.get("dateparution") or ""),
            "date_limite_reponse": normaliser_date(f.get("datelimitereponse") or ""),
            "url": f.get("url_avis") or "",
            # champ interne, utilisé uniquement pour le rapprochement précis
            # des rectificatifs BOAMP (voir sources/__init__.py) :
            "_annonce_lie": f.get("annonce_lie") or [],
        })
    return resultats


if __name__ == "__main__":
    # Test manuel : `python -m sources.boamp` OU `python sources/boamp.py`
    # (depuis etape7_pipeline_final/, ou bouton "Run" de l'IDE) — les deux
    # marchent (voir le try/except d'import de `commun` en tête de fichier).
    # API publique, aucun identifiant requis — plusieurs scénarios enchaînés
    # pour balayer différentes combinaisons de paramètres.
    import json

    print("=" * 70)
    print("sources/boamp.py — test manuel")
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
