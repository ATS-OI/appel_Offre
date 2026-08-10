"""
InsertIntoDataBase — Étape 4 : récupération BOAMP/TED + scoring + Supabase
================================================================================

Version étape 4 du pipeline (voir etape3_scoringDebut_and_website) : les
mots-clés et les acheteurs suivis ne sont plus des constantes Python codées
en dur — ils sont chargés depuis les tables Supabase `mots_cles` et
`acheteurs_suivis` (voir schema.sql et listes_partagees.py), pour être
identiques pour tout le monde et pour tous les avis, et modifiables depuis
le site (app.py) sans toucher au code.

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
from recupDataBaseOfficial import recuperer_appels_offres
from scoring import calculer_score

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

def formater_pour_supabase(resultat: dict, mots_cles: list[str]) -> dict:
    """Adapte un dict renvoyé par `recuperer_appels_offres()` au schéma de la
    table `appels_offres`, en y ajoutant le score heuristique.

    Le score ne compte que les mots-clés (objet) — pas les acheteurs suivis,
    qui servent uniquement à élargir la collecte (voir recap.md).
    """
    departement = [d.strip() for d in (resultat.get("departement") or "").split(",") if d.strip()]
    score = calculer_score(
        objet=resultat["objet"],
        mots_cles=mots_cles,
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


def inserer_resultats(client: Client, resultats: list[dict], mots_cles: list[str]) -> None:
    if not resultats:
        print("Aucun résultat à insérer.")
        return

    lignes = [formater_pour_supabase(r, mots_cles) for r in resultats]

    # upsert sur `identifiants` (nécessite la contrainte UNIQUE créée à
    # l'étape 2). "decision" n'étant pas dans `lignes`, Supabase ne touche
    # pas cette colonne pour les lignes déjà existantes lors d'un upsert.
    reponse = client.table(NOM_TABLE).upsert(lignes, on_conflict="identifiants").execute()
    print(f"[Supabase] {len(reponse.data)} ligne(s) insérée(s)/mise(s) à jour dans `{NOM_TABLE}`.")


def recalculer_scores(client: Client) -> int:
    """Recalcule et met à jour `score` pour toutes les lignes déjà en base,
    avec les mots-clés actuels — sans réinterroger BOAMP/TED (rapide, local).
    Utile juste après avoir ajouté/retiré un mot-clé, pour voir l'effet
    immédiatement sans relancer une recherche complète.
    """
    mots_cles = charger_mots_cles(client)
    lignes = client.table(NOM_TABLE).select("id, objet, date_limite_reponse").execute().data

    nb_maj = 0
    for ligne in lignes:
        nouveau_score = calculer_score(
            objet=ligne.get("objet") or "",
            mots_cles=mots_cles,
            date_limite_reponse=ligne.get("date_limite_reponse") or "",
        )
        client.table(NOM_TABLE).update({"score": nouveau_score}).eq("id", ligne["id"]).execute()
        nb_maj += 1

    print(f"[Supabase] {nb_maj} score(s) recalculé(s).")
    return nb_maj


# =====================================================================
# Point d'entrée réutilisable (CLI + site)
# =====================================================================

def lancer_recherche_et_insertion(client: Client | None = None) -> list[dict]:
    """Exécute le pipeline complet (récupération + scoring + insertion) et
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
    print("Étape 4 — récupération BOAMP/TED + scoring puis insertion dans Supabase")
    print("=" * 70)
    lancer_recherche_et_insertion()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrompu par l'utilisateur.")
