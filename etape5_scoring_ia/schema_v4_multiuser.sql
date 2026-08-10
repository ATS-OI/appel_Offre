-- ============================================================
-- Étape 5 (extension) — indépendance des décisions par utilisateur +
-- table mots_cles_lots (préparation, pas encore branchée sur la recherche).
-- À exécuter APRÈS schema.sql, schema_v2_ab_test.sql, schema_v3_description.sql.
-- ============================================================

-- `raisons` garde l'historique des décisions avec commentaire ; on y ajoute
-- `user_id` pour savoir QUI a pris quelle décision (cohérent avec `swipes`,
-- qui l'avait déjà). Nécessaire pour que chaque utilisateur ait sa propre
-- file de tri, indépendante des autres (voir recap.md).
ALTER TABLE raisons ADD COLUMN IF NOT EXISTS user_id text NOT NULL DEFAULT 'anonyme';


-- ------------------------------------------------------------
-- mots_cles_lots : liste partagée, même forme que `mots_cles`, pas encore
-- utilisée dans la construction des requêtes BOAMP/TED (prévu pour plus
-- tard — ex. filtrer/qualifier au niveau des lots d'un marché plutôt que
-- de l'avis entier). Gérable dès maintenant depuis le site.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mots_cles_lots (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mot         text NOT NULL UNIQUE,
    date_ajout  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE mots_cles_lots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique mots_cles_lots" ON mots_cles_lots FOR SELECT USING (true);
CREATE POLICY "Insertion publique mots_cles_lots" ON mots_cles_lots FOR INSERT WITH CHECK (true);
CREATE POLICY "Suppression publique mots_cles_lots" ON mots_cles_lots FOR DELETE USING (true);
