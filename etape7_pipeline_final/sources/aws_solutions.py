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
import subprocess
from datetime import datetime, timezone

import requests

from .commun import normaliser_date

URL_LOGIN = "https://awsolutions.fr/apr/"
URL_API = "https://awsolutions.fr/apiSelenee/apiSearch/searchConsultations"

_navigateur_verifie = False


def _assurer_navigateur_installe() -> None:
    """S'assure que le navigateur headless Chromium (nécessaire à Playwright)
    est bien installé avant de l'utiliser.

    En local/VM, l'installation se fait une fois pour toutes via `playwright
    install chromium` (voir README.md). Sur certains hébergeurs (ex.
    Streamlit Community Cloud), cette étape n'est JAMAIS exécutée
    automatiquement — seul `pip install -r requirements.txt` l'est — d'où
    l'erreur "Executable doesn't exist..." au premier lancement. On la
    déclenche donc ici, une fois par process : `playwright install` est
    déjà idempotent (quasi instantané s'il est déjà installé), donc sans
    coût perceptible les fois suivantes.
    """
    global _navigateur_verifie
    if _navigateur_verifie:
        return
    subprocess.run(["playwright", "install", "chromium"], check=False, capture_output=True)
    _navigateur_verifie = True


def _recuperer_token() -> str:
    """Se connecte au site via un navigateur headless (Playwright) et
    intercepte le jeton JWT envoyé par le site à sa propre API."""
    email = os.environ.get("EMAIL_AWS")
    mot_de_passe = os.environ.get("MOT_DE_PASSE_AWS")
    if not email or not mot_de_passe:
        raise RuntimeError("EMAIL_AWS et/ou MOT_DE_PASSE_AWS manquants dans .env — voir .env.example.")

    _assurer_navigateur_installe()

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
