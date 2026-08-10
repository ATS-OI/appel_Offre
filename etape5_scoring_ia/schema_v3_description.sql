-- ============================================================
-- Étape 5 (extension) — description longue de la mission
-- À exécuter APRÈS schema.sql et schema_v2_ab_test.sql.
-- ============================================================

ALTER TABLE appels_offres ADD COLUMN IF NOT EXISTS description text;
