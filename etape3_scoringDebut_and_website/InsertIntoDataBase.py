"""
InsertIntoDataBase — Étape 3 : récupération BOAMP/TED + scoring + Supabase
================================================================================

Version étape 3 du pipeline (voir etape2_base_de_donnee) : reprend la même
logique de récupération + insertion, mais calcule désormais un vrai `score`
(heuristique, voir scoring.py) au lieu de la valeur fixe 0. Le champ
`decision`, lui, n'est jamais touché ici : il est géré depuis le site
(app.py), pas par ce script.

Peut être utilisé :
  - en ligne de commande : `python InsertIntoDataBase.py`
  - importé depuis le site (app.py), via `lancer_recherche_et_insertion()`,
    pour que le bouton "Lancer la recherche" déclenche exactement ce pipeline.

Configuration :
  - Recherche (mots-clés, départements...) : bloc CONFIGURATION ci-dessous.
  - Connexion Supabase : fichier `.env` dans ce même dossier (voir
    `.env.example` pour le modèle). Jamais commité — voir .gitignore.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

from recupDataBaseOfficial import recuperer_appels_offres
from scoring import calculer_score

# =====================================================================
# CONFIGURATION — recherche
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
    table `appels_offres`, en y ajoutant le score heuristique (étape 3).
    """
    departement = [d.strip() for d in (resultat.get("departement") or "").split(",") if d.strip()]
    score = calculer_score(
        objet=resultat["objet"],
        mots_cles=KEYWORDS,
        date_limite_reponse=resultat["date_limite_reponse"],
    )
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
        "score": score,
        # "decision" volontairement omis : géré uniquement depuis le site
        # (app.py) — un ré-upsert ici ne doit jamais écraser une décision
        # déjà prise par un utilisateur.
    }


def inserer_resultats(client: Client, resultats: list[dict]) -> None:
    if not resultats:
        print("Aucun résultat à insérer.")
        return

    lignes = [formater_pour_supabase(r) for r in resultats]

    # upsert sur `identifiants` (nécessite la contrainte UNIQUE créée à
    # l'étape 2). Comme "decision" n'est pas dans `lignes`, Supabase ne
    # touche pas cette colonne pour les lignes déjà existantes lors d'un
    # upsert (seules les colonnes envoyées sont mises à jour).
    reponse = client.table(NOM_TABLE).upsert(lignes, on_conflict="identifiants").execute()
    print(f"[Supabase] {len(reponse.data)} ligne(s) insérée(s)/mise(s) à jour dans `{NOM_TABLE}`.")


# =====================================================================
# Point d'entrée réutilisable (CLI + site)
# =====================================================================

def lancer_recherche_et_insertion(client: Client | None = None) -> list[dict]:
    """Exécute le pipeline complet (récupération + scoring + insertion) et
    renvoie les résultats. `client` peut être fourni (ex. par le site, pour
    réutiliser une connexion déjà ouverte) ; sinon il est créé ici.
    """
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

    client = client or charger_client_supabase()
    inserer_resultats(client, resultats)
    return resultats


def main() -> None:
    print("=" * 70)
    print("Étape 3 — récupération BOAMP/TED + scoring puis insertion dans Supabase")
    print("=" * 70)
    lancer_recherche_et_insertion()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrompu par l'utilisateur.")
