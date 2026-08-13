"""
sources/place.py — PLACE (https://www.marches-publics.gouv.fr)
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py). Pas d'API publique ici :
scraping HTML (requests + BeautifulSoup), avec pagination par jeton PRADO
(mécanisme du framework du site, pas de notre fait).

⚠️ Limites connues (documentées ici plutôt qu'inventées) :
  - la recherche est câblée en dur sur La Réunion + Mayotte (paramètre
    `idsSelectedGeoN2`/`numSelectedGeoN2` du site) — `departements` n'est
    donc PAS utilisé pour construire la requête, contrairement aux 3 autres
    sources ; le paramètre est gardé dans la signature pour une interface
    commune, et pour documenter clairement cette limite ;
  - le HTML actuellement parsé ne contient pas de date limite de réponse ni
    de lien direct par avis (contrairement à BOAMP/TED/AWS) : ces deux champs
    sont renvoyés vides. Le filtre "seulement_ouverts" ne s'applique donc pas
    ici (rien à comparer) — tous les avis trouvés sont renvoyés ;
  - `seulement_ouverts` est gardé dans la signature pour la même raison
    (interface commune), sans effet réel.
  - un site qui change son HTML peut casser ce scraper (sélecteurs figés) —
    voir le message d'erreur affiché à l'utilisateur (sources/__init__.py).
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

URL_RECHERCHE = "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&searchAnnCons"
URL_BASE = "https://www.marches-publics.gouv.fr"


def _extraire_lots_popup(session: requests.Session, url_popup: str) -> list[dict]:
    """Visite le lien caché du pop-up HTML listant les lots d'un avis."""
    reponse = session.get(url_popup, timeout=10)
    if reponse.status_code != 200:
        return []

    soup = BeautifulSoup(reponse.text, "html.parser")
    lots = []
    for panel in soup.find_all("div", class_="panel-default"):
        heading = panel.find("div", class_="panel-heading")
        if not heading:
            continue
        strong = heading.find("strong")
        numero = strong.get_text(strip=True).replace(":", "").replace("Lot", "").strip() if strong else "?"
        spans = heading.find_all("span")
        description = spans[-1].get_text(strip=True) if len(spans) > 1 else ""
        lots.append({"identifiant": numero, "titre": "", "description": description})
    return lots


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis PLACE normalisés (voir sources/commun.py et les
    limites connues documentées en tête de ce fichier).

    Lève une exception (réseau, HTML inattendu — ex. jeton PRADO introuvable,
    signe que le site a changé) — à charge de l'appelant (sources/__init__.py)
    de la catcher.
    """
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    reponse_get = session.get(URL_RECHERCHE, headers=headers, timeout=30)
    reponse_get.raise_for_status()
    soup_get = BeautifulSoup(reponse_get.text, "html.parser")
    prado_input = soup_get.find("input", {"id": "PRADO_PAGESTATE"})
    if not prado_input:
        raise RuntimeError("Jeton PRADO_PAGESTATE introuvable sur la page de recherche — le site a peut-être changé.")

    payload_recherche = {
        "PRADO_PAGESTATE": prado_input["value"],
        # Câblé sur La Réunion + Mayotte (voir limites connues en tête de fichier).
        "ctl0$CONTENU_PAGE$AdvancedSearch$idsSelectedGeoN2": ",,719,716,",
        "ctl0$CONTENU_PAGE$AdvancedSearch$numSelectedGeoN2": "RE_YT",
        "ctl0$CONTENU_PAGE$AdvancedSearch$lancerRecherche": "Lancer la recherche",
        "ctl0$CONTENU_PAGE$AdvancedSearch$type_rechercheEntite": "floue",
        "ctl0$CONTENU_PAGE$AdvancedSearch$procedureType": "1",
        "ctl0$CONTENU_PAGE$AdvancedSearch$categorie": "0",
    }
    reponse_post = session.post(URL_RECHERCHE, data=payload_recherche, headers=headers, timeout=30)
    reponse_post.raise_for_status()
    html_courant = BeautifulSoup(reponse_post.text, "html.parser")

    span_total = html_courant.find("span", id=re.compile(r"nombrePageTop"))
    total_pages = int(span_total.get_text(strip=True)) if span_total else 1

    resultats: list[dict] = []
    page_actuelle = 1

    while page_actuelle <= total_pages and len(resultats) < limit:
        for cons in html_courant.find_all("div", class_=re.compile("item_consultation")):
            objet_div = cons.find("div", id=re.compile(r".*panelBlocObjet"))
            titre = objet_div.find_all("span", class_="small")[-1].get_text(strip=True) if objet_div else ""

            org_div = cons.find("div", id=re.compile(r".*panelBlocDenomination"))
            acheteur = org_div.find_all("span", class_="small")[-1].get_text(strip=True) if org_div else ""

            ref_div = cons.find("div", id=re.compile(r".*panelBlocIntitule"))
            ref_span = ref_div.find("div", class_="small pull-left") if ref_div else None
            reference = ref_span.get_text(strip=True) if ref_span else ""

            lots: list[dict] = []
            lots_div = cons.find("div", class_="lots")
            if lots_div:
                a_tag = lots_div.find("a", href=re.compile(r"popUpOpen"))
                if a_tag:
                    match = re.search(r"popUpOpen\('([^']+)'", a_tag["href"])
                    if match:
                        lots = _extraire_lots_popup(session, f"{URL_BASE}/{match.group(1)}")

            if not (titre or reference):
                continue

            resultats.append({
                "source": "PLACE",
                "identifiant": reference,
                "objet": titre,
                "description": "",
                "lots": lots,
                "acheteur": acheteur,
                "departement": ", ".join(departements),
                "date_parution": "",
                "date_limite_reponse": "",  # non extrait actuellement, voir limites connues
                "url": "",                  # non extrait actuellement, voir limites connues
                "_annonce_lie": [],
            })

        page_actuelle += 1
        if page_actuelle <= total_pages:
            prado_input = html_courant.find("input", {"id": "PRADO_PAGESTATE"})
            if not prado_input:
                break  # jeton introuvable pour la pagination : on s'arrête là, avec ce qu'on a déjà
            payload_page = {
                "PRADO_PAGESTATE": prado_input["value"],
                "ctl0$CONTENU_PAGE$resultSearch$numPageTop": str(page_actuelle),
                "ctl0$CONTENU_PAGE$resultSearch$DefaultButtonTop": "",
            }
            reponse_page = session.post(URL_RECHERCHE, data=payload_page, headers=headers, timeout=30)
            reponse_page.raise_for_status()
            html_courant = BeautifulSoup(reponse_page.text, "html.parser")

    return resultats
