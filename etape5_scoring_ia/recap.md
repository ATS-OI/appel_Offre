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

## Description complète de la mission

Chaque avis affiche désormais, sous le titre, le début de la **description
complète** de la mission (pas seulement l'objet, souvent trop court pour
juger) :
- Côté BOAMP, extraite du blob JSON `donnees` (structure eForms), champ
  `cac:ProcurementProject.cbc:Description` (`_extraire_description_boamp`
  dans `recupDataBaseOfficial.py`) — best-effort, absente sur les avis les
  plus anciens qui n'ont pas ce blob.
- Côté TED, champ `description-lot` de l'API v3 (texte multilingue,
  concaténé via `_texte_multilingue_complet`).
- Stockée dans `appels_offres.description`, propagée par la fusion de
  doublons comme les autres champs texte. Utilisée aussi dans le texte
  passé à l'embedding (`features.construire_texte`) — les deux modèles en
  bénéficient donc, pas seulement l'affichage.
- Affichage tronqué à 400 caractères (`app.py`) pour respecter la
  contrainte "tout visible sans scroller".

## Indépendance des décisions par utilisateur

Chaque utilisateur a sa **propre file de tri**, complètement indépendante
des autres : si Alice accepte un avis, il reste "à trier" pour Bob, et
inversement.

- Identification légère en haut de la barre latérale ("👤 Votre nom") — un
  simple texte libre gardé en `st.session_state`, **pas une authentification**
  (aucun mot de passe, rien n'empêche de changer de nom). Suffisant pour
  distinguer les décisions de chacun sans gérer de comptes.
- La file de tri (`app.py::charger_offres_a_trier`) se base sur la table
  `swipes` filtrée par `user_id` : un avis est "à trier" pour un utilisateur
  tant que celui-ci n'a **lui-même** aucune ligne dans `swipes` pour cet
  avis, quel que soit ce que les autres ont décidé.
- `raisons` (historique métier + commentaire) porte aussi désormais un
  `user_id`, pour savoir qui a pris quelle décision et pourquoi.
- `appels_offres.decision` continue d'être mis à jour à chaque swipe, mais
  **uniquement à titre informatif** (dernière décision connue, tous
  utilisateurs confondus, utile pour un coup d'œil rapide) — ce n'est plus
  ce champ qui détermine la file de qui que ce soit.
- Les deux modèles A/B, eux, restent **partagés** : chaque swipe de
  n'importe quel utilisateur continue d'entraîner les mêmes modèles pour
  tout le monde (voir "Principe général" ci-dessus) — seule la *file
  d'avis à trier* est individuelle, pas l'apprentissage.

## `mots_cles_lots` (préparation, pas encore branchée)

Nouvelle table partagée, même forme que `mots_cles` (une colonne texte +
une date d'ajout), gérable dès maintenant depuis la barre latérale
("Mots-clés lots"). **Pas encore utilisée** dans la construction des
requêtes BOAMP/TED ni dans le scoring — réservée à un usage futur (par
exemple filtrer/qualifier au niveau des lots d'un marché plutôt que de
l'avis entier). Voir `schema_v4_multiuser.sql`,
`listes_partagees.charger_mots_cles_lots`/`ajouter_mot_cle_lot`/`retirer_mot_cle_lot`.

## Nouveaux éléments SQL (`schema_v3_description.sql`, `schema_v4_multiuser.sql`)

- `schema_v3_description.sql` : `appels_offres.description` (text).
- `schema_v4_multiuser.sql` :
  - `raisons.user_id` (text, défaut `'anonyme'`) — cohérent avec
    `swipes.user_id`, déjà présent depuis le début de l'étape 5.
  - Table `mots_cles_lots` (id, mot unique, date_ajout) + policies RLS
    publiques (lecture/insertion/suppression), même modèle que `mots_cles`.

## Anti-doublons / anti-fuite (solidifie l'entraînement)

Relecture a posteriori de l'entraînement en ligne (River) qui a révélé
plusieurs façons dont un exemple pouvait peser plus qu'il ne devrait, ou
carrément fuiter sa propre étiquette dans sa propre feature. Corrections
apportées (`schema_v5_antidoublons.sql`) :

**1) + 3) Un avis n'est appris qu'une seule fois par les modèles.**
Avant, un même avis swipé par plusieurs utilisateurs (file de tri
indépendante par utilisateur) était réappris à chaque swipe → le centroïde
et le gradient de la régression logistique étaient tirés plusieurs fois
vers le même contenu. Corrigé avec un seul mécanisme, qui couvre aussi les
quasi-doublons de contenu (rectificatifs mal chaînés, republication
BOAMP+TED non fusionnée à l'ingestion) — détectés **directement via la
similarité d'embedding** déjà calculée à l'étage 1, plutôt qu'une nouvelle
heuristique texte :
- `appels_offres_features.deja_appris` (booléen) : marqué `true` juste
  après qu'un avis a servi à entraîner les modèles (`marquer_appris`).
- RPC `existe_apprentissage_proche(vecteur, seuil=0.97)` : vrai s'il existe
  déjà un avis `deja_appris` dont la similarité cosinus avec `vecteur`
  dépasse le seuil — que ce soit littéralement le même avis (swipé une 2e
  fois par un autre utilisateur, similarité = 1.0 avec lui-même) ou un
  quasi-doublon de contenu.
- Dans `pipeline_scoring.update_model`, avant `apprendre_a`/`apprendre_b` :
  si `existe_apprentissage_proche` répond vrai, l'entraînement est **sauté**
  (le swipe reste quand même enregistré dans `swipes`/`raisons` comme
  avant — juste pas réinjecté dans les modèles).
- **Solution retenue explicitement (option "a")** : seul le premier swipe
  d'un avis (ou de son cluster de quasi-doublons) compte pour l'entraînement
  — pas de recalcul de vote majoritaire a posteriori. Limite assumée : un
  premier swipe erroné (clic accidentel) reste appris définitivement, sans
  mécanisme de correction. Le seuil (0.97) est une heuristique réglable, pas
  une garantie absolue de détection de tous les quasi-doublons.

**2) Fuite directe via le kNN du modèle B.** `match_swipes_proches` ne
s'excluait pas elle-même : comme le swipe en cours est écrit dans `swipes`
AVANT le calcul des features (ordre volontaire, voir plus bas), un avis déjà
swipé par un autre utilisateur pouvait se retrouver son **propre voisin** à
similarité 1.0 dans son propre calcul de `knn_like_ratio` — fuite directe de
l'étiquette dans sa propre feature (existait même en usage mono-utilisateur,
côté `predict_score`, dès qu'un avis était affiché une 2e fois après son
propre swipe). Corrigé : `match_swipes_proches(vecteur, k, exclu)` prend
maintenant un paramètre `exclu` (l'`appel_offre_id` courant), transmis par
`features.knn_like_ratio` et donc par `pipeline_scoring._features_b_avec_signaux`
— utilisé aussi bien à la prédiction (`predict_score`) qu'à l'entraînement
(`update_model`).

**4) "⏳ Pour l'instant" retiré.** Décision jugée inutile — supprimée
entièrement de l'interface (`app.py`, 2 boutons au lieu de 3 : Rejeter /
Accepter), pas seulement de l'entraînement. Aucune contrainte SQL à changer
(`appels_offres.decision` est un `text` libre sans `CHECK`).

**5) Double-swipe accidentel.** Contrainte `UNIQUE (appel_offre_id, user_id)`
sur `swipes` + écriture en `upsert` (au lieu d'`insert`) côté
`pipeline_scoring.update_model` : un double clic ou un re-render Streamlit
ne peut plus créer deux lignes pour le même utilisateur sur le même avis.
`raisons` reste un pur historique en `insert` (pas de contrainte) — ce n'est
pas ce qui pilote l'entraînement, donc pas concerné par ce risque.

**Non retenu** : pondération des exemples par force de consensus entre
utilisateurs (ex. 3 like/2 dislike pesant moins qu'un 5/0 unanime) — écarté
par choix explicite, l'option "premier swipe fait foi" (ci-dessus) suffit.

## Filtrage côté client + lots BOAMP/TED

Constat vérifié en direct sur les APIs réelles : un marché multi-lots est
parfois publié avec un objet de haut niveau **générique**, le détail
pertinent étant uniquement dans ses lots. Exemple concret (BOAMP,
`24-24231`) : objet "ACHAT DE FOURNITURES ET DE MATÉRIAUX DE
CONSTRUCTION…", 10 lots dont "Lot 7 Plaques de plâtre (BA) et
accessoires" — un mot-clé comme "plâtre" ne matchait **jamais** l'ancien
filtre serveur (`objet like "%mot%"` côté BOAMP, texte intégral côté TED
mais toujours agrégé au niveau de l'avis), l'avis était donc raté
silencieusement.

**Changement d'architecture** (inspiré de `etape6_webScrapping/aws.py`,
dashboard de filtre séparant mots-clés "titre" et "lots") : BOAMP/TED ne
sont plus interrogés que par département/date (plus aucune clause
mots-clés/acheteurs dans `_construire_where_boamp`/`_construire_requete_ted`)
— tout le filtrage se fait maintenant **côté client**, après récupération et
fusion des doublons, via `_est_pertinent` :
- pertinent si l'objet matche `mots_cles`, OU
- l'acheteur matche `acheteurs_suivis`, OU
- le texte des lots (titres + descriptions) matche **`mots_cles_lots`**
  (table créée à l'extension précédente, "pour plus tard" — c'est
  maintenant branché) ;
- comparaison accent/casse-insensible (`_normaliser_texte`, déjà utilisé
  pour le dédoublonnage) ;
- si les 3 listes sont vides, tout est gardé (même comportement que `aws.py`).

Volumes vérifiés en direct sans aucun filtre mot-clé (974+976, avis
ouverts) : ~181 BOAMP + ~126 TED — largement gérable en un seul aller-retour
paginé, d'où `LIMIT_PAR_SOURCE` relevé à 250 (c'est maintenant un plafond
sur les résultats BRUTS avant filtrage, plus un plafond "final").

**Extraction des lots** :
- BOAMP : `_extraire_lots_boamp` parse `cac:ProcurementProjectLot` dans le
  blob `donnees` (eForms) — un dict si le marché n'a qu'un seul lot, une
  liste sinon (les deux formes existent en pratique, vérifié en direct).
- TED : `TED_FIELDS` gagne `title-lot`/`identifier-lot` (en plus de
  `description-lot`, déjà collecté) — 3 listes alignées par index, vérifié
  en direct (ex. notice à 24 lots aux titres tous différents).
- Chaque lot : `{"identifiant", "titre", "description"}`. Propagé par la
  fusion de doublons avec le même compromis que `description` (lots du
  représentant du groupe, sinon le premier membre qui en a).
- Stocké dans `appels_offres.lots` (jsonb) — colonne directe (contrairement
  à `type_procedure`/`nature_libelle`, qui nécessitent le pont
  `par_identifiants` dans `InsertIntoDataBase.py` car absents de la table).

## Lots dans le scoring

- `features.construire_texte` ajoute le texte des lots à la fin du texte
  embarqué (après objet/acheteur/description) — l'embedding "voit" le détail
  des lots, pas seulement l'objet générique.
- Deux nouvelles features structurées, pour les DEUX modèles A et B :
  `score_mots_cles_lots` (réutilise `scoring.compter_mots_cles` sur le texte
  des lots, comme `score_mots_cles` sur l'objet) et `nb_lots` (nombre de
  lots — signal brut, un marché à 20 lots n'est pas la même chose qu'un
  marché à 1 lot).
- `mots_cles_lots` circule maintenant partout où `mots_cles` circulait déjà
  (`extraire_et_stocker_features`, `predict_score`, `update_model`,
  `recalculer_tous_les_scores`).

## Reset du scoring (lots)

Le texte embarqué changeant avec cette extension, les embeddings/modèles
existants sont construits sur un texte incomplet (sans les lots) — il faut
les régénérer, pas les corriger en place. `schema_v6_lots.sql` inclut un
bloc de reset explicite (à exécuter une seule fois) :
- `appels_offres_features` **vidée entièrement** — force le recalcul de
  tous les embeddings/features (lots inclus) au prochain accès (seul
  mécanisme d'invalidation existant du cache), et remet `deja_appris` à
  zéro (cohérent avec le reset des modèles, sinon un modèle neuf ne
  pourrait plus jamais réapprendre sur des avis déjà marqués appris).
- `modele_preference_etat` (A et B) remis à l'état neuf : cold start
  recommence pour tout le monde.
- `appels_offres.score_modele_a/b` remis à `NULL`.
- **`swipes`/`raisons` ne sont volontairement PAS touchés** : c'est
  l'historique/l'audit humain, pas le scoring. Les anciens swipes ne sont
  pas rejoués automatiquement dans les modèles neufs — seuls les swipes à
  venir comptent, en repartant de zéro.

## Limites connues

- Pas de gestion de concept drift (reporté, comme décidé à l'étape 5 initiale).
- `descripteur_libelle`/`type_procedure`/`nature_libelle` vides pour les
  avis TED (modèle B légèrement désavantagé sur ces avis).
- Le kNN (`match_swipes_proches`) ne devient utile qu'une fois qu'il y a
  des swipes en base — neutre (0.5) sinon.
- `description` absente sur les avis BOAMP anciens (pas de blob `donnees`
  structuré) — l'avis reste utilisable, juste avec moins de contexte affiché
  et un texte d'embedding un peu plus court.
- Identification par nom libre, sans mot de passe : n'importe qui peut
  swiper "sous le nom" de quelqu'un d'autre en le tapant — acceptable pour
  un usage interne restreint, mais pas une vraie authentification.
- Anti-doublons "premier swipe fait foi" (voir section dédiée ci-dessus) :
  un swipe erroné dès le premier passage sur un avis reste appris
  définitivement, sans mécanisme de correction/désapprentissage.
- `LIMIT_PAR_SOURCE` (250) est un plafond sur le brut avant filtrage : si les
  volumes BOAMP/TED pour 974+976 dépassaient significativement les ~180/~125
  vérifiés, certains avis pourraient être tronqués avant même le filtrage
  mots-clés/lots — à surveiller si le périmètre géographique s'élargit.

## Déploiement — rendre le site accessible à toute l'équipe

Voir la section dédiée en bas de ce fichier ou demander à Claude — objectif :
un lien unique, accessible sans installer Python, pour tous les employés.
Option recommandée : **Streamlit Community Cloud** (gratuit, quasi zéro
config). Alternative : conteneur Docker sur un petit serveur/VM si le projet
doit rester privé hors GitHub.
