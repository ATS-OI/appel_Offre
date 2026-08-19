"""
sources/aws_solutions.py — AWSolutions (https://awsolutions.fr)
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py). Contrairement à BOAMP/TED
(API publiques), AWSolutions demande un compte (identifiants dans `.env` :
`EMAIL_AWS`/`MOT_DE_PASSE_AWS`) et un login via navigateur (Playwright) pour
récupérer un jeton JWT, avant d'interroger son API interne.

Plus de filtre mots-clés/lots/acheteur ici (contrairement à l'ancien aws.py
d'etape6) : comme pour BOAMP/TED, on récupère tout ce qui correspond aux
départements suivis, le filtre se fait une seule fois pour toutes les
sources (voir sources/__init__.py et sources/commun.py::est_pertinent).

⚠️ Source fragile par nature (identifiants + site tiers qui peut changer) :
toute erreur ici (identifiants manquants, login qui échoue, API qui change)
doit remonter comme une exception explicite — c'est `sources/__init__.py`
qui l'attrape et prévient l'utilisateur (encadré rouge), sans faire planter
la récupération des 3 autres sources.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

try:
    from .commun import assurer_navigateur_installe, normaliser_date
except ImportError:
    # Lancé directement (`python sources/aws_solutions.py` ou bouton "Run"
    # de l'IDE) plutôt qu'en module (`python -m sources.aws_solutions`) —
    # voir boamp.py pour le détail de ce repli.
    from commun import assurer_navigateur_installe, normaliser_date

URL_LOGIN = "https://awsolutions.fr/apr/"
URL_API = "https://awsolutions.fr/apiSelenee/apiSearch/searchConsultations"


def _recuperer_token() -> str:
    """Se connecte au site via un navigateur headless (Playwright) et
    intercepte le jeton JWT envoyé par le site à sa propre API."""
    email = os.environ.get("EMAIL_AWS")
    mot_de_passe = os.environ.get("MOT_DE_PASSE_AWS")
    if not email or not mot_de_passe:
        raise RuntimeError("EMAIL_AWS et/ou MOT_DE_PASSE_AWS manquants dans .env — voir .env.example.")

    assurer_navigateur_installe()

    from playwright.sync_api import sync_playwright

    token: str | None = None
    with sync_playwright() as p:
        navigateur = p.chromium.launch(headless=True)
        page = navigateur.new_page()

        def espionner(requete):
            nonlocal token
            if "apiSelenee" in requete.url:
                entete = requete.headers.get("authorization")
                if entete and "Bearer" in entete:
                    token = entete

        page.on("request", espionner)
        page.goto(URL_LOGIN)
        page.fill('input[name="username"]', email)
        page.fill('input[name="password"]', mot_de_passe)
        page.click('input[type="submit"], button[type="submit"]')
        page.wait_for_load_state("networkidle")
        navigateur.close()

    if not token:
        raise RuntimeError("Connexion à AWSolutions réussie mais aucun jeton d'API intercepté — le site a peut-être changé.")
    return token


def _normaliser_lots(marche: dict) -> list[dict]:
    lots = []
    for lot in marche.get("lots") or []:
        libelle = lot.get("libelle") or ""
        numero = lot.get("numero") or ""
        if libelle or numero:
            lots.append({"identifiant": str(numero), "titre": libelle, "description": ""})
    return lots


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis AWSolutions normalisés (voir sources/commun.py).

    Lève une exception (identifiants manquants, login échoué, API HTTP en
    erreur) — à charge de l'appelant (sources/__init__.py) de la catcher.
    """
    token = _recuperer_token()
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Authorization": token,
    }

    criteres: dict = {"localisation_departement": departements}
    if seulement_ouverts:
        maintenant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        criteres["minDateExp"] = [maintenant]

    resultats: list[dict] = []
    page_actuelle = 0
    pages_totales = 1

    while page_actuelle < pages_totales and len(resultats) < limit:
        payload = {
            "criteres": criteres,
            "page": page_actuelle,
            "isSchoolMode": False,
            "sortBy": {"datePub": True},
        }
        reponse = requests.post(URL_API, json=payload, headers=headers, timeout=30)
        reponse.raise_for_status()
        donnees = reponse.json()
        pages_totales = donnees.get("totalPages", 1)

        for marche in donnees.get("content") or []:
            resultats.append({
                "source": "AWS",
                "identifiant": marche.get("referenceAWS") or "",
                "objet": marche.get("objet") or "",
                "description": "",
                "lots": _normaliser_lots(marche),
                "acheteur": marche.get("acheteurNom") or "",
                "departement": ", ".join(departements),
                "date_parution": "",
                "date_limite_reponse": normaliser_date(marche.get("dateExp") or ""),
                "url": marche.get("urlAnnonce") or "",
                "_annonce_lie": [],
            })
        page_actuelle += 1

    return resultats


if __name__ == "__main__":
    # Test manuel : `python -m sources.aws_solutions` OU
    # `python sources/aws_solutions.py` (depuis etape7_pipeline_final/, ou
    # bouton "Run" de l'IDE) — les deux marchent (voir le try/except
    # d'import de `commun` en tête de fichier).
    # Nécessite EMAIL_AWS/MOT_DE_PASSE_AWS dans l'environnement — sans ça, le
    # chemin d'erreur "identifiants manquants" est testé à la place (c'est le
    # chemin emprunté par ce module tant que le compte n'est pas configuré).
    import json

    from dotenv import load_dotenv

    load_dotenv()  # ok ici : script autonome, pas importé par le reste de l'appli

    print("=" * 70)
    print("sources/aws_solutions.py — test manuel")
    print("=" * 70)
    try:
        resultats = recuperer(["974", "976"], seulement_ouverts=True, limit=20)
        print(f"\n{len(resultats)} résultat(s).")
        if resultats:
            print(json.dumps(resultats[0], ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(f"\n⚠️ Chemin d'erreur attendu si les identifiants ne sont pas configurés : {exc}")
    except Exception as exc:
        print(f"\n❌ Erreur inattendue : {type(exc).__name__}: {exc}")
