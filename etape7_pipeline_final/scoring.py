"""
scoring.py — le score d'un avis, en une seule méthode : KNN par embedding
================================================================================

Principe : un avis est intéressant s'il ressemble (au sens de son embedding
sémantique) aux avis déjà acceptés par l'équipe, et pas aux avis déjà
rejetés. Concrètement :

  1. On calcule l'embedding sémantique de chaque avis (objet + acheteur +
     description + lots), mis en cache dans `appels_offres_features` (coûteux
     à calculer, jamais recalculé deux fois pour le même avis).
  2. Score = proportion de "like" parmi les k avis déjà swipés les plus
     proches de cet embedding (similarité cosinus, requête pgvector côté
     Supabase — voir `match_swipes_proches` dans schema.sql).

Tant qu'il n'y a pas assez de swipes enregistrés (`SEUIL_COLD_START`), le
KNN n'a rien à comparer d'utile : on retombe sur une heuristique simple
(nombre de mots-clés trouvés + délai restant avant la date limite).

Pas de modèle à entraîner, pas de pickle à sauvegarder : le KNN n'apprend
rien à l'avance, il compare à la volée — c'est ce qui rend `pipeline.py`
aussi simple (voir sa docstring).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache

from supabase import Client

TABLE_FEATURES = "appels_offres_features"

# En dessous de ce nombre total de swipes enregistrés (tous utilisateurs
# confondus), le KNN n'a pas assez d'exemples pour être fiable : on utilise
# l'heuristique mots-clés + délai à la place.
SEUIL_COLD_START = 30

NOM_MODELE_EMBEDDING = "intfloat/multilingual-e5-large"  # 1024 dimensions, 512 tokens max
MOTS_MAX_TEXTE = 350  # marge de sécurité sous la limite de tokens du modèle

# Poids de l'heuristique cold start (somme = 100 = score max théorique).
POIDS_MOTS_CLES = 60
POIDS_DELAI = 40
MOTS_CLES_MAX = 4   # au-delà, le critère mots-clés est considéré comme maximal
JOURS_MAX = 45      # au-delà, le critère délai est considéré comme maximal


# =====================================================================
# Texte (heuristique + embedding)
# =====================================================================

def _normaliser_texte(texte: str) -> str:
    texte = texte or ""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def _compter_mots_cles(texte: str, mots_cles: list[str]) -> int:
    texte_normalise = _normaliser_texte(texte)
    return sum(1 for mot in mots_cles if _normaliser_texte(mot) in texte_normalise)


def _texte_lots(lots: list[dict] | None) -> str:
    morceaux = []
    for lot in lots or []:
        if lot.get("titre"):
            morceaux.append(lot["titre"])
        if lot.get("description"):
            morceaux.append(lot["description"])
    return " ".join(morceaux)


def construire_texte_embedding(offre: dict) -> str:
    """Texte préparé pour l'embedding : objet + acheteur + description + lots."""
    objet = (offre.get("objet") or "").strip()
    acheteur = (offre.get("acheteur") or "").strip()
    description = (offre.get("description") or "").strip()
    texte_lots = _texte_lots(offre.get("lots")).strip()

    texte = objet
    if acheteur:
        texte += f". Acheteur : {acheteur}."
    if description:
        texte += f" {description}"
    if texte_lots:
        texte += f" Lots : {texte_lots}"

    mots = texte.split()
    if len(mots) > MOTS_MAX_TEXTE:
        texte = " ".join(mots[:MOTS_MAX_TEXTE])

    return f"query: {texte}"  # préfixe recommandé par e5 hors recherche pure


# =====================================================================
# Heuristique cold start (aucun embedding requis — rapide)
# =====================================================================

def _jours_restants(date_limite_reponse: str | None) -> int:
    if not date_limite_reponse:
        return 0
    try:
        limite = datetime.strptime(date_limite_reponse[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    return max(0, (limite - date.today()).days)


def calculer_score_heuristique(offre: dict, mots_cles: list[str], mots_cles_lots: list[str]) -> float:
    """Score 0-100 = mots-clés trouvés (objet + lots) + délai restant."""
    nb_mots = _compter_mots_cles(offre.get("objet") or "", mots_cles)
    nb_mots += _compter_mots_cles(_texte_lots(offre.get("lots")), mots_cles_lots)
    points_mots_cles = min(nb_mots, MOTS_CLES_MAX) / MOTS_CLES_MAX * POIDS_MOTS_CLES

    jours = _jours_restants(offre.get("date_limite_reponse"))
    points_delai = min(jours, JOURS_MAX) / JOURS_MAX * POIDS_DELAI

    return round(points_mots_cles + points_delai, 1)


# =====================================================================
# Embedding (calcul + cache Supabase)
# =====================================================================

@lru_cache(maxsize=1)
def _charger_modele_embedding():
    """Chargé une seule fois par process (import différé : les autres
    fonctions de ce module restent utilisables sans télécharger le modèle,
    ~2 Go au premier appel)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(NOM_MODELE_EMBEDDING, device="cpu")


def calculer_embedding(texte: str) -> list[float]:
    modele = _charger_modele_embedding()
    return modele.encode(texte, normalize_embeddings=True).tolist()


def similarite_cosinus(a: list[float] | None, b: list[float] | None) -> float:
    """Similarité cosinus entre deux embeddings (0 = rien à voir, 1 =
    identiques). Les embeddings de ce module sont déjà normalisés
    (`normalize_embeddings=True`), donc un simple produit scalaire suffit —
    robuste si l'un des deux est vide/absent. Fonction pure, aucune
    dépendance réseau : utilisée par `bac_a_sable_embedding.py` pour
    explorer un seuil de similarité (ex. pour repérer des doublons entre
    sources qui reformulent le même avis, voir sources/commun.py).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def _parser_vecteur(valeur: object) -> list[float] | None:
    """PostgREST renvoie parfois les colonnes `vector` en texte brut plutôt
    qu'en liste déjà parsée — sans cette normalisation, itérer dessus itère
    sur les CARACTÈRES de la chaîne, pas ses composantes."""
    if valeur is None:
        return None
    if isinstance(valeur, str):
        valeur = valeur.strip()
        return [float(x) for x in valeur.strip("[]").split(",")] if valeur else None
    return list(valeur)


def obtenir_embedding(client: Client, offre: dict) -> list[float]:
    """Embedding d'un avis, en réutilisant le cache si déjà calculé."""
    reponse = (
        client.table(TABLE_FEATURES)
        .select("embedding")
        .eq("appel_offre_id", offre["id"])
        .limit(1)
        .execute()
    )
    if reponse.data:
        vecteur = _parser_vecteur(reponse.data[0]["embedding"])
        if vecteur:
            return vecteur

    embedding = calculer_embedding(construire_texte_embedding(offre))
    client.table(TABLE_FEATURES).upsert(
        {"appel_offre_id": offre["id"], "embedding": embedding}, on_conflict="appel_offre_id"
    ).execute()
    return embedding


# =====================================================================
# KNN (similarité aux avis déjà swipés)
# =====================================================================

def knn_like_ratio(client: Client, embedding: list[float], appel_offre_id: str | None = None, k: int = 10) -> float:
    """Proportion de "like" parmi les k avis déjà swipés les plus proches de
    `embedding` (RPC `match_swipes_proches`, voir schema.sql). Renvoie 0.5
    (neutre) si aucun swipe n'existe encore.

    `appel_offre_id`, quand fourni, est exclu de ses propres voisins : sinon
    un avis déjà swipé se retrouverait son propre voisin à similarité 1.0 —
    fuite directe du résultat dans sa propre feature.
    """
    reponse = client.rpc(
        "match_swipes_proches", {"vecteur": embedding, "k": k, "exclu": appel_offre_id}
    ).execute()
    voisins = reponse.data or []
    if not voisins:
        return 0.5
    nb_like = sum(1 for v in voisins if v.get("decision") == "like")
    return nb_like / len(voisins)


# =====================================================================
# Score final
# =====================================================================

def score_offre(
    client: Client,
    offre: dict,
    mots_cles: list[str],
    mots_cles_lots: list[str],
    nb_swipes_total: int,
) -> float:
    """Score 0-100 d'un avis : heuristique tant que peu de swipes existent
    (cold start), sinon similarité KNN aux avis déjà swipés."""
    if nb_swipes_total < SEUIL_COLD_START:
        return calculer_score_heuristique(offre, mots_cles, mots_cles_lots)

    embedding = obtenir_embedding(client, offre)
    ratio = knn_like_ratio(client, embedding, appel_offre_id=offre.get("id"))
    return round(ratio * 100, 1)
