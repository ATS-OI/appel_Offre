"""
modele_preference.py — Étage 2 : deux modèles de préférence en ligne (A/B test)
================================================================================

Deux modèles de classification binaire (like/dislike), partagés par toute
l'équipe (pas de modèle par utilisateur), entraînés en parallèle sur les
mêmes swipes, pour comparer deux architectures de features :

- **Modèle A ("classique + prompt")** : embedding brut (1024 dims) +
  features structurées + similarité à un profil de recherche écrit à la
  main (voir profil_cible.py).
- **Modèle B ("features enrichies")** : PAS d'embedding brut (trop de
  dimensions pour le volume de swipes disponible) — à la place, similarité
  aux centroïdes like/dislike, ratio de "like" parmi les k plus proches
  voisins déjà swipés (kNN pgvector), et les champs BOAMP officiels
  (type_procedure, nature_libelle, descripteur_libelle) + les mêmes
  features structurées que le modèle A.

Chaque modèle est volontairement une régression logistique linéaire
(interprétable), précédée d'un OneHotEncoder (catégorielles) et d'un
StandardScaler (numériques). Persistance dans Supabase
(`modele_preference_etat`, une ligne par modèle, pickle encodé en base64).

Cold start : tant que moins de `SEUIL_COLD_START` swipes n'ont été
enregistrés, les deux modèles retombent sur l'heuristique mots-clés + délai
(`scoring.calculer_score`, inchangée depuis l'étape 3/4).
"""

from __future__ import annotations

import base64
import pickle

from supabase import Client

from features import parser_vecteur, similarite_cosinus
from scoring import calculer_score

TABLE_MODELE = "modele_preference_etat"
SEUIL_COLD_START = 30

NB_DIMENSIONS_EMBEDDING = 1024

# --- Modèle A : embedding brut + structurées + similarité profil ---------
CATEGORIELLES_A = ["source", "departement"]
NUMERIQUES_A = [
    "jours_urgence", "jours_fraicheur", "score_mots_cles", "score_mots_cles_lots",
    "nb_lots", "longueur_objet", "similarite_profil",
] + [f"emb_{i}" for i in range(NB_DIMENSIONS_EMBEDDING)]

# --- Modèle B : sans embedding brut, features enrichies -------------------
CATEGORIELLES_B = ["source", "departement", "type_procedure", "nature_libelle", "descripteur_libelle"]
NUMERIQUES_B = [
    "jours_urgence", "jours_fraicheur", "score_mots_cles", "score_mots_cles_lots",
    "nb_lots", "longueur_objet",
    "sim_centroide_like", "sim_centroide_dislike", "knn_like_ratio",
]


def _construire_pipeline(categorielles: list[str], numeriques: list[str]):
    from river import compose, linear_model, preprocessing

    cat = compose.Select(*categorielles) | preprocessing.OneHotEncoder()
    num = compose.Select(*numeriques) | preprocessing.StandardScaler()
    return (cat + num) | linear_model.LogisticRegression()


def construire_pipeline_a():
    return _construire_pipeline(CATEGORIELLES_A, NUMERIQUES_A)


def construire_pipeline_b():
    return _construire_pipeline(CATEGORIELLES_B, NUMERIQUES_B)


def _categorie(valeurs: list[str] | None) -> str:
    """Joint une liste de valeurs catégorielles (ex. département, descripteurs)
    en une seule catégorie textuelle triée — voir recap.md pour le compromis
    (combos traités comme catégories à part entière, pas explosés en indicateurs).
    """
    return ",".join(sorted(valeurs)) if valeurs else "inconnu"


def vectoriser_a(features: dict, profil_embedding: list[float]) -> dict:
    """Construit le `x` nommé du modèle A : embedding éclaté + structurées + similarité profil."""
    embedding = features.get("embedding") or []
    x = {f"emb_{i}": valeur for i, valeur in enumerate(embedding)}
    x.update({
        "jours_urgence": features.get("jours_urgence") or 0,
        "jours_fraicheur": features.get("jours_fraicheur") or 0,
        "score_mots_cles": features.get("score_mots_cles") or 0.0,
        "score_mots_cles_lots": features.get("score_mots_cles_lots") or 0.0,
        "nb_lots": features.get("nb_lots") or 0,
        "longueur_objet": features.get("longueur_objet") or 0,
        "source": features.get("source") or "inconnue",
        "departement": _categorie(features.get("departement")),
        "similarite_profil": similarite_cosinus(embedding, profil_embedding),
    })
    return x


def vectoriser_b(features_b: dict) -> dict:
    """Construit le `x` nommé du modèle B : PAS d'embedding brut, features enrichies."""
    return {
        "jours_urgence": features_b.get("jours_urgence") or 0,
        "jours_fraicheur": features_b.get("jours_fraicheur") or 0,
        "score_mots_cles": features_b.get("score_mots_cles") or 0.0,
        "score_mots_cles_lots": features_b.get("score_mots_cles_lots") or 0.0,
        "nb_lots": features_b.get("nb_lots") or 0,
        "longueur_objet": features_b.get("longueur_objet") or 0,
        "source": features_b.get("source") or "inconnue",
        "departement": _categorie(features_b.get("departement")),
        "type_procedure": features_b.get("type_procedure") or "inconnu",
        "nature_libelle": features_b.get("nature_libelle") or "inconnu",
        "descripteur_libelle": _categorie(features_b.get("descripteur_libelle")),
        "sim_centroide_like": features_b.get("sim_centroide_like") or 0.0,
        "sim_centroide_dislike": features_b.get("sim_centroide_dislike") or 0.0,
        "knn_like_ratio": features_b.get("knn_like_ratio", 0.5),
    }


# =====================================================================
# Persistance (une ligne par modèle : nom_modele = 'A' ou 'B')
# =====================================================================

def charger_modele(client: Client, nom_modele: str) -> dict:
    """Charge l'état complet d'un modèle ('A' ou 'B') depuis Supabase, ou en
    construit un neuf si absent. Renvoie un dict :
        {"modele", "nb_swipes_vus", "centroide_like", "centroide_dislike", "nb_like", "nb_dislike"}
    (les champs centroïdes/nb_like/nb_dislike ne concernent que le modèle B).
    """
    reponse = client.table(TABLE_MODELE).select("*").eq("nom_modele", nom_modele).limit(1).execute()
    if reponse.data and reponse.data[0].get("pickle_b64"):
        ligne = reponse.data[0]
        modele = pickle.loads(base64.b64decode(ligne["pickle_b64"]))
        return {
            "modele": modele,
            "nb_swipes_vus": ligne.get("nb_swipes_vus") or 0,
            "centroide_like": parser_vecteur(ligne.get("centroide_like")),
            "centroide_dislike": parser_vecteur(ligne.get("centroide_dislike")),
            "nb_like": ligne.get("nb_like") or 0,
            "nb_dislike": ligne.get("nb_dislike") or 0,
        }

    modele_neuf = construire_pipeline_a() if nom_modele == "A" else construire_pipeline_b()
    return {
        "modele": modele_neuf,
        "nb_swipes_vus": 0,
        "centroide_like": None,
        "centroide_dislike": None,
        "nb_like": 0,
        "nb_dislike": 0,
    }


def sauvegarder_modele(client: Client, nom_modele: str, etat: dict) -> None:
    """Sérialise et sauvegarde l'état complet d'un modèle dans Supabase."""
    pickle_b64 = base64.b64encode(pickle.dumps(etat["modele"])).decode("ascii")
    ligne = {
        "nom_modele": nom_modele,
        "pickle_b64": pickle_b64,
        "nb_swipes_vus": etat["nb_swipes_vus"],
        "nb_like": etat.get("nb_like") or 0,
        "nb_dislike": etat.get("nb_dislike") or 0,
    }
    if etat.get("centroide_like") is not None:
        ligne["centroide_like"] = etat["centroide_like"]
    if etat.get("centroide_dislike") is not None:
        ligne["centroide_dislike"] = etat["centroide_dislike"]
    client.table(TABLE_MODELE).upsert(ligne, on_conflict="nom_modele").execute()


def mettre_a_jour_centroide(centroide: list[float] | None, n: int, nouveau_vecteur: list[float]) -> tuple[list[float], int]:
    """Moyenne incrémentale numériquement stable : centroide += (x - centroide) / n_nouveau."""
    n_nouveau = n + 1
    if centroide is None:
        return list(nouveau_vecteur), n_nouveau
    return [c + (x - c) / n_nouveau for c, x in zip(centroide, nouveau_vecteur)], n_nouveau


# =====================================================================
# Prédiction / apprentissage
# =====================================================================

def predire_proba_a(
    etat: dict,
    features: dict,
    profil_embedding: list[float],
    objet: str,
    mots_cles: list[str],
    date_limite_reponse: str,
) -> float:
    if etat["nb_swipes_vus"] < SEUIL_COLD_START:
        return calculer_score(objet, mots_cles, date_limite_reponse)
    x = vectoriser_a(features, profil_embedding)
    probas = etat["modele"].predict_proba_one(x)
    return round(probas.get(True, 0.0) * 100, 1)


def predire_proba_b(
    etat: dict,
    features_b: dict,
    objet: str,
    mots_cles: list[str],
    date_limite_reponse: str,
) -> float:
    if etat["nb_swipes_vus"] < SEUIL_COLD_START:
        return calculer_score(objet, mots_cles, date_limite_reponse)
    x = vectoriser_b(features_b)
    probas = etat["modele"].predict_proba_one(x)
    return round(probas.get(True, 0.0) * 100, 1)


def apprendre_a(etat: dict, features: dict, profil_embedding: list[float], aime: bool) -> None:
    x = vectoriser_a(features, profil_embedding)
    etat["modele"].learn_one(x, aime)
    etat["nb_swipes_vus"] += 1


def apprendre_b(etat: dict, features_b: dict, embedding: list[float], aime: bool) -> None:
    x = vectoriser_b(features_b)
    etat["modele"].learn_one(x, aime)
    etat["nb_swipes_vus"] += 1
    if aime:
        etat["centroide_like"], etat["nb_like"] = mettre_a_jour_centroide(
            etat.get("centroide_like"), etat.get("nb_like") or 0, embedding
        )
    else:
        etat["centroide_dislike"], etat["nb_dislike"] = mettre_a_jour_centroide(
            etat.get("centroide_dislike"), etat.get("nb_dislike") or 0, embedding
        )


def poids_actuels(modele: object) -> dict:
    """Expose au mieux les poids de la régression logistique finale du
    pipeline, pour afficher "pourquoi ce score" (nom de feature -> poids
    appris). Best-effort : dépend de la structure interne de River.
    """
    try:
        dernieres_etapes = list(modele.steps.values())
        regression = dernieres_etapes[-1]
        return dict(regression.weights)
    except (AttributeError, IndexError):
        return {}
