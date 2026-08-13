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
                    │    sources/          │  4 sources, chacune isolée :
                    │  __init__.py          │  une panne n'en bloque pas 3 autres
                    └─────────┬─────────────┘
             ┌────────────────┼────────────────┬─────────────────┐
             ▼                ▼                ▼                 ▼
        boamp.py          ted.py       aws_solutions.py       place.py
     (API publique)   (API publique)   (login + API,       (scraping HTML,
                                        identifiants          Réunion/Mayotte
                                        requis)                câblé en dur)
             │                │                │                 │
             └────────────────┴────────┬───────┴─────────────────┘
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
| `app.py`                    | Le site Streamlit — 3 onglets (🎯 Trier, 📋 Historique, 🔑 Mots-clés) + barre latérale (actions globales). |
| `pipeline.py`                | Les actions déclenchables : nettoyer les avis expirés, lancer la recherche, recalculer les scores, enregistrer un swipe. |
| `sources/__init__.py`         | Appelle les 4 sources l'une après l'autre, catch les erreurs individuellement, fusionne et filtre — attache aussi les mots-clés qui ont fait matcher chaque avis. |
| `sources/boamp.py`             | Récupération BOAMP (API officielle). |
| `sources/ted.py`                | Récupération TED/JOUE (API officielle). |
| `sources/aws_solutions.py`       | Récupération AWSolutions (login + API interne, identifiants requis). |
| `sources/place.py`                | Récupération PLACE (scraping HTML, pas d'API publique). |
| `sources/commun.py`                | Code partagé par les 4 sources : normalisation de texte, fusion des doublons, `trouver_correspondances`/`est_pertinent` (filtre mots-clés/lots/acheteurs). |
| `scoring.py`                        | Le score d'un avis : embedding + KNN (ou heuristique en cold start). |
| `db.py`                              | Connexion Supabase + listes partagées (mots-clés, mots-clés lots, acheteurs suivis). |
| `schema.sql`                          | Le schéma Supabase complet (+ listes par défaut) — à exécuter une fois sur une base vidée. |
| `schema2.sql`                          | Migration additive : colonne `mots_cles_trouves` (pourquoi un avis est proposé) — à exécuter après `schema.sql`. |
| `config.json`                          | Départements suivis, filtre "avis ouverts uniquement". |

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
