-- ============================================================
-- Étape 5 (extension) — lots BOAMP/TED dans le stockage, le filtrage et le
-- scoring. À exécuter APRÈS schema.sql, schema_v2_ab_test.sql,
-- schema_v3_description.sql, schema_v4_multiuser.sql, schema_v5_antidoublons.sql.
-- Voir recap.md, section "Lots dans le scoring".
-- ============================================================

-- Détail des lots de chaque avis (BOAMP `cac:ProcurementProjectLot` /
-- TED `title-lot`+`description-lot`+`identifier-lot`), sous la forme
-- [{"identifiant", "titre", "description"}, ...] — éventuellement vide
-- (marché sans lots, ou source sans info exploitable). Colonne directe sur
-- `appels_offres` (contrairement à type_procedure/nature_libelle, qui ne
-- sont PAS des colonnes) : circule automatiquement partout où l'avis est lu.
ALTER TABLE appels_offres ADD COLUMN IF NOT EXISTS lots jsonb;

-- Nouvelles features structurées (voir features.calculer_features_structurees) :
-- score_mots_cles_lots (correspondance mots_cles_lots sur le texte des lots)
-- et nb_lots (nombre de lots). Colonnes du cache de features, comme
-- score_mots_cles/longueur_objet déjà présents.
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS score_mots_cles_lots real;
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS nb_lots integer;


-- ============================================================
-- RESET — à exécuter UNE SEULE FOIS, consciemment, juste après avoir
-- déployé le code de cette extension. Remet le SCORING à zéro pour tout le
-- monde (cold start recommence pour les deux modèles A/B), sans toucher à
-- l'historique humain (`swipes`/`raisons` intacts) — voir recap.md
-- "Reset du scoring (lots)" pour le détail du raisonnement.
--
-- Nécessaire car le texte embarqué (embedding) change avec cette extension
-- (ajout du texte des lots) : les embeddings/modèles actuels sont construits
-- sur un texte incomplet, il faut les régénérer plutôt que les corriger en
-- place.
-- ============================================================

-- 1) Vide le cache de features : force le recalcul de TOUS les
--    embeddings/features (lots inclus) au prochain accès — c'est déjà le
--    seul mécanisme d'invalidation existant (`extraire_et_stocker_features`
--    ne recalcule que si la ligne est absente). Remet aussi `deja_appris` à
--    zéro pour tout le monde (cohérent avec le reset des modèles ci-dessous).
TRUNCATE TABLE appels_offres_features;

-- 2) Remet les deux modèles ('A' et 'B') à l'état neuf : cold start
--    recommence pour tout le monde (SEUIL_COLD_START swipes à nouveau
--    nécessaires avant de sortir de l'heuristique de secours).
UPDATE modele_preference_etat
SET pickle_b64 = NULL,
    nb_swipes_vus = 0,
    nb_like = 0,
    nb_dislike = 0,
    centroide_like = NULL,
    centroide_dislike = NULL;

-- 3) Les anciens scores affichés n'ont plus de sens (calculés avec l'ancien
--    texte/l'ancien modèle) : redeviennent "non calculé" à l'affichage
--    jusqu'au prochain "🔄 Recalculer les scores".
UPDATE appels_offres SET score_modele_a = NULL, score_modele_b = NULL;

-- `swipes` et `raisons` ne sont PAS touchés : c'est l'historique/l'audit des
-- décisions humaines, pas le scoring. Les swipes déjà enregistrés ne sont
-- pas rejoués automatiquement dans les modèles neufs (pas de réentraînement
-- rétroactif) — seuls les swipes à venir comptent à nouveau, en repartant
-- de zéro.
