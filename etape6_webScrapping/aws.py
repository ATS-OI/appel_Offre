import os
import requests
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Chargement sécurisé des identifiants
load_dotenv()
EMAIL_AWS = os.getenv("EMAIL_AWS")
MOT_DE_PASSE_AWS = os.getenv("MOT_DE_PASSE_AWS")

# =====================================================================
# 🛠️ TABLEAU DE BORD
# =====================================================================
CONFIG = {
    "departements": ["974", "976"],
    # Logique "OU" : Si n'importe quel de ces mots est dans l'objet ou les lots
    "mots_cles": [
        "rénovation", "construction", "école", "collège", "lycée", 
        "université", "laboratoire", "paillasse", "cuisine", 
        "placard", "agencement", "mobilier", "médical", "air"
    ],
    "uniquement_en_cours": True
}
# =====================================================================

def recuperer_token_frais():
    """Récupère le Token JWT via Playwright."""
    print("🤖 Lancement du navigateur pour la connexion automatique...")
    token_jwt = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        page = browser.new_page()

        def espionner_requetes(requete):
            nonlocal token_jwt
            if "apiSelenee" in requete.url:
                auth_header = requete.headers.get("authorization")
                if auth_header and "Bearer" in auth_header:
                    token_jwt = auth_header

        page.on("request", espionner_requetes)
        page.goto("https://awsolutions.fr/apr/")
        
        page.fill('input[name="username"]', EMAIL_AWS)
        page.fill('input[name="password"]', MOT_DE_PASSE_AWS)
        page.click('input[type="submit"], button[type="submit"]')
        
        page.wait_for_load_state("networkidle")
        browser.close()
        
    return token_jwt

def fetch_and_filter_aws(token):
    """Télécharge et filtre les données JSON."""
    url_api = "https://awsolutions.fr/apiSelenee/apiSearch/searchConsultations"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Authorization": token
    }
    
    appels_offres_pertinents = []
    page_actuelle = 0
    pages_totales = 1
    
    print("\n📡 Début de l'extraction sur AWS...")
    
    while page_actuelle < pages_totales:
        criteres_api = {"localisation_departement": CONFIG["departements"]}
        
        if CONFIG["uniquement_en_cours"]:
            maintenant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            criteres_api["minDateExp"] = [maintenant]
            
        payload = {
            "criteres": criteres_api,
            "page": page_actuelle,
            "isSchoolMode": False,
            "sortBy": {"datePub": True}
        }
        
        try:
            reponse = requests.post(url_api, json=payload, headers=headers)
            
            if reponse.status_code == 200:
                donnees = reponse.json()
                pages_totales = donnees.get("totalPages", 1)
                marches = donnees.get("content") or []
                
                for marche in marches:
                    titre = marche.get("objet") or ""
                    acheteur = marche.get("acheteurNom", "Inconnu")
                    lots = marche.get("lots") or []
                    
                    texte_a_analyser = titre.lower()
                    description_lots_texte = ""
                    
                    for lot in lots:
                        libelle_lot = lot.get("libelle") or ""
                        numero_lot = lot.get("numero", "?")
                        texte_a_analyser += f" {libelle_lot.lower()}"
                        description_lots_texte += f"Lot {numero_lot} : {libelle_lot}\n"
                    
                    # Filtre OU logique
                    est_pertinent = any(mot.lower() in texte_a_analyser for mot in CONFIG["mots_cles"])
                    
                    if est_pertinent:
                        statut_actuel = "Normal"
                        if marche.get("annule"):
                            statut_actuel = f"ANNULÉ le {marche.get('annule')[:10]}"
                        elif marche.get("rectifie"):
                            statut_actuel = f"RECTIFIÉ le {marche.get('rectifie')[:10]}"
                            
                        ao_structure = {
                            "reference": marche.get("referenceAWS"),
                            "statut": statut_actuel,
                            "titre": titre,
                            "acheteur": acheteur,
                            "description_lots": description_lots_texte.strip() if description_lots_texte else "Marché global (sans lots)",
                            "date_limite": marche.get("dateExp"),
                            "lien_dce": marche.get("urlAnnonce")
                        }
                        appels_offres_pertinents.append(ao_structure)
                        
                print(f"   ↳ Page {page_actuelle + 1}/{pages_totales} analysée.")
                page_actuelle += 1
                
            else:
                print(f"❌ Erreur API (Code {reponse.status_code}) : {reponse.text}")
                break
                
        except Exception as e:
            print(f"❌ Erreur réseau : {e}")
            break

    return appels_offres_pertinents

if __name__ == "__main__":
    mon_token = recuperer_token_frais()
    
    if mon_token:
        print("✅ Connexion réussie !")
        resultats = fetch_and_filter_aws(mon_token)
        
        print("\n" + "="*50)
        print("🎯 RÉSULTAT DE L'EXTRACTION")
        print("="*50)
        print(f"Total d'appels d'offres trouvés et filtrés : {len(resultats)}\n")
        
        if resultats:
            print("Exemple des 3 premiers marchés correspondants à tes critères :")
            # Modification ici : resultats[:3] prend les éléments de l'index 0 à 2
            print(json.dumps(resultats[:3], indent=4, ensure_ascii=False))