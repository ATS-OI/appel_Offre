"""
InsertIntoDataBase — Étape 5 : récupération BOAMP/TED + scoring IA + Supabase
================================================================================

Version étape 5 du pipeline (voir etape4_entrainement_heuristique) : le score
n'est plus calculé directement depuis les mots-clés (heuristique) — il est
maintenant calculé par le pipeline IA à 2 étages (`pipeline_scoring.py`) :
features (embedding + structurées) puis modèle de préférence partagé (ou
fallback heuristique tant que < 30 swipes au total, voir modele_preference.py).

Le champ `decision` n'est jamais touché ici : il est géré depuis le site.

Peut être utilisé :
  - en ligne de commande : `python InsertIntoDataBase.py`
  - importé depuis le site (app.py), via `lancer_recherche_et_insertion()`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

from listes_partagees import charger_acheteurs_suivis, charger_mots_cles
from modele_preference import charger_modele
from pipeline_scoring import predict_score
from profil_cible import charger_ou_calculer_profil
from recupDataBaseOfficial import recuperer_appels_offres

# =====================================================================
# CONFIGURATION — recherche
# =====================================================================

DEPARTEMENTS: list[str] = ["974", "976"]
SOURCES_ACTIVES: dict[str, bool] = {"boamp": True, "ted": True}
SEULEMENT_OUVERTS: bool = True
DEDUPLIQUER: bool = True
LIMIT_PAR_SOURCE: int = 100

NOM_TABLE = "appels_offres"


# =====================================================================
# Connexion Supabase
# =====================================================================

def charger_client_supabase() -> Client:
    load_dotenv(Path(__file__).parent / ".env")
    url = os.environ.get("SUPABASE_URL")
    cle = os.environ.get("SUPABASE_KEY")
    if not url or not cle:
        sys.exit(
            "SUPABASE_URL et/ou SUPABASE_KEY manquants.\n"
            "Copiez .env.example vers .env (dans ce même dossier) et remplissez vos identifiants Supabase."
        )
    return create_client(url, cle)


# =====================================================================
# Adaptation du format bibliothèque -> schéma de la table Supabase
# =====================================================================

def formater_pour_supabase(resultat: dict) -> dict:
    """Adapte un dict renvoyé par `recuperer_appels_offres()` au schéma de la
    table `appels_offres`. Le score n'est PAS calculé ici (voir
    `inserer_resultats` : il est calculé juste après l'upsert, avis par avis,
    par le pipeline IA — qui a besoin de l'`id` généré par Supabase).
    """
    departement = [d.strip() for d in (resultat.get("departement") or "").split(",") if d.strip()]
    return {
        "identifiants": "; ".join(resultat["identifiants"]),
        "objet": resultat["objet"],
        "description": resultat.get("description") or None,
        "source": "+".join(resultat["source"]),
        "acheteur": resultat["acheteur"],
        "departement": departement,
        "date_parution": resultat["date_parution"] or None,
        "date_limite_reponse": resultat["date_limite_reponse"] or None,
        "urls": "; ".join(resultat["urls"]),
        "nb_versions": resultat["nb_versions"],
        # "score" et "decision" volontairement omis :
        #  - score : calculé après coup par le pipeline IA (besoin de l'id).
        #  - decision : géré uniquement depuis le site (app.py).
    }


def inserer_resultats(client: Client, resultats: list[dict], mots_cles: list[str]) -> None:
    if not resultats:
        print("Aucun résultat à insérer.")
        return

    lignes = [formater_pour_supabase(r) for r in resultats]

    # upsert sur `identifiants` (nécessite la contrainte UNIQUE créée à
    # l'étape 2). "decision"/"score_modele_*" n'étant pas dans `lignes`,
    # Supabase ne touche pas ces colonnes pour les lignes déjà existantes.
    reponse = client.table(NOM_TABLE).upsert(lignes, on_conflict="identifiants").execute()
    lignes_upsertees = reponse.data
    print(f"[Supabase] {len(lignes_upsertees)} ligne(s) insérée(s)/mise(s) à jour dans `{NOM_TABLE}`.")

    # Les champs BOAMP officiels (type_procedure/nature_libelle/descripteur_libelle,
    # utilisés par le modèle B) ne sont pas des colonnes de `appels_offres` —
    # on les recolle depuis `resultats` (clé = identifiants, unique) pour le
    # premier calcul de features (voir features.py).
    par_identifiants = {"; ".join(r["identifiants"]): r for r in resultats}

    # Scoring IA : modèles + profil chargés une seule fois pour tout le lot
    # (évite un aller-retour Supabase par avis).
    etat_a = charger_modele(client, "A")
    etat_b = charger_modele(client, "B")
    profil_embedding = charger_ou_calculer_profil(client)
    fallback = etat_a["nb_swipes_vus"] < 30
    print(f"[Scoring IA] {'fallback heuristique' if fallback else 'modèles appris'} "
          f"({etat_a['nb_swipes_vus']} swipe(s) enregistré(s) au total).")

    for ligne in lignes_upsertees:
        source_boamp = par_identifiants.get(ligne["identifiants"], {})
        ligne_enrichie = {
            **ligne,
            "type_procedure": source_boamp.get("type_procedure") or "",
            "nature_libelle": source_boamp.get("nature_libelle") or "",
            "descripteur_libelle": source_boamp.get("descripteur_libelle") or [],
        }
        predict_score(client, ligne_enrichie, mots_cles, etat_a=etat_a, etat_b=etat_b, profil_embedding=profil_embedding)

    print(f"[Scoring IA] scores A/B calculés pour {len(lignes_upsertees)} avis.")


# =====================================================================
# Point d'entrée réutilisable (CLI + site)
# =====================================================================

def lancer_recherche_et_insertion(client: Client | None = None) -> list[dict]:
    """Exécute le pipeline complet (récupération + scoring IA + insertion) et
    renvoie les résultats. `client` peut être fourni (ex. par le site, pour
    réutiliser une connexion déjà ouverte) ; sinon il est créé ici.
    """
    client = client or charger_client_supabase()

    mots_cles = charger_mots_cles(client)
    acheteurs = charger_acheteurs_suivis(client)
    print(f"[Config] {len(mots_cles)} mot(s)-clé(s), {len(acheteurs)} acheteur(s) suivi(s) chargés depuis Supabase.")

    resultats = recuperer_appels_offres(
        mots_cles=mots_cles,
        departements=DEPARTEMENTS,
        seulement_ouverts=SEULEMENT_OUVERTS,
        sources=SOURCES_ACTIVES,
        limit_par_source=LIMIT_PAR_SOURCE,
        dedupliquer=DEDUPLIQUER,
        verbeux=True,
        acheteurs=acheteurs,
    )
    print(f"[Récupération] {len(resultats)} appel(s) d'offre(s) récupéré(s) et fusionné(s).")

    inserer_resultats(client, resultats, mots_cles)
    return resultats


def main() -> None:
    print("=" * 70)
    print("Étape 5 — récupération BOAMP/TED + scoring IA puis insertion dans Supabase")
    print("=" * 70)
    lancer_recherche_et_insertion()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrompu par l'utilisateur.")
