"""
profil_cible.py — profil de recherche écrit à la main (feature du modèle A)
================================================================================

Un texte décrivant ce que l'équipe recherche (ex. "travaux de rénovation
scolaire, plomberie sanitaire, laboratoires... à La Réunion et Mayotte"),
embeddé une seule fois et mis en cache dans la table `profil_cible`
(singleton, id=1). Le modèle A calcule la similarité entre chaque avis et ce
profil comme feature supplémentaire (`similarite_profil`) — voir
modele_preference.py.

Le texte du prompt vit dans son propre fichier, `profil_prompt.txt` (même
dossier), pour être modifiable sans toucher au code — actuellement rempli
d'un simple placeholder, à remplacer par la vraie description. Modifier ce
fichier puis relancer `charger_ou_calculer_profil` recalcule automatiquement
l'embedding (le cache Supabase n'est réutilisé que si le texte n'a pas changé).
"""

from __future__ import annotations

from pathlib import Path

from supabase import Client

TABLE_PROFIL = "profil_cible"
ID_PROFIL = 1

FICHIER_PROMPT = Path(__file__).parent / "profil_prompt.txt"


def charger_texte_profil() -> str:
    """Lit le texte du prompt depuis `profil_prompt.txt`."""
    return FICHIER_PROMPT.read_text(encoding="utf-8").strip()


def charger_ou_calculer_profil(client: Client, texte: str | None = None) -> list[float]:
    """Renvoie l'embedding du profil de recherche, en le recalculant
    uniquement si absent ou si le texte a changé depuis le dernier calcul.
    `texte` par défaut = contenu de `profil_prompt.txt`.
    """
    if texte is None:
        texte = charger_texte_profil()
    # Import différé : évite de dépendre du modèle d'embedding pour les
    # modules qui n'en ont pas besoin (cohérent avec features.py).
    from features import calculer_embedding, parser_vecteur

    reponse = client.table(TABLE_PROFIL).select("*").eq("id", ID_PROFIL).limit(1).execute()
    if reponse.data and reponse.data[0].get("texte") == texte and reponse.data[0].get("embedding"):
        return parser_vecteur(reponse.data[0]["embedding"])

    embedding = calculer_embedding(f"query: {texte}")
    client.table(TABLE_PROFIL).upsert({
        "id": ID_PROFIL,
        "texte": texte,
        "embedding": embedding,
    }, on_conflict="id").execute()
    return embedding
