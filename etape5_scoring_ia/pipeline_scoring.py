"""
pipeline_scoring.py — orchestration du scoring IA (2 modèles A/B en parallèle)
================================================================================

Point d'entrée haut niveau utilisé par `InsertIntoDataBase.py` et `app.py` :
- `predict_score(...)` : calcule (ou relit du cache) les features d'un avis,
  les passe aux DEUX modèles de préférence partagés (A et B — voir
  modele_preference.py), écrit `score_modele_a`/`score_modele_b`.
- `update_model(...)` : enregistre un swipe (table `swipes`, pour les
  modèles) ET la décision "métier" (table `raisons`, avec commentaire —
  voir étape 4), puis entraîne LES DEUX modèles sur ce nouvel exemple et
  les sauvegarde.
- `recalculer_tous_les_scores(...)` : recalcule les deux scores de tous les
  avis en base (modèles chargés une seule fois pour tout le lot).
"""

from __future__ import annotations

from supabase import Client

from features import (
    existe_apprentissage_proche,
    extraire_et_stocker_features,
    knn_like_ratio,
    marquer_appris,
    similarite_cosinus,
)
from listes_partagees import charger_mots_cles, charger_mots_cles_lots
from modele_preference import (
    apprendre_a,
    apprendre_b,
    charger_modele,
    predire_proba_a,
    predire_proba_b,
    sauvegarder_modele,
)
from profil_cible import charger_ou_calculer_profil

NOM_TABLE_OFFRES = "appels_offres"
NOM_TABLE_RAISONS = "raisons"
NOM_TABLE_SWIPES = "swipes"

# accepted -> like ; rejected -> dislike (voir recap.md pour la distinction
# avec la table `raisons`, qui garde la décision d'origine). "rejected (for
# now)" a existé un temps côté UI mais a été retiré (décision inutile).
DECISIONS_POSITIVES = {"accepted"}


def _features_b_avec_signaux(client: Client, features: dict, etat_b: dict) -> dict:
    """Complète les features de l'étage 1 avec les signaux propres au modèle B
    (centroïdes, kNN), calculés à partir de l'état ACTUEL (avant tout
    apprentissage sur l'exemple en cours — évite toute fuite de la décision
    dans sa propre feature).

    L'avis lui-même est exclu de son propre calcul de kNN
    (`knn_like_ratio(..., appel_offre_id=...)`) : sans ça, un avis déjà
    swipé par un autre utilisateur (file de tri indépendante par
    utilisateur) se retrouverait son propre voisin à similarité 1.0, ce qui
    fuirait directement l'étiquette dans sa propre feature — voir recap.md
    "Anti-doublons / anti-fuite".
    """
    embedding = features.get("embedding") or []
    appel_offre_id = features.get("appel_offre_id")
    features_b = dict(features)
    features_b["sim_centroide_like"] = similarite_cosinus(embedding, etat_b.get("centroide_like"))
    features_b["sim_centroide_dislike"] = similarite_cosinus(embedding, etat_b.get("centroide_dislike"))
    if etat_b["nb_swipes_vus"] >= 30:  # inutile de payer l'appel RPC pendant le cold start
        features_b["knn_like_ratio"] = knn_like_ratio(client, embedding, appel_offre_id)
    else:
        features_b["knn_like_ratio"] = 0.5
    return features_b


def predict_score(
    client: Client,
    offre: dict,
    mots_cles: list[str],
    mots_cles_lots: list[str] | None = None,
    etat_a: dict | None = None,
    etat_b: dict | None = None,
    profil_embedding: list[float] | None = None,
) -> tuple[float, float]:
    """Calcule et enregistre les deux scores (A et B) d'un avis. `etat_a`/
    `etat_b`/`profil_embedding` peuvent être fournis (traitement par lot)
    pour éviter de recharger le modèle/profil à chaque appel.
    """
    if etat_a is None:
        etat_a = charger_modele(client, "A")
    if etat_b is None:
        etat_b = charger_modele(client, "B")
    if profil_embedding is None:
        profil_embedding = charger_ou_calculer_profil(client)

    features = extraire_et_stocker_features(client, offre, mots_cles, mots_cles_lots)
    features_b = _features_b_avec_signaux(client, features, etat_b)

    objet = offre.get("objet") or ""
    date_limite = offre.get("date_limite_reponse") or ""

    score_a = predire_proba_a(etat_a, features, profil_embedding, objet, mots_cles, date_limite)
    score_b = predire_proba_b(etat_b, features_b, objet, mots_cles, date_limite)

    client.table(NOM_TABLE_OFFRES).update({
        "score_modele_a": score_a,
        "score_modele_b": score_b,
    }).eq("id", offre["id"]).execute()

    return score_a, score_b


def update_model(
    client: Client,
    appel_offre_id: str,
    decision: str,
    commentaire: str = "",
    user_id: str = "anonyme",
) -> bool:
    """Enregistre une décision de swipe et entraîne les deux modèles partagés.

    `decision` est la décision "métier" telle qu'affichée sur le site
    (accepted / rejected — "rejected (for now)" a existé un temps mais a été
    retiré, décision jugée inutile) — binarisée ici en like/dislike pour la
    table `swipes` et l'entraînement, tout en gardant la décision d'origine
    + le commentaire dans `raisons` (étape 4).

    `user_id` identifie qui a swipé (texte libre saisi sur le site, pas
    d'authentification — voir recap.md). Chaque utilisateur a sa propre file
    de tri, indépendante des autres : `appels_offres.decision` est mis à jour
    à titre purement informatif (dernière décision connue, tous utilisateurs
    confondus) mais n'est PLUS ce qui détermine la file de qui que ce soit
    (voir `app.py::charger_offre_suivante`, basée sur `swipes` par `user_id`).

    Anti-doublons (voir recap.md "Anti-doublons / anti-fuite") :
    - `swipes` est en `upsert` sur `(appel_offre_id, user_id)` : un même
      utilisateur ne peut jamais compter deux fois pour le même avis (double
      clic, re-render Streamlit) — voir schema_v5_antidoublons.sql.
    - Un avis (ou un quasi-doublon détecté par similarité d'embedding) n'est
      appris par les modèles qu'UNE SEULE FOIS, quel que soit le nombre
      d'utilisateurs qui le swipent (`existe_apprentissage_proche` +
      `marquer_appris`) — sinon un même contenu pèserait plusieurs fois
      dans le centroïde/le gradient de la régression.

    Renvoie True si l'écriture du swipe et le traitement (entraînement
    effectif ou saut volontaire pour cause de doublon) se sont déroulés sans
    erreur, False si une exception a été levée pendant la partie IA (le
    swipe est dans tous les cas déjà enregistré avant que l'entraînement
    soit tenté — voir commentaire ci-dessous).
    """
    aime = decision in DECISIONS_POSITIVES

    # --- Écritures prioritaires : décision métier + swipe binaire ---
    # Faites AVANT tout calcul IA (embedding, modèles) pour ne JAMAIS perdre
    # une décision si l'entraînement plante ensuite (ex. souci d'environnement
    # ML — voir recap.md, "swipe toujours enregistré même si l'entraînement échoue").
    client.table(NOM_TABLE_OFFRES).update({"decision": decision}).eq("id", appel_offre_id).execute()

    client.table(NOM_TABLE_RAISONS).insert({
        "appel_offre_id": appel_offre_id,
        "decision": decision,
        "commentaire": commentaire or None,
        "user_id": user_id,
    }).execute()

    # upsert (pas insert) : protège contre un double clic/re-render qui
    # enverrait deux fois le même (appel_offre_id, user_id) — voir
    # schema_v5_antidoublons.sql (contrainte UNIQUE correspondante).
    client.table(NOM_TABLE_SWIPES).upsert(
        {
            "appel_offre_id": appel_offre_id,
            "decision": "like" if aime else "dislike",
            "user_id": user_id,
        },
        on_conflict="appel_offre_id,user_id",
    ).execute()

    # --- Apprentissage des deux modèles (best-effort) ---
    # États chargés APRÈS les écritures ci-dessus (le swipe qu'on vient
    # d'enregistrer n'a pas encore été appris, donc les signaux du modèle B
    # reflètent bien l'historique précédent cet exemple, pas de fuite).
    try:
        mots_cles = charger_mots_cles(client)
        mots_cles_lots = charger_mots_cles_lots(client)
        offre = client.table(NOM_TABLE_OFFRES).select("*").eq("id", appel_offre_id).single().execute().data
        features = extraire_et_stocker_features(client, offre, mots_cles, mots_cles_lots)
        embedding = features.get("embedding") or []

        # Cet avis (ou un quasi-doublon de contenu) a-t-il déjà servi à
        # entraîner les modèles ? Si oui, on ne le réapprend pas (voir
        # docstring ci-dessus) — le swipe reste enregistré dans
        # `swipes`/`raisons`, juste pas réinjecté dans les modèles.
        if existe_apprentissage_proche(client, embedding):
            return True

        etat_a = charger_modele(client, "A")
        etat_b = charger_modele(client, "B")
        profil_embedding = charger_ou_calculer_profil(client)
        features_b = _features_b_avec_signaux(client, features, etat_b)

        apprendre_a(etat_a, features, profil_embedding, aime)
        sauvegarder_modele(client, "A", etat_a)

        apprendre_b(etat_b, features_b, embedding, aime)
        sauvegarder_modele(client, "B", etat_b)

        marquer_appris(client, appel_offre_id)
        return True
    except Exception as exc:
        # Le swipe est déjà en base (raisons + swipes) à ce stade — on ne le
        # perd pas même si l'entraînement plante (ex. souci d'environnement
        # ML côté embedding). Erreur journalisée côté serveur ; l'appelant
        # (app.py) décide comment l'afficher, sans bloquer le swipe suivant.
        import traceback
        print(f"[pipeline_scoring] Entraînement du modèle échoué pour {appel_offre_id} : {exc}")
        traceback.print_exc()
        return False


def recalculer_tous_les_scores(client: Client) -> int:
    """Recalcule les deux scores de tous les avis en base, avec les
    mots-clés et les modèles actuels (chargés une seule fois pour tout le lot).
    """
    mots_cles = charger_mots_cles(client)
    mots_cles_lots = charger_mots_cles_lots(client)
    etat_a = charger_modele(client, "A")
    etat_b = charger_modele(client, "B")
    profil_embedding = charger_ou_calculer_profil(client)

    offres = client.table(NOM_TABLE_OFFRES).select("*").execute().data
    for offre in offres:
        predict_score(
            client, offre, mots_cles, mots_cles_lots,
            etat_a=etat_a, etat_b=etat_b, profil_embedding=profil_embedding,
        )

    return len(offres)
