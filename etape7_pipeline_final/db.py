"""
db.py — connexion Supabase + listes partagées (mots-clés / mots-clés lots / acheteurs suivis)
================================================================================

Un seul endroit pour tout ce qui parle directement à Supabase en dehors du
scoring (scoring.py) et de l'orchestration (pipeline.py) : charger le
client, et lire/écrire les 3 listes partagées qui pilotent la recherche
(identiques pour tout le monde, voir schema.sql).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

TABLE_MOTS_CLES = "mots_cles"
TABLE_MOTS_CLES_LOTS = "mots_cles_lots"
TABLE_ACHETEURS = "acheteurs_suivis"


def charger_client() -> Client:
    load_dotenv(Path(__file__).parent / ".env")
    url = os.environ.get("SUPABASE_URL")
    cle = os.environ.get("SUPABASE_KEY")
    if not url or not cle:
        sys.exit(
            "SUPABASE_URL et/ou SUPABASE_KEY manquants.\n"
            "Copiez .env.example vers .env (dans ce dossier) et remplissez vos identifiants Supabase."
        )
    return create_client(url, cle)


def _charger_valeurs(client: Client, table: str, colonne: str) -> list[str]:
    reponse = client.table(table).select(colonne).order(colonne).execute()
    return [ligne[colonne] for ligne in reponse.data]


def _ajouter_valeur(client: Client, table: str, colonne: str, valeur: str) -> None:
    valeur = valeur.strip()
    if not valeur:
        return
    client.table(table).upsert({colonne: valeur}, on_conflict=colonne).execute()


def _retirer_valeur(client: Client, table: str, colonne: str, valeur: str) -> None:
    client.table(table).delete().eq(colonne, valeur).execute()


# --- Mots-clés (objet) ------------------------------------------------------

def charger_mots_cles(client: Client) -> list[str]:
    return _charger_valeurs(client, TABLE_MOTS_CLES, "mot")


def ajouter_mot_cle(client: Client, mot: str) -> None:
    _ajouter_valeur(client, TABLE_MOTS_CLES, "mot", mot)


def retirer_mot_cle(client: Client, mot: str) -> None:
    _retirer_valeur(client, TABLE_MOTS_CLES, "mot", mot)


# --- Mots-clés lots ----------------------------------------------------------

def charger_mots_cles_lots(client: Client) -> list[str]:
    return _charger_valeurs(client, TABLE_MOTS_CLES_LOTS, "mot")


def ajouter_mot_cle_lot(client: Client, mot: str) -> None:
    _ajouter_valeur(client, TABLE_MOTS_CLES_LOTS, "mot", mot)


def retirer_mot_cle_lot(client: Client, mot: str) -> None:
    _retirer_valeur(client, TABLE_MOTS_CLES_LOTS, "mot", mot)


# --- Acheteurs suivis ---------------------------------------------------------

def charger_acheteurs_suivis(client: Client) -> list[str]:
    return _charger_valeurs(client, TABLE_ACHETEURS, "nom")


def ajouter_acheteur_suivi(client: Client, nom: str) -> None:
    _ajouter_valeur(client, TABLE_ACHETEURS, "nom", nom)


def retirer_acheteur_suivi(client: Client, nom: str) -> None:
    _retirer_valeur(client, TABLE_ACHETEURS, "nom", nom)
