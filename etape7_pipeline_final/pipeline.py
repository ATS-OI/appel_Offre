"""
pipeline.py — orchestration : les 3 actions que le site peut déclencher
================================================================================

  - `lancer_recherche(...)` : interroge les 4 sources (sources/__init__.py),
    upsert les résultats dans `appels_offres`, puis calcule le score de tout
    le monde (`recalculer_scores`). Action LOURDE (réseau + embeddings).
  - `recalculer_scores(...)` : (re)calcule le score de tous les avis en base,
    sans réinterroger les sources. Action LOURDE (embeddings).
  - `enregistrer_swipe(...)` : enregistre une décision (accepter/rejeter).
    Action RAPIDE (3 écritures simples, AUCUN calcul) — c'est justement pour
    que swiper reste instantané que le scoring KNN ne "s'entraîne" pas :
    il compare à la volée aux swipes déjà enregistrés, il n'y a donc rien à
    recalculer au moment du swipe lui-même.

Sur Windows, la console par défaut (cp1252) plante sur un print() contenant
un emoji (message de progression) — `errors="replace"` évite le crash.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Callable

from supabase import Client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import db
import scoring
from sources import recuperer_toutes_les_offres

NOM_TABLE_OFFRES = "appels_offres"
NOM_TABLE_RAISONS = "raisons"
NOM_TABLE_SWIPES = "swipes"

DECISIONS_POSITIVES = {"accepted"}  # accepted -> like ; rejected -> dislike


def _annoncer(message: str, on_progress: Callable[[str], None] | None) -> None:
    print(message)
    if on_progress:
        on_progress(message)


def formater_pour_supabase(resultat: dict) -> dict:
    """Adapte un dict renvoyé par `recuperer_toutes_les_offres` au schéma de
    la table `appels_offres`. Le score n'est pas calculé ici (voir
    `recalculer_scores`, appelé juste après)."""
    departement = [d.strip() for d in (resultat.get("departement") or "").split(",") if d.strip()]
    return {
        "identifiants": "; ".join(resultat["identifiants"]),
        "objet": resultat["objet"],
        "description": resultat.get("description") or None,
        "lots": resultat.get("lots") or [],
        "source": "+".join(resultat["source"]),
        "acheteur": resultat["acheteur"],
        "departement": departement,
        "date_parution": resultat["date_parution"] or None,
        "date_limite_reponse": resultat["date_limite_reponse"] or None,
        "urls": "; ".join(resultat["urls"]),
        "nb_versions": resultat["nb_versions"],
        # mots-clés/lots/acheteur qui ont fait matcher cet avis au moment de
        # sa récupération — affiché sur la fiche (voir app.py) pour que
        # l'utilisateur sache pourquoi cet avis lui est proposé. Figé à la
        # récupération plutôt que recalculé à l'affichage : les listes de
        # mots-clés changent avec le temps, on garde une trace fidèle.
        "mots_cles_trouves": resultat.get("mots_cles_trouves") or [],
    }


def supprimer_offres_expirees(client: Client, on_progress: Callable[[str], None] | None = None) -> int:
    """Supprime les avis dont la date limite de réponse est dépassée — ils ne
    servent plus à rien une fois qu'on ne peut plus y répondre. Les avis sans
    date limite connue (ex. PLACE, voir sources/place.py) ne sont jamais
    supprimés par cette fonction (rien à comparer). La suppression entraîne
    aussi celle des lignes liées (features, swipes, raisons — `on delete
    cascade`, voir schema.sql) : c'est voulu, un avis expiré ne compte plus
    comme exemple pour le scoring KNN des avis encore ouverts.
    """
    aujourdhui = date.today().isoformat()
    expirees = (
        client.table(NOM_TABLE_OFFRES)
        .select("id")
        .lt("date_limite_reponse", aujourdhui)
        .execute()
        .data
    )
    if not expirees:
        return 0
    ids = [o["id"] for o in expirees]
    client.table(NOM_TABLE_OFFRES).delete().in_("id", ids).execute()
    _annoncer(f"🗑️ {len(ids)} avis expiré(s) supprimé(s) de la base.", on_progress)
    return len(ids)


def lancer_recherche(
    client: Client,
    departements: list[str],
    seulement_ouverts: bool,
    on_progress: Callable[[str], None] | None = None,
    on_erreur: Callable[[str, str], None] | None = None,
) -> list[dict]:
    """Pipeline complet : nettoyage des avis expirés + récupération (4
    sources) + insertion + scoring."""
    supprimer_offres_expirees(client, on_progress=on_progress)

    mots_cles = db.charger_mots_cles(client)
    mots_cles_lots = db.charger_mots_cles_lots(client)
    acheteurs = db.charger_acheteurs_suivis(client)

    resultats = recuperer_toutes_les_offres(
        departements, seulement_ouverts,
        mots_cles=mots_cles, mots_cles_lots=mots_cles_lots, acheteurs=acheteurs,
        on_progress=on_progress, on_erreur=on_erreur,
    )

    if not resultats:
        _annoncer("ℹ️ Aucun résultat à insérer.", on_progress)
        return []

    lignes = [formater_pour_supabase(r) for r in resultats]
    _annoncer(f"💾 Enregistrement de {len(lignes)} avis dans Supabase...", on_progress)
    client.table(NOM_TABLE_OFFRES).upsert(lignes, on_conflict="identifiants").execute()

    recalculer_scores(client, on_progress=on_progress)
    return resultats


def recalculer_scores(client: Client, on_progress: Callable[[str], None] | None = None) -> int:
    """(Re)calcule le score de tous les avis en base (mots-clés et nombre de
    swipes chargés une seule fois pour tout le lot)."""
    mots_cles = db.charger_mots_cles(client)
    mots_cles_lots = db.charger_mots_cles_lots(client)
    nb_swipes_total = len(client.table(NOM_TABLE_SWIPES).select("id").execute().data)

    offres = client.table(NOM_TABLE_OFFRES).select("*").execute().data
    total = len(offres)
    _annoncer(f"📊 Calcul des scores pour {total} avis "
               f"({'heuristique, cold start' if nb_swipes_total < scoring.SEUIL_COLD_START else 'KNN'})...",
               on_progress)

    for i, offre in enumerate(offres, 1):
        score = scoring.score_offre(client, offre, mots_cles, mots_cles_lots, nb_swipes_total)
        client.table(NOM_TABLE_OFFRES).update({"score": score}).eq("id", offre["id"]).execute()
        if i % 10 == 0 or i == total:
            _annoncer(f"📊 Score {i}/{total}...", on_progress)

    _annoncer(f"✅ {total} avis recalculé(s).", on_progress)
    return total


def enregistrer_swipe(client: Client, appel_offre_id: str, decision: str, commentaire: str, user_id: str) -> bool:
    """Enregistre une décision de swipe — RAPIDE : 3 écritures simples,
    aucun calcul (voir la docstring en tête de fichier).

    `swipes` est en upsert sur `(appel_offre_id, user_id)` : un même
    utilisateur ne compte jamais deux fois pour le même avis.

    Renvoie True si les 3 écritures ont réussi, False sinon (rien n'est
    alors enregistré).
    """
    aime = decision in DECISIONS_POSITIVES
    try:
        client.table(NOM_TABLE_OFFRES).update({"decision": decision}).eq("id", appel_offre_id).execute()
        client.table(NOM_TABLE_RAISONS).insert({
            "appel_offre_id": appel_offre_id,
            "decision": decision,
            "commentaire": commentaire or None,
            "user_id": user_id,
        }).execute()
        client.table(NOM_TABLE_SWIPES).upsert(
            {"appel_offre_id": appel_offre_id, "decision": "like" if aime else "dislike", "user_id": user_id},
            on_conflict="appel_offre_id,user_id",
        ).execute()
        return True
    except Exception as exc:
        print(f"[pipeline] Enregistrement du swipe échoué pour {appel_offre_id} : {exc}")
        return False
