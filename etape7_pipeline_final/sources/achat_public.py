"""
sources/achat_public.py — achatpublic.com
================================================================================

Une seule fonction publique : `recuperer(departements, seulement_ouverts) ->
list[dict normalisé]` (voir sources/commun.py). Pas de compte requis, mais
pas d'API non plus : scraping HTML (requests + BeautifulSoup), avec une
session générée automatiquement (cookies) puis un formulaire de recherche
envoyé tel quel.

⚠️ Limite connue, assumée par choix (pas un oubli) : contrairement aux
autres sources, le filtre géographique n'est PAS construit à partir de
`departements` — achatpublic.com attend un payload de formulaire complet
(`PAYLOAD_RECHERCHE` ci-dessous), pas un simple paramètre. Ce payload se
récupère en faisant la recherche à la main sur le site (F12 -> onglet
réseau -> requête `rechercheCsl.action` -> copier le corps de la requête),
déjà rempli ci-dessous pour 974+976. Tant qu'il est vide, cette source
échoue proprement (voir `recuperer`) plutôt que de scanner toute la France
par défaut (ce que ferait le script d'origine).

La date limite affichée par le site est au format `J Mois AAAA HH : MM`
(ex. `"19 Août 2026 10 : 00"`, vérifié en direct via
`python sources/achat_public.py`) — DIFFÉRENT du format `AAAA-MM-JJ` que
`commun.normaliser_date` suppose. D'où `_normaliser_date_achatpublic`
ci-dessous, dédiée à ce format (avec un repli JJ/MM/AAAA au cas où le site
change de présentation).
"""

from __future__ import annotations

import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

# 🔴 Payload de recherche fixe pour La Réunion + Mayotte (récupéré une fois
# depuis le site — voir la note en tête de fichier pour comment le
# renouveler si le site change son formulaire).
PAYLOAD_RECHERCHE = (
    "searchCslBean.initial=0&searchCslBean.intitule=&searchCslBean.region="
    "&locations%5B%5D=+974+-+La+R%E9union+&locations%5B%5D=+976+-+Mayotte+"
    "&searchCslBean.departement=+974%21%3B%21+976&searchCslBean.dlrpStart="
    "&searchCslBean.dlrpEnd=&searchCslBean.procedure=-1&searchCslBean.codeCPV="
    "&codeCPV="
)

URL_BASE = "https://www.achatpublic.com"
_PAGES_MAX = 50  # garde-fou anti-boucle infinie si le site change de structure


_MOIS_FR = {
    "janvier": "01", "fevrier": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "aout": "08",
    "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12",
}


def _sans_accent(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")


def _normaliser_date_achatpublic(texte: str) -> str:
    """Convertit une date au format `J Mois AAAA` (celui utilisé par ce
    site, ex. "19 Août 2026 10 : 00" — voir la note en tête de fichier) vers
    `AAAA-MM-JJ`, attendu par la colonne Postgres `date`. Renvoie "" si le
    texte ne contient pas une date reconnaissable (plutôt que de transmettre
    du texte brut qui ferait échouer l'upsert Supabase)."""
    texte = texte or ""

    correspondance = re.search(r"(\d{2})/(\d{2})/(\d{4})", texte)  # repli si le site change de format
    if correspondance:
        jour, mois, annee = correspondance.groups()
        return f"{annee}-{mois}-{jour}"

    correspondance = re.search(r"(\d{4}-\d{2}-\d{2})", texte)
    if correspondance:
        return correspondance.group(1)

    correspondance = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", texte)
    if correspondance:
        jour, mois_texte, annee = correspondance.groups()
        mois = _MOIS_FR.get(_sans_accent(mois_texte).lower())
        if mois:
            return f"{annee}-{mois}-{jour.zfill(2)}"

    return ""


def _extraire_lots(session: requests.Session, url_detail: str) -> list[dict]:
    """Le détail des lots est sur un onglet séparé de la fiche de l'avis
    (`ongletActif=2`), une requête HTTP de plus par avis à lots."""
    if "ongletActif" not in url_detail:
        url_detail += "&ongletActif=2" if "?" in url_detail else "?ongletActif=2"
    try:
        time.sleep(0.5)  # pause polie, cohérent avec le script d'origine
        reponse = session.get(url_detail, timeout=15)
        soup = BeautifulSoup(reponse.text, "html.parser")
        corps = soup.find("tbody", class_="sdmBasicTable__body")
        if not corps:
            return []
        lots = []
        for ligne in corps.find_all("tr", class_="sdmBasicTable__line"):
            cellules = ligne.find_all("td")
            if len(cellules) >= 2:
                lots.append({
                    "identifiant": cellules[0].get_text(strip=True),
                    "titre": "",
                    "description": cellules[1].get_text(strip=True),
                })
        return lots
    except Exception:
        return []  # une fiche illisible ne doit pas faire échouer toute la récupération


def _extraire_lignes(html: str) -> list[dict]:
    """Parse une page de résultats et renvoie les avis bruts (pas encore le
    schéma normalisé, ni les lots — voir `recuperer`, qui a besoin de
    `session` pour aller chercher les lots)."""
    soup = BeautifulSoup(html, "html.parser")
    lignes_brutes = []
    for ligne in soup.find_all("li", class_=re.compile(r"sdmListResult__mainListItem")):
        identifiant = ligne.get("id", "").replace("li_consult_", "")
        titre_tag = ligne.find("h2", class_="sdmCardGeneric__title")
        lien_tag = titre_tag.find("a") if titre_tag else None
        titre = lien_tag.get_text(strip=True) if lien_tag else ""
        lien_brut = lien_tag["href"] if lien_tag else ""
        url = f"{URL_BASE}{lien_brut}" if lien_brut else ""

        date_str = ""
        bloc_time = ligne.find("div", class_="sdmCardConsult__blocTime")
        if bloc_time:
            date_str = " ".join(s.get_text(strip=True) for s in bloc_time.find_all("span"))

        details = {}
        for item in ligne.find_all("li", class_="sdmCardConsult__listItem"):
            spans = item.find_all("span")
            if len(spans) >= 2:
                cle = spans[0].get_text(strip=True).replace(":", "").replace("\xa0", "").strip()
                details[cle] = spans[1].get_text(strip=True)

        lignes_brutes.append({
            "identifiant": identifiant,
            "titre": titre,
            "acheteur": details.get("Organisme", ""),
            "date_limite": date_str,
            "url": url,
            "nb_lots": details.get("Lots", details.get("Allotissement", "1")),
        })
    return lignes_brutes


def recuperer(departements: list[str], seulement_ouverts: bool = True, limit: int = 250) -> list[dict]:
    """Renvoie les avis achatpublic.com normalisés (voir sources/commun.py).

    `departements`/`seulement_ouverts` sont gardés dans la signature pour
    l'interface commune aux 6 sources, mais SANS effet réel sur la requête
    (voir la limite connue en tête de fichier — le filtre géographique est
    déjà encodé dans `PAYLOAD_RECHERCHE`).

    Lève une exception (payload non configuré, réseau, HTML inattendu) — à
    charge de l'appelant (sources/__init__.py) de la catcher.
    """
    if not PAYLOAD_RECHERCHE.strip():
        raise RuntimeError(
            "PAYLOAD_RECHERCHE n'est pas configuré dans sources/achat_public.py — "
            "voir la note en tête de fichier pour savoir comment le récupérer sur le site."
        )

    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    session.get(f"{URL_BASE}/sdm/ent2/gen/accueil.action", headers=headers, timeout=15)

    url_recherche = f"{URL_BASE}/sdm/ent2/gen/rechercheCsl.action"
    headers_recherche = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    reponse = session.post(url_recherche, data=PAYLOAD_RECHERCHE, headers=headers_recherche, timeout=15)
    reponse.raise_for_status()

    resultats: list[dict] = []
    page_actuelle = 1

    while page_actuelle <= _PAGES_MAX and len(resultats) < limit:
        lignes = _extraire_lignes(reponse.text)
        if not lignes:
            break

        for ligne in lignes:
            a_des_lots = "unique" not in ligne["nb_lots"].lower()
            lots = _extraire_lots(session, ligne["url"]) if (ligne["url"] and a_des_lots) else []
            resultats.append({
                "source": "ACHAT_PUBLIC",
                "identifiant": ligne["identifiant"],
                "objet": ligne["titre"],
                "description": "",
                "lots": lots,
                "acheteur": ligne["acheteur"],
                "departement": ", ".join(departements),
                "date_parution": "",
                "date_limite_reponse": _normaliser_date_achatpublic(ligne["date_limite"]),
                "url": ligne["url"],
                "_annonce_lie": [],
            })
            if len(resultats) >= limit:
                break

        page_actuelle += 1
        if page_actuelle > _PAGES_MAX or len(resultats) >= limit:
            break
        reponse = session.get(f"{url_recherche}?page={page_actuelle}", headers=headers, timeout=15)
        reponse.raise_for_status()

    return resultats


if __name__ == "__main__":
    # Test manuel : `python sources/achat_public.py` OU
    # `python -m sources.achat_public` (depuis etape7_pipeline_final/, ou
    # bouton "Run" de l'IDE) — les deux marchent (ce fichier n'importe rien
    # de `commun.py`, pas de souci d'import relatif ici).
    import json

    print("=" * 70)
    print("sources/achat_public.py — test manuel")
    print("=" * 70)
    try:
        resultats = recuperer(["974", "976"], seulement_ouverts=True, limit=20)
        print(f"\n{len(resultats)} résultat(s).")
        if resultats:
            print("\nExemple :")
            print(json.dumps(resultats[0], ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(f"\n⚠️ Chemin d'erreur attendu si PAYLOAD_RECHERCHE n'est pas configuré : {exc}")
    except Exception as exc:
        print(f"\n❌ Erreur inattendue : {type(exc).__name__}: {exc}")
