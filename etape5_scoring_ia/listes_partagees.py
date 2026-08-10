"""
listes_partagees.py — accès aux listes partagées Supabase (mots-clés, acheteurs suivis)
================================================================================

Les mots-clés et les acheteurs suivis doivent être identiques pour tout le
monde et pour tous les avis (voir recap.md) : ils ne sont donc plus des
constantes Python codées en dur, mais vivent dans deux tables Supabase
(`mots_cles`, `acheteurs_suivis` — voir schema.sql), avec une même forme
(une colonne texte unique + une date d'ajout). Ce module factorise l'accès
générique à ces deux tables, réutilisé par le site (app.py) et par le
pipeline de récupération (InsertIntoDataBase.py).
"""

from __future__ import annotations

from supabase import Client

TABLE_MOTS_CLES = "mots_cles"
COLONNE_MOTS_CLES = "mot"

TABLE_ACHETEURS = "acheteurs_suivis"
COLONNE_ACHETEURS = "nom"

# Liste partagée préparée pour un usage futur (pas encore branchée sur la
# construction des requêtes BOAMP/TED) — voir schema_v4_multiuser.sql.
TABLE_MOTS_CLES_LOTS = "mots_cles_lots"
COLONNE_MOTS_CLES_LOTS = "mot"


def charger_valeurs(client: Client, table: str, colonne: str) -> list[str]:
    """Renvoie la liste des valeurs d'une table partagée, triée alphabétiquement."""
    reponse = client.table(table).select(colonne).order(colonne).execute()
    return [ligne[colonne] for ligne in reponse.data]


def ajouter_valeur(client: Client, table: str, colonne: str, valeur: str) -> None:
    """Ajoute une valeur (ignore silencieusement si déjà présente, grâce à la
    contrainte UNIQUE + upsert sur la colonne)."""
    valeur = valeur.strip()
    if not valeur:
        return
    client.table(table).upsert({colonne: valeur}, on_conflict=colonne).execute()


def retirer_valeur(client: Client, table: str, colonne: str, valeur: str) -> None:
    """Retire une valeur de la table partagée."""
    client.table(table).delete().eq(colonne, valeur).execute()


# Raccourcis pratiques pour les deux listes de l'étape 4 -----------------

def charger_mots_cles(client: Client) -> list[str]:
    return charger_valeurs(client, TABLE_MOTS_CLES, COLONNE_MOTS_CLES)


def ajouter_mot_cle(client: Client, mot: str) -> None:
    ajouter_valeur(client, TABLE_MOTS_CLES, COLONNE_MOTS_CLES, mot)


def retirer_mot_cle(client: Client, mot: str) -> None:
    retirer_valeur(client, TABLE_MOTS_CLES, COLONNE_MOTS_CLES, mot)


def charger_acheteurs_suivis(client: Client) -> list[str]:
    return charger_valeurs(client, TABLE_ACHETEURS, COLONNE_ACHETEURS)


def ajouter_acheteur_suivi(client: Client, nom: str) -> None:
    ajouter_valeur(client, TABLE_ACHETEURS, COLONNE_ACHETEURS, nom)


def retirer_acheteur_suivi(client: Client, nom: str) -> None:
    retirer_valeur(client, TABLE_ACHETEURS, COLONNE_ACHETEURS, nom)


def charger_mots_cles_lots(client: Client) -> list[str]:
    return charger_valeurs(client, TABLE_MOTS_CLES_LOTS, COLONNE_MOTS_CLES_LOTS)


def ajouter_mot_cle_lot(client: Client, mot: str) -> None:
    ajouter_valeur(client, TABLE_MOTS_CLES_LOTS, COLONNE_MOTS_CLES_LOTS, mot)


def retirer_mot_cle_lot(client: Client, mot: str) -> None:
    retirer_valeur(client, TABLE_MOTS_CLES_LOTS, COLONNE_MOTS_CLES_LOTS, mot)
