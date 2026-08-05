"""
InsertIntoDataBase — Étape 2 : récupération BOAMP/TED + insertion Supabase
================================================================================

Enchaîne bout à bout :
  1. `recupDataBaseOfficial.recuperer_appels_offres(...)` (étape 1) — récupère
     et fusionne les appels d'offres BOAMP + TED.
  2. Insertion (upsert) des résultats dans la table Supabase `appels_offres`.

Configuration :
  - Recherche (mots-clés, départements...) : bloc CONFIGURATION ci-dessous,
    identique dans l'esprit à etape1_recupDataBaseOfficial/main.py.
  - Connexion Supabase : fichier `.env` dans ce même dossier (voir
    `.env.example` pour le modèle). Jamais commité — voir .gitignore.

Usage :
    python InsertIntoDataBase.py

Schéma de la table `appels_offres` visé (créée manuellement dans Supabase) :

    id                   uuid      (généré automatiquement, gen_random_uuid())
    identifiants         text      NOT NULL  <- identifiants du groupe, joints par "; "
    objet                text
    source               text      <- sources du groupe, jointes par "+" (ex. "BOAMP+TED")
    acheteur             text
    departement          ARRAY     <- ex. ["974", "976"]
    date_parution        date
    date_limite_reponse  date
    urls                 text      <- URLs du groupe, jointes par "; "
    nb_versions          smallint  NOT NULL
    score                real      <- mis à 0 pour l'instant (scoring IA pas encore implémenté)
    decision             text      default 'n/A' <- non renseigné ici, la valeur par défaut
                                       de la base s'applique (n/A / accepted / rejected / ...)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

from recupDataBaseOfficial import recuperer_appels_offres

# =====================================================================
# CONFIGURATION — recherche (identique dans l'esprit à l'étape 1)
# =====================================================================

KEYWORDS: list[str] = [
    "rénovation",
    "construction",
    "école",
    "collège",
    "lycée",
    "université",
    "laboratoire",
    "paillasse",
    "cuisine",
    "placard",
    "agencement",
    "mobilier",
]
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
    table `appels_offres` (les types Python liste ne correspondent pas tous à
    des colonnes ARRAY côté base : `source`/`identifiants`/`urls` sont des
    colonnes `text`, seul `departement` est un vrai ARRAY Postgres).
    """
    departement = [d.strip() for d in (resultat.get("departement") or "").split(",") if d.strip()]
    return {
        "identifiants": "; ".join(resultat["identifiants"]),
        "objet": resultat["objet"],
        "source": "+".join(resultat["source"]),
        "acheteur": resultat["acheteur"],
        "departement": departement,
        "date_parution": resultat["date_parution"] or None,
        "date_limite_reponse": resultat["date_limite_reponse"] or None,
        "urls": "; ".join(resultat["urls"]),
        "nb_versions": resultat["nb_versions"],
        # Scoring IA pas encore implémenté : on remplit à 0 pour l'instant.
        "score": 0,
        # "decision" volontairement omis : la colonne a un défaut ('n/A')
        # côté base, qui s'applique tant qu'on ne l'envoie pas explicitement.
    }


def inserer_resultats(client: Client, resultats: list[dict]) -> None:
    if not resultats:
        print("Aucun résultat à insérer.")
        return

    lignes = [formater_pour_supabase(r) for r in resultats]

    # upsert sur `identifiants` : si le même dossier est déjà en base (ré-
    # exécution du script), la ligne est mise à jour plutôt que dupliquée.
    # ⚠️ Nécessite une contrainte UNIQUE sur la colonne `identifiants` côté
    # Supabase pour fonctionner ; sans elle, Supabase refusera la clause
    # on_conflict (message d'erreur explicite dans ce cas).
    reponse = client.table(NOM_TABLE).upsert(lignes, on_conflict="identifiants").execute()
    print(f"[Supabase] {len(reponse.data)} ligne(s) insérée(s)/mise(s) à jour dans `{NOM_TABLE}`.")


# =====================================================================
# main
# =====================================================================

def main() -> None:
    print("=" * 70)
    print("Étape 2 — récupération BOAMP/TED puis insertion dans Supabase")
    print("=" * 70)

    resultats = recuperer_appels_offres(
        mots_cles=KEYWORDS,
        departements=DEPARTEMENTS,
        seulement_ouverts=SEULEMENT_OUVERTS,
        sources=SOURCES_ACTIVES,
        limit_par_source=LIMIT_PAR_SOURCE,
        dedupliquer=DEDUPLIQUER,
        verbeux=True,
    )
    print(f"[Récupération] {len(resultats)} appel(s) d'offre(s) récupéré(s) et fusionné(s).")

    client = charger_client_supabase()
    inserer_resultats(client, resultats)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrompu par l'utilisateur.")
