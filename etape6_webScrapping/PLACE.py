import requests
from bs4 import BeautifulSoup
import re
import json

# =====================================================================
# 🛠️ CONFIGURATION ET TABLEAU DE BORD
# =====================================================================
URL_RECHERCHE = "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&searchAnnCons"
URL_BASE = "https://www.marches-publics.gouv.fr"

CONFIG = {
    "filtre_acheteur": {
        "actif": False,
        "liste_cibles": ["sidr", "cbo", "shlmr", "sedre", "spag", "icade", "opale", "jwh", "cirad"]
    },
    "filtre_mots_cles": {
        "recherche_dans_titre": True, 
        "mots_cles_titre": [
            "rénovation", "construction", "école", "collège", "lycée", 
            "université", "laboratoire", "logement", "paillasse", "cuisine", 
            "placard", "agencement", "mobilier", "infrastructure", "réhabilitation", 
            "salle de classe", "salle de bain", "meuble", "cloison sanitaire", 
            "saniclip", "équipement spécialisé", "materiel de laboratoire", 
            "plan vasque", "aménagement", "menuiserie", "plan de travail", "restructuration"
        ],
        "recherche_dans_lots": True,  
        "mots_cles_lots": [
            "menuiserie", "meuble", "cuisine", "sdb", "salle de bain", 
            "placard", "aménagement", "mobilier", "agencement", 
            "salle spécialisée", "cloison", "paillasse", "sur mesure", "gros œuvre", "peinture"
        ]
    }
}

# =====================================================================
# ⛏️ FONCTION : EXTRACTION RAPIDE DES LOTS VIA POP-UP
# =====================================================================
def extraire_lots_popup(session, url_popup):
    """Visite le lien caché du pop-up HTML et extrait tous les lots proprement."""
    try:
        reponse = session.get(url_popup, timeout=10)
        if reponse.status_code != 200:
            return []
            
        soup_popup = BeautifulSoup(reponse.text, 'html.parser')
        panels = soup_popup.find_all("div", class_="panel-default")
        lots_extraits = []
        
        for panel in panels:
            heading = panel.find("div", class_="panel-heading")
            if not heading:
                continue
                
            strong = heading.find("strong")
            numero = strong.get_text(strip=True).replace(':', '').replace('Lot', '').strip() if strong else "?"
            
            spans = heading.find_all("span")
            description = spans[-1].get_text(strip=True) if len(spans) > 1 else ""
            
            cpv_liste = []
            cpv_panel = panel.find("div", id=re.compile(r"panelCodeRef"))
            if cpv_panel:
                cpv_spans = cpv_panel.find_all("span", style="display: block")
                for cs in cpv_spans:
                    code = cs.get_text(strip=True).replace("(Code principal)", "").strip()
                    for c in code.split(';'):
                        if c.strip():
                            cpv_liste.append(c.strip())
            
            lots_extraits.append({
                "numero": numero,
                "description": description,
                "cpv": cpv_liste
            })
            
        return lots_extraits
        
    except Exception as e:
        print(f"      ❌ Erreur pop-up : {e}")
        return []

# =====================================================================
# 🚀 FONCTION PRINCIPALE : SCRAPING PLACE (AVEC PAGINATION)
# =====================================================================
def scrapper_place():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    print("🕵️  Étape 1/3 : Connexion initiale et récupération du jeton de sécurité...")
    reponse_get = session.get(URL_RECHERCHE, headers=headers)
    soup_get = BeautifulSoup(reponse_get.text, 'html.parser')
    prado_input = soup_get.find("input", {"id": "PRADO_PAGESTATE"})
    
    if not prado_input:
        print("❌ Impossible de trouver le PRADO_PAGESTATE.")
        return []
        
    payload_recherche = {
        "PRADO_PAGESTATE": prado_input["value"],
        "ctl0$CONTENU_PAGE$AdvancedSearch$idsSelectedGeoN2": ",,719,716,",
        "ctl0$CONTENU_PAGE$AdvancedSearch$numSelectedGeoN2": "RE_YT",
        "ctl0$CONTENU_PAGE$AdvancedSearch$lancerRecherche": "Lancer la recherche",
        "ctl0$CONTENU_PAGE$AdvancedSearch$type_rechercheEntite": "floue",
        "ctl0$CONTENU_PAGE$AdvancedSearch$procedureType": "1",
        "ctl0$CONTENU_PAGE$AdvancedSearch$categorie": "0"
    }

    print("🚀 Étape 2/3 : Envoi de la requête de recherche (Filtre Réunion/Mayotte)...")
    reponse_post = session.post(URL_RECHERCHE, data=payload_recherche, headers=headers)
    html_courant = BeautifulSoup(reponse_post.text, 'html.parser')
    
    # --- DÉTECTION DE LA PAGINATION ---
    span_total = html_courant.find("span", id=re.compile(r"nombrePageTop"))
    total_pages = int(span_total.get_text(strip=True)) if span_total else 1
    print(f"📊 Il y a {total_pages} page(s) de résultats à analyser.\n")
    
    appels_offres_finaux = []
    page_actuelle = 1
    
    # --- BOUCLE SUR TOUTES LES PAGES ---
    while page_actuelle <= total_pages:
        print(f"--- 🔄 TRAITEMENT DE LA PAGE {page_actuelle}/{total_pages} ---")
        
        consultations = html_courant.find_all("div", class_=re.compile("item_consultation"))
        
        for idx, cons in enumerate(consultations):
            try:
                # --- EXTRACTION DE SURFACE ---
                objet_div = cons.find("div", id=re.compile(r".*panelBlocObjet"))
                titre = objet_div.find_all("span", class_="small")[-1].get_text(strip=True) if objet_div else "Titre introuvable"
                
                org_div = cons.find("div", id=re.compile(r".*panelBlocDenomination"))
                acheteur = org_div.find_all("span", class_="small")[-1].get_text(strip=True) if org_div else "Acheteur introuvable"
                
                ref_div = cons.find("div", id=re.compile(r".*panelBlocIntitule"))
                reference = ref_div.find("div", class_="small pull-left").get_text(strip=True) if ref_div else "Réf introuvable"
                
                # --- FILTRE 1 : ACHETEUR ---
                if CONFIG["filtre_acheteur"]["actif"]:
                    acheteur_lower = acheteur.lower()
                    if not any(cible.lower() in acheteur_lower for cible in CONFIG["filtre_acheteur"]["liste_cibles"]):
                        continue
                
                # --- EXTRACTION DES LOTS VIA POP-UP ---
                tous_les_lots = []
                lots_div = cons.find("div", class_="lots")
                if lots_div:
                    a_tag = lots_div.find("a", href=re.compile(r"popUpOpen"))
                    if a_tag:
                        match = re.search(r"popUpOpen\('([^']+)'", a_tag['href'])
                        if match:
                            url_rc_popup = f"{URL_BASE}/{match.group(1)}"
                            tous_les_lots = extraire_lots_popup(session, url_rc_popup)

                # --- FILTRE 2 : MOTS-CLÉS (LOGIQUE OU) ---
                est_pertinent = False
                
                # A. Titre
                if CONFIG["filtre_mots_cles"]["recherche_dans_titre"]:
                    if any(mot.lower() in titre.lower() for mot in CONFIG["filtre_mots_cles"]["mots_cles_titre"]):
                        est_pertinent = True
                
                # B. Lots
                if not est_pertinent and CONFIG["filtre_mots_cles"]["recherche_dans_lots"]:
                    texte_lots_concat = " ".join([lot.get("description", "").lower() for lot in tous_les_lots])
                    if any(mot.lower() in texte_lots_concat for mot in CONFIG["filtre_mots_cles"]["mots_cles_lots"]):
                        est_pertinent = True
                
                # Si rejeté, on passe au marché suivant
                if not est_pertinent and (CONFIG["filtre_mots_cles"]["recherche_dans_titre"] or CONFIG["filtre_mots_cles"]["recherche_dans_lots"]):
                    continue

                # --- SAUVEGARDE ---
                # 🔴 CORRECTION DU CHEMIN CONFIG ICI :
                lots_cibles = [lot for lot in tous_les_lots if any(mot.lower() in lot['description'].lower() for mot in CONFIG["filtre_mots_cles"]["mots_cles_lots"])]
                
                print(f"   ✅ [Validé] {reference} ({len(lots_cibles)} lot(s) cible(s) sur {len(tous_les_lots)})")
                
                ao_structure = {
                    "plateforme": "PLACE",
                    "reference": reference,
                    "acheteur": acheteur,
                    "titre": titre,
                    "nombre_de_lots": len(tous_les_lots),
                    "lots_cibles_detectes": lots_cibles,
                    "tous_les_lots": tous_les_lots
                }
                appels_offres_finaux.append(ao_structure)
                
            except Exception as e:
                print(f"⚠️ Erreur parsing sur l'annonce {idx} : {e}")
                continue
                
        # --- LOGIQUE DE PAGINATION (Passage à la page suivante) ---
        page_actuelle += 1
        
        if page_actuelle <= total_pages:
            # On doit voler le nouveau jeton PRADO sur la page qu'on vient de lire !
            prado_input = html_courant.find("input", {"id": "PRADO_PAGESTATE"})
            if not prado_input:
                print("❌ Jeton PRADO introuvable pour la pagination. Arrêt prématuré.")
                break
                
            payload_page = {
                "PRADO_PAGESTATE": prado_input["value"],
                "ctl0$CONTENU_PAGE$resultSearch$numPageTop": str(page_actuelle),
                "ctl0$CONTENU_PAGE$resultSearch$DefaultButtonTop": "" # On simule le clic sur "Aller à la page"
            }
            
            reponse_page = session.post(URL_RECHERCHE, data=payload_page, headers=headers)
            html_courant = BeautifulSoup(reponse_page.text, 'html.parser')

    return appels_offres_finaux

# ==========================================
# EXÉCUTION PRINCIPALE
# ==========================================
if __name__ == "__main__":
    resultats = scrapper_place()
    print("\n" + "="*50)
    print(f"🎯 Total d'appels d'offres qualifiés : {len(resultats)}")
    print("="*50)
    
    if resultats:
        print("Exemple du premier marché extrait :")
        print(json.dumps(resultats[0], indent=4, ensure_ascii=False))