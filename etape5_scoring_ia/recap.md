# Récapitulatif — Étape 5 : scoring par apprentissage de préférence (A/B test)

## Principe général

Le score n'est plus une heuristique fixe (étapes 3/4) : ce sont désormais
**deux modèles de préférence, partagés par toute l'équipe**, entraînés en
parallèle sur les mêmes swipes, pour comparer empiriquement deux
architectures de features. Chaque avis affiche `score_modele_a` et
`score_modele_b` côte à côte le temps du test.

## Modèle A — "classique + prompt"

Architecture initiale de l'étape 5 : embedding brut (1024 dims,
`multilingual-e5-large`) + features structurées (urgence, fraîcheur,
correspondance mots-clés, département, source, longueur), **+ une feature
supplémentaire** : `similarite_profil`, la similarité cosinus entre
l'embedding de l'avis et l'embedding d'un **profil de recherche** écrit à la
main (`profil_cible.py`, texte modifiable, calculé une seule fois et mis en
cache).

## Modèle B — "features enrichies, sans embedding brut"

Ne reçoit **pas** l'embedding brut (1024 dims pour quelques centaines de
swipes = trop de paramètres pour trop peu de données, convergence lente/
bruitée). À la place, des features plus compactes et sample-efficaces :

- `sim_centroide_like` / `sim_centroide_dislike` — similarité cosinus aux
  centroïdes (moyennes mobiles) des embeddings des avis déjà aimés/rejetés.
  Mis à jour à chaque swipe via une moyenne incrémentale numériquement
  stable (`mettre_a_jour_centroide`).
- `knn_like_ratio` — proportion de "like" parmi les k=10 avis déjà swipés
  les plus proches (similarité cosinus), via la fonction SQL
  `match_swipes_proches` (pgvector), exposée en RPC Supabase. 0.5 (neutre)
  s'il n'y a encore aucun swipe. Ce calcul est sauté pendant le cold start
  (économise l'appel RPC tant qu'il n'est pas utilisé de toute façon).
- `type_procedure`, `nature_libelle`, `descripteur_libelle` — champs BOAMP
  officiels (nomenclature de marché), auparavant récupérés puis jetés lors
  de la normalisation (`recupDataBaseOfficial.py`), maintenant conservés
  jusqu'à `features.py`. **Vides pour les avis TED** (pas d'équivalent
  direct collecté actuellement côté TED — limite connue).
- + les mêmes features structurées que le modèle A (urgence, fraîcheur,
  score mots-clés, département, source, longueur).

Les deux modèles restent des régressions logistiques linéaires
(interprétables), précédées d'un `OneHotEncoder` (catégorielles) et d'un
`StandardScaler` (numériques) — voir `modele_preference.py`.

## Ordre de la file de swipe : aléatoire, plus par score

Changement demandé : trier par score décroissant biaise l'entraînement vers
les avis déjà "évidents" pour le(s) modèle(s), au détriment des exemples
réellement informatifs (limites de décision). `app.py` tire maintenant un
avis **au hasard** parmi ceux non triés (`decision = 'n/A'`) à chaque
rafraîchissement.

## Séparation des rôles inchangée

Mots-clés/acheteurs suivis pilotent toujours uniquement le périmètre de
recherche BOAMP/TED — `score_mots_cles` reste une feature parmi d'autres
pour les deux modèles, ce n'est plus (depuis le début de l'étape 5) le score
final.

## Anti-fuite de données (data leakage)

Dans `pipeline_scoring.update_model`, les signaux du modèle B propres à un
swipe (`sim_centroide_*`, `knn_like_ratio`) sont calculés **avant**
d'écrire le nouveau swipe en base et avant de mettre à jour les centroïdes
— sinon l'avis se retrouverait à influencer sa propre feature (ex. matcher
avec lui-même dans le kNN, ou décaler son propre centroïde avant même
d'apprendre dessus).

## Persistance — `modele_preference_etat`

Passe d'une ligne fixe (`id=1`) à **une ligne par modèle**
(`nom_modele = 'A'` ou `'B'`). Colonnes `centroide_like`, `centroide_dislike`,
`nb_like`, `nb_dislike` ajoutées (utilisées uniquement par `'B'`, nulles
pour `'A'`).

## Nouveaux éléments SQL (`schema_v2_ab_test.sql`, à exécuter APRÈS `schema.sql`)

- `appels_offres.score_modele_a` / `score_modele_b` (l'ancienne colonne
  `score` n'est plus mise à jour par le pipeline, gardée pour compat).
- `appels_offres_features.type_procedure` / `nature_libelle` / `descripteur_libelle`.
- `modele_preference_etat` : `id` → `nom_modele` (clé texte) + colonnes centroïdes.
- Table `profil_cible` (singleton) : texte + embedding du profil de recherche.
- Fonction RPC `match_swipes_proches(vecteur, k)` : kNN pgvector parmi les
  avis déjà swipés (jointure `swipes` + `appels_offres_features`).

## Fichiers du dossier (ajouts/changements pour l'A/B test)

- `schema_v2_ab_test.sql` — évolutions de schéma ci-dessus.
- `profil_cible.py` *(nouveau)* — profil de recherche du modèle A.
- `features.py` — ajoute `similarite_cosinus`, `knn_like_ratio`, champs
  BOAMP dans `calculer_features_structurees`.
- `modele_preference.py` — généralisé : deux pipelines (A/B), deux jeux de
  features, persistance par `nom_modele`, gestion des centroïdes.
- `pipeline_scoring.py` — `predict_score` calcule et écrit les deux scores ;
  `update_model` entraîne les deux modèles à partir du même swipe.
- `InsertIntoDataBase.py` — recolle les champs BOAMP (absents de la table
  `appels_offres`) depuis les résultats bruts de `recuperer_appels_offres`
  avant le premier calcul de features.
- `app.py` — file de swipe aléatoire, deux scores affichés côte à côte,
  panneau "poids du modèle" dupliqué pour A et B dans la barre latérale.
- `recupDataBaseOfficial.py` — `normaliser_boamp`/`fusionner_doublons`
  conservent désormais `type_procedure`, `nature_libelle`, `descripteur_libelle`.

## Comment décider quel modèle est le meilleur (une fois assez de swipes)

Pistes simples à appliquer une fois qu'il y a suffisamment de swipes
accumulés (piste évoquée mais pas encore implémentée dans cette étape) :
comparer une métrique "test-then-train" (prédire avant `learn_one`, ex.
`river.metrics.ROCAUC`/`Accuracy`, mise à jour à chaque swipe) pour A et B
séparément — celui qui a la meilleure métrique cumulée prédit le mieux les
préférences réelles de l'équipe. À ajouter dans une itération suivante si
utile.

## Limites connues

- Pas de gestion de concept drift (reporté, comme décidé à l'étape 5 initiale).
- `descripteur_libelle`/`type_procedure`/`nature_libelle` vides pour les
  avis TED (modèle B légèrement désavantagé sur ces avis).
- Le kNN (`match_swipes_proches`) ne devient utile qu'une fois qu'il y a
  des swipes en base — neutre (0.5) sinon.
