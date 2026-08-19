# Structure — carte du pipeline

Ce document se lit une fois, puis sert de repère : un fichier = une étape du
pipeline, dans l'ordre où les données le traversent.

```
                          ┌─────────────────────────────┐
                          │   app.py  (Streamlit)         │  <- seul point d'entrée utilisateur
                          └───────────────┬───────────────┘
                                          │
                     ┌────────────────────┼────────────────────┐
                     │                    │                    │
              "Lancer la           "Recalculer les        swipe (accepter/
               recherche"              scores"                rejeter)
                     │                    │                    │
                     ▼                    ▼                    ▼
              ┌─────────────────────────────────┐      ┌──────────────────┐
              │           pipeline.py             │      │   pipeline.py      │
              │  lancer_recherche()                │      │ enregistrer_swipe() │
              │  recalculer_scores()                │      │  (rapide, pas de   │
              └───────────────┬─────────────────────┘      │   calcul ici)      │
                              │                              └──────────────────┘
                              ▼
                    ┌───────────────────┐
                    │    sources/          │  6 sources, chacune isolée :
                    │  __init__.py          │  une panne n'en bloque pas 5 autres
                    └─────────┬─────────────┘
   ┌────────────┬────────────┬─────────────────┬──────────────┬──────────────────┐
   ▼            ▼            ▼                 ▼              ▼                  ▼
boamp.py     ted.py    aws_solutions.py     place.py      e_marche.py      achat_public.py
(API        (API       (login + API,     (scraping HTML,  (login +        (scraping HTML,
publique)  publique)   identifiants        Réunion/Mayotte  scraping HTML,  filtre géo en
                        requis)             câblé en dur)   identifiants    payload fixe)
                                                             requis, agrège
                                                             lui-même
                                                             BOAMP/JOUE)
   │            │            │                 │              │                  │
   └────────────┴────────┬───┴─────────────────┴──────────────┴──────────────────┘
                                       ▼
                              sources/commun.py
                     (normalise, fusionne les doublons,
                      filtre mots-clés/lots/acheteurs)
                                       │
                                       ▼
                          appels_offres (Supabase)
                                       │
                                       ▼
                                 scoring.py
                    embedding (cache) + KNN sur les swipes
                    déjà enregistrés, ou heuristique si pas
                    encore assez de swipes (cold start)
                                       │
                                       ▼
                          appels_offres.score (Supabase)
```

## Un fichier = une responsabilité

| Fichier                    | Rôle |
|-----------------------------|------|
| `app.py`                    | Le site Streamlit — 4 onglets (🎯 Trier, 🆕 Nouveautés, 📋 Historique, 🔑 Mots-clés) + barre latérale (actions globales) + fenêtre modale de détails. |
| `pipeline.py`                | Les actions déclenchables : nettoyer les avis expirés, lancer la recherche (+ logger dans `recherches`), recalculer les scores, enregistrer un swipe, `calculer_cutoff_nouveautes`. |
| `sources/__init__.py`         | Appelle les 6 sources l'une après l'autre, catch les erreurs individuellement, fusionne et filtre — attache aussi les mots-clés qui ont fait matcher chaque avis. |
| `sources/boamp.py`             | Récupération BOAMP (API officielle). |
| `sources/ted.py`                | Récupération TED/JOUE (API officielle). |
| `sources/aws_solutions.py`       | Récupération AWSolutions (login + API interne, identifiants requis). |
| `sources/place.py`                | Récupération PLACE (scraping HTML, pas d'API publique). |
| `sources/e_marche.py`              | Récupération e-marchespublics (login + scraping HTML, identifiants requis ; agrège lui-même BOAMP/JOUE). |
| `sources/achat_public.py`           | Récupération achatpublic.com (scraping HTML, pas de compte requis, filtre géographique en payload fixe). |
| `sources/commun.py`                | Code partagé par les 6 sources : normalisation de texte, fusion des doublons, `trouver_correspondances`/`est_pertinent` (filtre mots-clés/lots/acheteurs), `assurer_navigateur_installe` (Playwright). |
| `scoring.py`                        | Le score d'un avis : embedding + KNN (ou heuristique en cold start) + `similarite_cosinus` (réutilisée par le bac à sable). |
| `db.py`                              | Connexion Supabase + listes partagées (mots-clés, mots-clés lots, acheteurs suivis). |
| `schema.sql`                          | Le schéma Supabase complet (+ listes par défaut) — à exécuter une fois sur une base vidée. |
| `schema2.sql`                          | Migration additive : colonne `mots_cles_trouves` (pourquoi un avis est proposé) — à exécuter après `schema.sql`. |
| `schema3.sql`                          | Migration additive : table `recherches` (repère temporel pour l'onglet "🆕 Nouveautés") — à exécuter après `schema2.sql`. |
| `config.json`                          | Départements suivis, filtre "avis ouverts uniquement". |
| `bac_a_sable_embedding.py`             | Outil manuel (hors pipeline) pour explorer un seuil de similarité d'embedding — voir README.md "Limites connues". |

## Tester une brique individuellement

Chaque fichier de `sources/` a son propre bloc `if __name__ == "__main__":`
pour un test manuel isolé (pas de framework de test, un script direct comme
le reste du projet) :

```bash
cd etape7_pipeline_final
python sources/boamp.py           # ou ted.py / place.py / aws_solutions.py /
                                   # e_marche.py / achat_public.py
                                   # (marche aussi via `python -m sources.boamp`, ou le
                                   #  bouton "Run" de l'IDE)
python sources/commun.py          # bac à sable des fonctions de normalisation/fusion/filtre
python bac_a_sable_embedding.py   # exploration du seuil de similarité d'embedding
```

## Pourquoi c'est plus simple qu'avant

Les versions précédentes (voir `etape5_scoring_ia/`, gardée intacte comme
référence) entraînaient deux modèles de classification en parallèle
(bibliothèque River), avec sauvegarde/rechargement d'un modèle pickled à
chaque calcul, et un mécanisme séparé pour éviter d'apprendre plusieurs fois
sur le même avis. Ici, le KNN n'apprend rien à l'avance : il compare
directement l'embedding de l'avis en cours aux embeddings des avis déjà
swipés, à la demande. Résultat : pas de modèle à entraîner, pas de fichier
pickle, pas de logique d'entraînement différé — juste un embedding par avis,
mis en cache une fois calculé.
