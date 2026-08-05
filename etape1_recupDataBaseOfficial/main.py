"""
Script de test — récupération d'appels d'offres BOAMP + TED (JOUE)
====================================================================

Ce script est la version "test / export" du projet : il sert à lancer une
recherche depuis la ligne de commande, l'afficher en console, et l'exporter
en CSV + JSON. Toute la logique de récupération (requêtes API, normalisation,
dédoublonnage, détection de doublons inter-sources) vit dans
`recupDataBaseOfficial.py`, la version "bibliothèque" réutilisable ailleurs
dans le projet — voir ce fichier pour l'utiliser en boîte noire depuis un
autre script.

Logique de filtre :
    (mot-clé 1 OU mot-clé 2 OU ... OU mot-clé N)   <- OR entre les mots-clés
    ET (département dans PROVENANCE)                <- AND entre les champs
    ET (date limite de réponse > aujourd'hui)        <- AND entre les champs

Tout se règle dans le bloc CONFIGURATION ci-dessous. Relancez simplement
`python main.py` après avoir modifié les valeurs qui vous intéressent.

Sources / documentation :
  - BOAMP (Opendatasoft) : https://boamp-datadila.opendatasoft.com/explore/dataset/boamp/
  - TED Search API v3    : https://docs.ted.europa.eu/api/latest/index.html
  - Détail des champs    : voir recap.md dans ce même dossier
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from recupDataBaseOfficial import recuperer_appels_offres

# =====================================================================
# CONFIGURATION — c'est ici que vous contrôlez les requêtes
# =====================================================================

# Mots-clés recherchés dans l'objet / le texte de l'appel d'offre.
# Combinés en OU logique : un avis matche s'il contient AU MOINS UN de ces mots.
KEYWORDS: list[str] = [
    "rénovation",
    "construction",
    "école",
    "collège",
    "lycée",
    "université",
    "laboratoire",
    "paillasse",
    "cuisine",
    "placard",
    "agencement",
    "mobilier",
]

# Départements/territoires ciblés (provenance des appels d'offres).
# Combinés en OU logique entre eux, mais en ET avec les mots-clés et la date.
#   974 = La Réunion, 976 = Mayotte
DEPARTEMENTS: list[str] = ["974", "976"]

# Quelles bases interroger. Passez à False pour désactiver une source.
SOURCES_ACTIVES: dict[str, bool] = {
    "boamp": True,
    "ted": True,
}

# Ne garder que les appels d'offres dont la date limite de réponse n'est
# pas encore passée. Passez à False pour tout récupérer (y compris les avis clos).
SEULEMENT_OUVERTS: bool = True

# Fusionne les avis BOAMP correspondant au même dossier (rectificatifs /
# avis édités plusieurs fois) en un seul, en gardant la version la plus
# récente. Passez à False pour voir toutes les versions séparément.
DEDUPLIQUER: bool = True

# Nombre maximum de résultats à récupérer par source (protège contre des
# requêtes trop larges pendant les tests).
LIMIT_PAR_SOURCE: int = 100

# Dossier de sortie pour les exports (par défaut : ce même dossier test1/)
DOSSIER_SORTIE: Path = Path(__file__).parent


# =====================================================================
# Export
# =====================================================================

def exporter_csv(resultats: list[dict], chemin: Path) -> None:
    if not resultats:
        print(f"[Export] Aucun résultat, {chemin.name} non créé.")
        return
    colonnes = list(resultats[0].keys())
    with chemin.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes, delimiter=";")
        writer.writeheader()
        for r in resultats:
            # les champs liste (source, identifiants, urls) sont aplatis en
            # une seule cellule "a; b; c" pour rester lisibles dans un tableur
            ligne = {k: ("; ".join(v) if isinstance(v, list) else v) for k, v in r.items()}
            writer.writerow(ligne)
    print(f"[Export] CSV écrit : {chemin}")


def exporter_json(resultats: list[dict], chemin: Path) -> None:
    with chemin.open("w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    print(f"[Export] JSON écrit : {chemin}")


# =====================================================================
# main
# =====================================================================

def main() -> None:
    print("=" * 70)
    print("Recherche d'appels d'offres — BOAMP + TED (JOUE)")
    print("=" * 70)
    print(f"Mots-clés (OU)      : {', '.join(KEYWORDS)}")
    print(f"Départements (OU)   : {', '.join(DEPARTEMENTS)}")
    print(f"Seulement ouverts   : {SEULEMENT_OUVERTS}")
    print(f"Dédoublonnage       : {DEDUPLIQUER}")
    print(f"Sources actives     : {', '.join(s for s, actif in SOURCES_ACTIVES.items() if actif)}")
    print(f"Limite par source   : {LIMIT_PAR_SOURCE}")
    print("=" * 70)

    tous_resultats = recuperer_appels_offres(
        mots_cles=KEYWORDS,
        departements=DEPARTEMENTS,
        seulement_ouverts=SEULEMENT_OUVERTS,
        sources=SOURCES_ACTIVES,
        limit_par_source=LIMIT_PAR_SOURCE,
        dedupliquer=DEDUPLIQUER,
        verbeux=True,
    )

    print("-" * 70)
    print(f"TOTAL : {len(tous_resultats)} appel(s) d'offre(s) trouvé(s) (après fusion des doublons).")
    for r in tous_resultats[:10]:
        marqueur_versions = f" [{r['nb_versions']} avis fusionnés]" if r["nb_versions"] > 1 else ""
        print(
            f"  [{'+'.join(r['source'])}] {', '.join(r['identifiants'])} — {r['objet'][:70]} — {r['acheteur']} "
            f"— limite : {r['date_limite_reponse']}{marqueur_versions}"
        )
    if len(tous_resultats) > 10:
        print(f"  ... et {len(tous_resultats) - 10} de plus (voir les fichiers exportés).")

    nb_croises = sum(1 for r in tous_resultats if len(r["source"]) > 1)
    nb_fusionnes = sum(1 for r in tous_resultats if r["nb_versions"] > 1)
    print(f"Avis présents dans les 2 bases (BOAMP+TED) : {nb_croises}.")
    print(f"Lignes issues d'une fusion de plusieurs avis (rectificatifs/republications) : {nb_fusionnes}.")

    if not tous_resultats:
        print("Aucun résultat : rien à exporter.")
        return

    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    exporter_csv(tous_resultats, DOSSIER_SORTIE / f"resultats_{horodatage}.csv")
    exporter_json(tous_resultats, DOSSIER_SORTIE / f"resultats_{horodatage}.json")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrompu par l'utilisateur.")
