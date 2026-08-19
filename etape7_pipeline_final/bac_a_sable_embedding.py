"""
bac_a_sable_embedding.py — jouer avec la similarité d'embedding
================================================================================

Outil manuel (pas un script du pipeline, rien ici n'est appelé par
app.py/pipeline.py) pour répondre concrètement à une question : "à partir
de quelle similarité deux avis sont-ils vraiment le même marché ?"

Utile en particulier maintenant qu'une 5e source (e-marchespublics) agrège
elle-même BOAMP/JOUE : la redondance entre sources augmente, et la fusion
des doublons actuelle (`sources/commun.py::fusionner_doublons`) est une
heuristique texte (acheteur identique + `difflib.SequenceMatcher`), PAS une
similarité d'embedding — elle peut rater des reformulations importantes
(voir la démonstration dans `sources/commun.py`, bloc `__main__`, avis EMP
qui ne fusionne pas avec son équivalent BOAMP). Ce bac à sable sert à
évaluer si/à quel seuil un embedding ferait mieux, AVANT de décider de
l'ajouter au pipeline réel.

Deux modes (constantes ci-dessous) :
  - hors-ligne (par défaut) : quelques paires de textes écrits à la main.
  - sur données réelles (`UTILISER_SUPABASE = True`) : compare les avis déjà
    en base entre eux, affiche les paires les plus proches — utile pour
    repérer en pratique des doublons BOAMP/EMP.

Lancer avec : `python bac_a_sable_embedding.py` (depuis etape7_pipeline_final/).
Télécharge le modèle d'embedding au premier lancement (~2 Go, une seule fois).
"""

from __future__ import annotations

import itertools
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scoring import calculer_embedding, construire_texte_embedding, obtenir_embedding, similarite_cosinus

# =====================================================================
# Constantes à modifier pour expérimenter
# =====================================================================

UTILISER_SUPABASE = False  # True -> compare les avis réels déjà en base
NB_AVIS_SUPABASE = 25       # nombre d'avis tirés (les plus récents) en mode Supabase

# Seuils à évaluer — ajoutez/retirez des valeurs pour affiner.
SEUILS_A_TESTER = [0.80, 0.85, 0.90, 0.95, 0.97]

# Paires de textes d'avis (label, texte A, texte B) — écrites à la main pour
# couvrir 3 cas typiques : doublon évident, reformulation (même marché,
# texte différent — le cas qui échappe à l'heuristique texte actuelle), et
# deux avis clairement différents.
PAIRES_EXEMPLE = [
    (
        "doublon évident (texte quasi identique)",
        "query: Rénovation de l'école primaire de Saint-Denis. Acheteur : Mairie de Saint-Denis.",
        "query: Rénovation école primaire Saint-Denis. Acheteur : Mairie de Saint-Denis.",
    ),
    (
        "reformulation (même marché, texte différent — cas EMP/BOAMP typique)",
        "query: Rénovation de l'école primaire de Saint-Denis. Acheteur : Mairie de Saint-Denis.",
        "query: Travaux de rénovation de l'école primaire de Saint-Denis. Acheteur : Mairie de Saint-Denis.",
    ),
    (
        "avis clairement différents",
        "query: Rénovation de l'école primaire de Saint-Denis. Acheteur : Mairie de Saint-Denis.",
        "query: Collecte des déchets ménagers. Acheteur : CIREST.",
    ),
]


def _tableau_seuils(similarites: list[tuple[str, float]]) -> None:
    """Affiche, pour chaque seuil candidat, combien de paires seraient
    considérées comme doublon."""
    print("\nSensibilité par seuil (nombre de paires considérées comme doublon) :")
    for seuil in SEUILS_A_TESTER:
        nb = sum(1 for _, sim in similarites if sim >= seuil)
        print(f"  seuil >= {seuil:.2f} : {nb}/{len(similarites)} paire(s)")


def mode_hors_ligne() -> None:
    print("=" * 70)
    print("Mode hors-ligne — paires d'exemple écrites à la main")
    print("=" * 70)

    # Cache pour ne calculer chaque texte qu'une fois.
    embeddings: dict[str, list[float]] = {}

    def embedding_de(texte: str) -> list[float]:
        if texte not in embeddings:
            embeddings[texte] = calculer_embedding(texte)
        return embeddings[texte]

    similarites = []
    for label, texte_a, texte_b in PAIRES_EXEMPLE:
        sim = similarite_cosinus(embedding_de(texte_a), embedding_de(texte_b))
        similarites.append((label, sim))
        print(f"\n[{label}]")
        print(f"  A : {texte_a}")
        print(f"  B : {texte_b}")
        print(f"  similarité cosinus = {sim:.4f}")

    _tableau_seuils(similarites)


def mode_supabase(n: int = NB_AVIS_SUPABASE) -> None:
    print("=" * 70)
    print(f"Mode Supabase — {n} avis réels les plus récents")
    print("=" * 70)

    import db

    client = db.charger_client()
    offres = (
        client.table("appels_offres")
        .select("id,objet,acheteur,description,lots,source")
        .order("created_at", desc=True)
        .limit(n)
        .execute()
        .data
    )
    if len(offres) < 2:
        print("Pas assez d'avis en base pour comparer (il en faut au moins 2).")
        return

    print(f"{len(offres)} avis chargés, calcul des embeddings (cache réutilisé si déjà calculé)...")
    embeddings = {o["id"]: obtenir_embedding(client, o) for o in offres}

    paires = []
    for a, b in itertools.combinations(offres, 2):
        sim = similarite_cosinus(embeddings[a["id"]], embeddings[b["id"]])
        label = f"[{a['source']}] {a['objet'][:60]!r}  <->  [{b['source']}] {b['objet'][:60]!r}"
        paires.append((label, sim))

    paires.sort(key=lambda p: p[1], reverse=True)
    print(f"\nTop 10 des paires les plus proches (sur {len(paires)} paires au total) :")
    for label, sim in paires[:10]:
        print(f"  {sim:.4f}  {label}")

    _tableau_seuils(paires)


if __name__ == "__main__":
    if UTILISER_SUPABASE:
        mode_supabase()
    else:
        mode_hors_ligne()
        print("\n(Astuce : passez UTILISER_SUPABASE = True en tête de fichier "
              "pour comparer de vrais avis déjà en base.)")
