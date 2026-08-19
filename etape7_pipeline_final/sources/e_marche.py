"""
sources/e_marche.py — e-marchespublics.com
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py). Comme AWSolutions, demande
un compte (identifiants dans `.env` : `EMAIL_EMP`/`MOT_DE_PASSE_EMP`) et un
login via navigateur (Playwright) pour récupérer une session, avant de
scraper les pages de résultats (pas d'API publique ici).

e-marchespublics agrège lui-même des avis venant d'autres plateformes (dont
BOAMP/JOUE — voir `SOURCES_EMP` ci-dessous, on interroge volontairement les
deux : natif ET agrégé, pour ne rater aucun avis visible depuis ce site) :
de la redondance avec notre propre source BOAMP est donc attendue. Rien à
faire de spécial ici — `sources/__init__.py` fusionne déjà TOUTES les
sources ensemble (voir `commun.py::fusionner_doublons`, qui gère déjà les
recoupements BOAMP<->TED, donc BOAMP<->EMP de la même façon).

Plus de filtre mots-clés/lots ici (contrairement à la première version
écrite à la main) : comme pour les autres sources, on récupère tout ce qui
correspond aux départements suivis, le filtre se fait une seule fois pour
toutes les sources (voir sources/__init__.py et
sources/commun.py::trouver_correspondances).

⚠️ Source fragile par nature (identifiants + site tiers qui peut changer,
scraping HTML sans API) : toute erreur ici doit remonter comme une
exception explicite — c'est `sources/__init__.py` qui l'attrape et prévient
l'utilisateur (encadré rouge), sans faire planter la récupération des 4
autres sources.

La date limite affichée par le site (`tds[9]`) est au format `JJ/MM/AAAA`
(vérifié en direct via `python -m sources.e_marche`) — DIFFÉRENT du format
`AAAA-MM-JJ` que `commun.normaliser_date` (`[:10]`) suppose. Utiliser cette
dernière ici renverrait `date_limite_reponse` tel quel (`"25/08/2026"`),
que Postgres refuserait ou interpréterait mal (colonne `date`, format ISO
attendu) — d'où `_normaliser_date_emp` ci-dessous, dédiée à ce format.
"""

from __future__ import annotations

import os
import re

import requests
from bs4 import BeautifulSoup

try:
    from .commun import assurer_navigateur_installe
except ImportError:
    # Lancé directement (`python sources/e_marche.py` ou bouton "Run" de
    # l'IDE) plutôt qu'en module (`python -m sources.e_marche`) — voir
    # boamp.py pour le détail de ce repli.
    from commun import assurer_navigateur_installe

URL_LOGIN = (
    "https://authenticate-v2.dematis.com/login/custom-emp"
    "?callbackUrl=https%3A%2F%2Fprivate.e-marchespublics.com%2Fsociete%2Fnew_societe%2Finclude%2Fcallback.php"
    "&appId=2"
)
URL_AJAX_FILTRES = "https://private.e-marchespublics.com/societe/div_ajax/les_requetes.php"
URL_RESULTATS = "https://private.e-marchespublics.com/societe/index.php"

# e-marchespublics propose plusieurs "sources" internes : le natif du site
# (gratuit) et un agrégat BOAMP/JOUE (payant, inclus dans l'abonnement). On
# interroge les deux pour ne rater aucun avis visible depuis ce site — voir
# la remarque sur la redondance en tête de fichier.
_SOURCES_EMP = [
    {"code": "1", "nom": "natif E-MP"},
    {"code": "2,3,4", "nom": "agrégé BOAMP/JOUE"},
]

_PAGES_MAX_PAR_SOURCE = 50  # garde-fou anti-boucle infinie si le site change de structure


def _creer_session_connectee(email: str, mot_de_passe: str) -> requests.Session:
    """Se connecte via un navigateur headless (Playwright) et transfère les
    cookies de session obtenus vers une session `requests` classique
    (le scraping des pages de résultats se fait ensuite sans navigateur,
    plus rapide)."""
    assurer_navigateur_installe()

    from playwright.sync_api import sync_playwright

    session = requests.Session()
    with sync_playwright() as p:
        navigateur = p.chromium.launch(headless=True)
        contexte = navigateur.new_context()
        page = contexte.new_page()
        try:
            page.goto(URL_LOGIN)
            page.fill('input[name="mail"]', email)
            page.fill('input[name="pwd"]', mot_de_passe)
            page.click('button[type="submit"]')
            page.wait_for_url("**/societe/**", timeout=15000)
            for cookie in contexte.cookies():
                session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
        finally:
            navigateur.close()
    return session


def _normaliser_date_emp(texte: str) -> str:
    """Convertit une date au format `JJ/MM/AAAA` (celui utilisé par ce
    site — vérifié en direct, voir la note en tête de fichier) vers le
    format `AAAA-MM-JJ` attendu par la colonne Postgres `date`. Renvoie ""
    si le texte ne contient pas une date reconnaissable (plutôt que de
    transmettre du texte brut qui ferait échouer l'upsert Supabase)."""
    correspondance = re.search(r"(\d{2})/(\d{2})/(\d{4})", texte or "")
    if not correspondance:
        return ""
    jour, mois, annee = correspondance.groups()
    return f"{annee}-{mois}-{jour}"


def _extraire_lots(soup: BeautifulSoup, reference: str) -> list[dict]:
    """Le détail des lots ("loupe") est un bloc HTML séparé, identifié par
    la référence de l'avis. Best-effort : le texte brut est gardé tel quel
    dans un lot unique plutôt que d'inventer un découpage lot-par-lot sans
    exemple réel de structure — suffisant pour la recherche de mots-clés
    (voir commun.py::texte_lots, qui concatène simplement titre+description)."""
    bloc = soup.find("div", id=f"loupe_{reference}")
    if not bloc:
        return []
    texte = bloc.get_text(separator=" | ", strip=True)
    return [{"identifiant": "", "titre": "", "description": texte}] if texte else []


def _extraire_lignes(html: str) -> list[dict]:
    """Parse une page de résultats et renvoie les avis bruts (pas encore le
    schéma normalisé — juste les champs extraits du HTML)."""
    soup = BeautifulSoup(html, "html.parser")
    lignes_brutes = []
    for ligne in soup.find_all("tr"):
        cellules = ligne.find_all("td", class_="td_G")
        if len(cellules) < 10:
            continue

        titre_tag = cellules[5].find("a")
        titre = titre_tag.get_text(strip=True) if titre_tag else cellules[5].get_text(strip=True)
        lien_brut = titre_tag["href"] if titre_tag else ""

        reference = ""
        correspondance = re.search(r"avis_([0-9a-zA-Z-]+)\.html", lien_brut)
        if correspondance:
            reference = correspondance.group(1)

        lignes_brutes.append({
            "reference": reference,
            "titre": titre,
            "acheteur": cellules[4].get_text(strip=True),
            "date_limite": cellules[9].get_text(separator=" ", strip=True),
            "lots": _extraire_lots(soup, reference),
            "url": f"https://private.e-marchespublics.com/societe/{lien_brut}" if lien_brut else "",
        })
    return lignes_brutes


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis e-marchespublics normalisés (voir sources/commun.py).

    `seulement_ouverts` n'est pas utilisé pour construire la requête (le
    site ne propose pas ce filtre dans l'écran utilisé ici) — filtré comme
    les autres critères, après coup, par le pipeline (voir
    `sources/__init__.py`/`pipeline.py::supprimer_offres_expirees`).

    Lève une exception (identifiants manquants, login échoué, HTML
    inattendu) — à charge de l'appelant (sources/__init__.py) de la catcher.
    """
    email = os.environ.get("EMAIL_EMP")
    mot_de_passe = os.environ.get("MOT_DE_PASSE_EMP")
    if not email or not mot_de_passe:
        raise RuntimeError("EMAIL_EMP et/ou MOT_DE_PASSE_EMP manquants dans .env — voir .env.example.")

    session = _creer_session_connectee(email, mot_de_passe)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # Filtre géographique envoyé une fois pour toute la session (le "72"
    # correspond au code interne du site pour un filtre par département —
    # relevé par observation du trafic réseau, pas documenté officiellement).
    payload_filtres = {"donnees": f"affichage//////null///1//////72:{','.join(departements)}///false/////////false///undefined"}
    session.post(URL_AJAX_FILTRES, data=payload_filtres, headers=headers, timeout=30)

    resultats: list[dict] = []
    for source in _SOURCES_EMP:
        page_actuelle = 1
        while page_actuelle <= _PAGES_MAX_PAR_SOURCE and len(resultats) < limit:
            reponse = session.get(
                URL_RESULTATS,
                params={"rub": "rech_complete_tab", "page": page_actuelle, "source": source["code"]},
                headers=headers,
                timeout=30,
            )
            reponse.raise_for_status()
            lignes = _extraire_lignes(reponse.text)
            if not lignes:
                break  # page vide -> fin de cette source

            for ligne in lignes:
                resultats.append({
                    "source": "EMP",
                    "identifiant": ligne["reference"],
                    "objet": ligne["titre"],
                    "description": "",
                    "lots": ligne["lots"],
                    "acheteur": ligne["acheteur"],
                    "departement": ", ".join(departements),
                    "date_parution": "",
                    "date_limite_reponse": _normaliser_date_emp(ligne["date_limite"]),
                    "url": ligne["url"],
                    "_annonce_lie": [],
                })
            page_actuelle += 1

    return resultats


if __name__ == "__main__":
    # Test manuel : `python -m sources.e_marche` OU `python sources/e_marche.py`
    # (depuis etape7_pipeline_final/, ou bouton "Run" de l'IDE) — les deux
    # marchent (voir le try/except d'import de `commun` en tête de fichier).
    # Nécessite EMAIL_EMP/MOT_DE_PASSE_EMP dans l'environnement — sans ça, le
    # chemin d'erreur "identifiants manquants" est testé à la place (utile
    # aussi, c'est le chemin emprunté par ce module quand le compte n'est
    # pas encore configuré, voir sources/__init__.py).
    import json

    from dotenv import load_dotenv

    load_dotenv()  # ok ici : script autonome, pas importé par le reste de l'appli

    print("=" * 70)
    print("sources/e_marche.py — test manuel")
    print("=" * 70)
    try:
        resultats = recuperer(["974", "976"], seulement_ouverts=True, limit=20)
        print(f"\n{len(resultats)} résultat(s).")
        if resultats:
            print("\nExemple :")
            print(json.dumps(resultats[0], ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(f"\n⚠️ Chemin d'erreur attendu si les identifiants ne sont pas configurés : {exc}")
    except Exception as exc:
        print(f"\n❌ Erreur inattendue : {type(exc).__name__}: {exc}")
