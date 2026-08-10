"""
scoring.py — score heuristique "à priori" d'un appel d'offre (étape 3)
================================================================================

En attendant l'intégration d'une vraie IA de scoring, on utilise une
heuristique simple basée sur deux critères disponibles sans traitement
supplémentaire :

  1. Le nombre de mots-clés de recherche retrouvés dans l'objet de l'avis
     (plus il y en a, plus l'avis correspond précisément à ce qu'on cherche).
  2. Le temps restant avant la date limite de réponse (pour l'instant : plus
     il y a de temps, mieux c'est, pour avoir le temps de préparer une bonne
     offre — ce critère est volontairement simpliste et pourra être affiné).

Score final sur 100, combinaison pondérée des deux critères (poids et seuils
ajustables ci-dessous). À remplacer plus tard par un vrai scoring IA — ce
module ne fait que documenter/isoler la logique actuelle pour que ce
remplacement soit simple (une seule fonction à changer : `calculer_score`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

# Poids relatifs des deux critères (somme = 100 = score max théorique)
POIDS_MOTS_CLES = 60
POIDS_DELAI = 40

# Nombre de mots-clés distincts au-delà duquel le critère "mots-clés" est
# considéré comme maximal (pas de bonus supplémentaire au-delà).
MOTS_CLES_MAX = 4

# Nombre de jours restants au-delà duquel le critère "délai" est considéré
# comme maximal (pas de bonus supplémentaire au-delà).
JOURS_MAX = 45


def _normaliser_texte(texte: str) -> str:
    texte = texte or ""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()
    return texte


def compter_mots_cles(objet: str, mots_cles: list[str]) -> int:
    """Nombre de mots-clés (parmi la liste de recherche) présents dans l'objet de l'avis."""
    objet_normalise = _normaliser_texte(objet)
    return sum(1 for mot in mots_cles if _normaliser_texte(mot) in objet_normalise)


def jours_restants(date_limite_reponse: str, aujourdhui: date | None = None) -> int:
    """Nombre de jours entre aujourd'hui et la date limite (0 si passée/absente/invalide)."""
    if not date_limite_reponse:
        return 0
    try:
        limite = datetime.strptime(date_limite_reponse[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    aujourdhui = aujourdhui or date.today()
    return max(0, (limite - aujourdhui).days)


def calculer_score(objet: str, mots_cles: list[str], date_limite_reponse: str) -> float:
    """Score heuristique 0-100 combinant nombre de mots-clés trouvés et délai restant.

    ⚠️ Heuristique provisoire (pas d'IA) — voir l'en-tête de ce fichier.
    """
    nb_mots = compter_mots_cles(objet, mots_cles)
    points_mots_cles = min(nb_mots, MOTS_CLES_MAX) / MOTS_CLES_MAX * POIDS_MOTS_CLES

    jours = jours_restants(date_limite_reponse)
    points_delai = min(jours, JOURS_MAX) / JOURS_MAX * POIDS_DELAI

    return round(points_mots_cles + points_delai, 1)
