"""
sources/__init__.py — point d'entrée unique des 6 sources de données
================================================================================

`recuperer_toutes_les_offres(...)` appelle BOAMP, TED, AWS, PLACE, EMP
(e-marchespublics) et ACHAT_PUBLIC à la suite, chacune protégée
individuellement : si l'une plante (site changé, identifiants manquants,
réseau...), elle est signalée via `on_erreur` et les 5 autres continuent
normalement — une panne d'une source ne doit jamais empêcher de récupérer
les autres.

Une fois toutes les sources récupérées : fusion des doublons (un même
marché republié/rectifié, y compris à cheval entre deux sources — voir
commun.py ; e-marchespublics agrège lui-même BOAMP/JOUE, donc ce cas est
particulièrement attendu entre EMP et BOAMP/TED) puis filtre mots-clés/lots/
acheteurs suivis.
"""

from __future__ import annotations

from typing import Callable

from . import achat_public, aws_solutions, boamp, e_marche, place, ted
from .commun import fusionner_doublons, trouver_correspondances

# (nom affiché, fonction `recuperer`, nom de fichier pour le message d'erreur)
_SOURCES = [
    ("BOAMP", boamp.recuperer, "boamp.py"),
    ("TED", ted.recuperer, "ted.py"),
    ("AWS", aws_solutions.recuperer, "aws_solutions.py"),
    ("PLACE", place.recuperer, "place.py"),
    ("EMP", e_marche.recuperer, "e_marche.py"),
    ("ACHAT_PUBLIC", achat_public.recuperer, "achat_public.py"),
]


# Longueur max du détail technique affiché à l'utilisateur (encadré rouge/
# fenêtre de progression) — certaines erreurs (ex. Playwright qui recopie
# tout le log de démarrage du navigateur) font plusieurs dizaines de lignes ;
# le détail complet reste dans la console (`print`), seul l'affichage
# utilisateur est raccourci.
LONGUEUR_MAX_DETAIL = 200


def _tronquer(texte: str, longueur_max: int = LONGUEUR_MAX_DETAIL) -> str:
    texte = str(texte).strip()
    if len(texte) <= longueur_max:
        return texte
    return texte[:longueur_max].rstrip() + "…"


def message_erreur_source(nom: str, fichier: str, exc: Exception) -> str:
    """Texte affiché à l'utilisateur (encadré rouge côté app.py) quand une
    source échoue — ne bloque jamais les autres sources."""
    return (
        f"⚠️ Attention, il y a eu une erreur dans la récupération des données {nom} : "
        f"il se pourrait que le site ait changé ou qu'une erreur de connexion à la page "
        f"soit survenue. Allez dans le fichier `sources/{fichier}` ou contactez un "
        f"administrateur pour avoir plus de détails sur le problème. "
        f"(Détail technique : {_tronquer(exc)})"
    )


def recuperer_toutes_les_offres(
    departements: list[str],
    seulement_ouverts: bool = True,
    mots_cles: list[str] | None = None,
    mots_cles_lots: list[str] | None = None,
    acheteurs: list[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_erreur: Callable[[str, str], None] | None = None,
) -> list[dict]:
    """Récupère, fusionne et filtre les avis des 6 sources.

    `on_progress(message)` : progression textuelle (voir app.py, fenêtre de
    chargement). `on_erreur(nom_source, message)` : appelé une fois par
    source en échec, sans interrompre les autres.
    """
    mots_cles = mots_cles or []
    mots_cles_lots = mots_cles_lots or []
    acheteurs = acheteurs or []

    def annoncer(message: str) -> None:
        print(message)
        if on_progress:
            on_progress(message)

    tous_resultats: list[dict] = []
    for nom, fonction, fichier in _SOURCES:
        annoncer(f"📡 Interrogation de {nom}...")
        try:
            resultats = fonction(departements, seulement_ouverts)
        except Exception as exc:
            print(f"❌ {nom} a échoué : {exc}")  # détail complet réservé à la console
            if on_progress:
                on_progress(f"❌ {nom} a échoué : {_tronquer(exc)}")
            if on_erreur:
                on_erreur(nom, message_erreur_source(nom, fichier, exc))
            continue
        annoncer(f"✅ {nom} : {len(resultats)} avis brut(s) récupéré(s).")
        tous_resultats.extend(resultats)

    annoncer("🔀 Fusion des doublons (rectificatifs, republications, recoupements entre sources)...")
    liens_precis = {
        i: r["_annonce_lie"] for i, r in enumerate(tous_resultats) if r.get("_annonce_lie")
    }
    fusionnes = fusionner_doublons(tous_resultats, liens_precis)

    annoncer("🔎 Filtrage mots-clés / lots / acheteurs suivis...")
    pertinents = []
    for g in fusionnes:
        correspondances = trouver_correspondances(g, mots_cles, mots_cles_lots, acheteurs)
        if correspondances or (not mots_cles and not mots_cles_lots and not acheteurs):
            g["mots_cles_trouves"] = correspondances
            pertinents.append(g)
    annoncer(f"✅ {len(pertinents)}/{len(fusionnes)} avis pertinent(s).")
    return pertinents
