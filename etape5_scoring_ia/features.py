"""
features.py — Étage 1 : extraction de features (figé, pas de fine-tuning)
================================================================================

Pour chaque appel d'offre, calcule :
  1. Un embedding sémantique (objet + acheteur) via multilingual-e5-large
     (sentence-transformers, CPU, ~1024 dimensions).
  2. Des features structurées : urgence, fraîcheur, correspondance
     mots-clés, département, source, longueur du texte.

Les résultats sont mis en cache dans la table `appels_offres_features`
(voir schema.sql) pour ne jamais recalculer deux fois le même avis.

⚠️ Le score de correspondance mots-clés (`score_mots_cles`) n'est ici QU'UNE
feature parmi d'autres en entrée du modèle (étage 2) — ce n'est plus, comme
aux étapes 3/4, le score final. Voir recap.md.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

from supabase import Client

from scoring import compter_mots_cles

TABLE_FEATURES = "appels_offres_features"


def parser_vecteur(valeur: object) -> list[float] | None:
    """Normalise une colonne pgvector lue depuis Supabase.

    L'API REST (PostgREST) renvoie parfois les colonnes `vector` en texte
    brut (ex. `"[0.012,0.045,...]"`) plutôt qu'en liste déjà parsée — sans
    cette normalisation, itérer dessus itère sur les CARACTÈRES de la
    chaîne au lieu de ses composantes, ce qui casse silencieusement le
    modèle (bug rencontré en pratique — voir recap.md).
    """
    if valeur is None:
        return None
    if isinstance(valeur, str):
        valeur = valeur.strip()
        if not valeur:
            return None
        return [float(x) for x in valeur.strip("[]").split(",")]
    return list(valeur)

# multilingual-e5-large : 1024 dimensions, limite de contexte 512 tokens.
NOM_MODELE_EMBEDDING = "intfloat/multilingual-e5-large"
DIMENSIONS_EMBEDDING = 1024

# Troncature préventive avant tokenisation : ~500 tokens ≈ 350-400 mots pour
# du texte français/anglais courant (marge de sécurité sous la limite de 512
# tokens du modèle, qui compte aussi le préfixe "query: " ajouté ci-dessous).
MOTS_MAX_TEXTE = 350


@lru_cache(maxsize=1)
def charger_modele_embedding():
    """Charge (une seule fois par process, mis en cache) le modèle d'embedding.

    Import de sentence-transformers différé dans la fonction pour que les
    autres fonctions de ce module (features structurées) restent utilisables
    sans dépendre du téléchargement du modèle (~2 Go au premier appel).
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(NOM_MODELE_EMBEDDING, device="cpu")


def construire_texte(offre: dict) -> str:
    """Concatène les champs textuels pertinents d'un avis (objet + acheteur +
    description longue si disponible), tronqués proprement.

    `offre` est un dict avec au moins `objet` et `acheteur` (schéma de la
    table `appels_offres` — voir etape2/recap.md). `description` (BOAMP
    best-effort / TED `description-lot`) est ajoutée quand disponible —
    c'est elle qui apporte le plus de substance à l'embedding, l'objet seul
    étant souvent court.
    """
    objet = (offre.get("objet") or "").strip()
    acheteur = (offre.get("acheteur") or "").strip()
    description = (offre.get("description") or "").strip()

    texte = objet
    if acheteur:
        texte += f". Acheteur : {acheteur}."
    if description:
        texte += f" {description}"

    mots = texte.split()
    if len(mots) > MOTS_MAX_TEXTE:
        texte = " ".join(mots[:MOTS_MAX_TEXTE])

    # Préfixe recommandé par le modèle e5 pour les tâches hors recherche
    # pure (classification/similarité) : "query: " de façon uniforme.
    return f"query: {texte}"


def calculer_embedding(texte: str) -> list[float]:
    """Calcule l'embedding (1024 dims) d'un texte déjà préparé (voir `construire_texte`)."""
    modele = charger_modele_embedding()
    vecteur = modele.encode(texte, normalize_embeddings=True)
    return vecteur.tolist()


def _jours_entre(date_str: str | None, depuis_aujourdhui: bool) -> int | None:
    """`depuis_aujourdhui=True` -> jours restants (date - aujourd'hui) ;
    `False` -> jours passés (aujourd'hui - date). None si date absente/invalide.
    """
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    aujourdhui = date.today()
    return (d - aujourdhui).days if depuis_aujourdhui else (aujourdhui - d).days


def calculer_features_structurees(offre: dict, mots_cles: list[str]) -> dict:
    """Calcule les features structurées (hors embedding) d'un avis.

    `offre` : dict issu de la table `appels_offres` (objet, acheteur,
    departement [ARRAY], source, date_parution, date_limite_reponse).
    """
    departement = offre.get("departement") or []
    if not isinstance(departement, list):
        departement = [departement]
    departement = [str(d) for d in departement]

    return {
        "jours_urgence": _jours_entre(offre.get("date_limite_reponse"), depuis_aujourdhui=True),
        "jours_fraicheur": _jours_entre(offre.get("date_parution"), depuis_aujourdhui=False),
        "score_mots_cles": float(compter_mots_cles(offre.get("objet") or "", mots_cles)),
        "departement": departement,
        "source": offre.get("source") or "",
        "longueur_objet": len(offre.get("objet") or ""),
        # Champs BOAMP officiels (voir recupDataBaseOfficial.py) — vides pour
        # les avis TED (pas d'équivalent collecté actuellement). Utilisés
        # uniquement par le modèle B (A/B test, voir modele_preference.py).
        "type_procedure": offre.get("type_procedure") or "",
        "nature_libelle": offre.get("nature_libelle") or "",
        "descripteur_libelle": offre.get("descripteur_libelle") or [],
    }


def similarite_cosinus(a: list[float] | None, b: list[float] | None) -> float:
    """Similarité cosinus entre deux vecteurs. Les embeddings de ce module
    sont déjà normalisés (`normalize_embeddings=True`), donc un simple
    produit scalaire suffit ; robuste si l'un des deux est vide/absent.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def knn_like_ratio(client: Client, embedding: list[float], k: int = 10) -> float:
    """Proportion de "like" parmi les k avis déjà swipés les plus proches de
    `embedding` (similarité cosinus via pgvector, fonction RPC
    `match_swipes_proches` — voir schema_v2_ab_test.sql). Renvoie 0.5
    (neutre) si aucun swipe n'existe encore.
    """
    reponse = client.rpc("match_swipes_proches", {"vecteur": embedding, "k": k}).execute()
    voisins = reponse.data or []
    if not voisins:
        return 0.5
    nb_like = sum(1 for v in voisins if v.get("decision") == "like")
    return nb_like / len(voisins)


def _charger_features_en_cache(client: Client, appel_offre_id: str) -> dict | None:
    reponse = (
        client.table(TABLE_FEATURES)
        .select("*")
        .eq("appel_offre_id", appel_offre_id)
        .limit(1)
        .execute()
    )
    if not reponse.data:
        return None
    ligne = reponse.data[0]
    ligne["embedding"] = parser_vecteur(ligne.get("embedding"))
    return ligne


def extraire_et_stocker_features(client: Client, offre: dict, mots_cles: list[str]) -> dict:
    """Renvoie les features (embedding + structurées) d'un avis, en réutilisant
    le cache `appels_offres_features` si déjà calculé, sinon en les calculant
    et en les stockant.
    """
    appel_offre_id = offre["id"]

    en_cache = _charger_features_en_cache(client, appel_offre_id)
    if en_cache is not None:
        return en_cache

    structurees = calculer_features_structurees(offre, mots_cles)
    texte = construire_texte(offre)
    embedding = calculer_embedding(texte)

    ligne = {
        "appel_offre_id": appel_offre_id,
        "embedding": embedding,
        **structurees,
    }
    client.table(TABLE_FEATURES).upsert(ligne, on_conflict="appel_offre_id").execute()
    return ligne
