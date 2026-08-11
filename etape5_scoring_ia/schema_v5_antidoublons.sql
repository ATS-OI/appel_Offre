-- ============================================================
-- Étape 5 (extension) — anti-doublons / anti-fuite de l'entraînement.
-- À exécuter APRÈS schema.sql, schema_v2_ab_test.sql, schema_v3_description.sql,
-- schema_v4_multiuser.sql. Voir recap.md, section "Anti-doublons / anti-fuite".
-- ============================================================

-- ------------------------------------------------------------
-- 1) + 3) Un avis (ou un quasi-doublon détecté par similarité d'embedding)
-- n'est appris qu'UNE SEULE FOIS par les modèles A/B, quel que soit le
-- nombre d'utilisateurs qui le swipent — évite la sur-pondération du
-- centroïde et du gradient de la régression logistique.
-- ------------------------------------------------------------
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS deja_appris boolean NOT NULL DEFAULT false;

-- `true` s'il existe déjà un avis marqué `deja_appris` dont l'embedding est
-- très proche de `vecteur` (>= seuil de similarité cosinus). Pas besoin
-- d'exclure l'avis courant : tant qu'il n'a pas encore été appris, sa
-- propre ligne n'est pas encore à `true`, donc aucun faux positif sur
-- lui-même au premier passage.
CREATE OR REPLACE FUNCTION existe_apprentissage_proche(vecteur vector(1024), seuil real DEFAULT 0.97)
RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1
        FROM appels_offres_features f
        WHERE f.deja_appris
          AND f.embedding IS NOT NULL
          AND 1 - (f.embedding <=> vecteur) > seuil
    );
$$;


-- ------------------------------------------------------------
-- 2) Fuite du kNN (modèle B) : `match_swipes_proches` doit pouvoir exclure
-- l'avis en cours de traitement de ses propres voisins (sinon un avis déjà
-- swipé par un autre utilisateur se retrouve son propre voisin à
-- similarité 1.0, ce qui fuit directement l'étiquette dans sa propre
-- feature). Redéfinition avec un paramètre `exclu` optionnel.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_swipes_proches(vecteur vector(1024), k integer, exclu uuid DEFAULT NULL)
RETURNS TABLE(appel_offre_id uuid, decision text, similarite float)
LANGUAGE sql STABLE AS $$
    SELECT s.appel_offre_id, s.decision, 1 - (f.embedding <=> vecteur) AS similarite
    FROM swipes s
    JOIN appels_offres_features f ON f.appel_offre_id = s.appel_offre_id
    WHERE f.embedding IS NOT NULL
      AND (exclu IS NULL OR s.appel_offre_id IS DISTINCT FROM exclu)
    ORDER BY f.embedding <=> vecteur
    LIMIT k;
$$;


-- ------------------------------------------------------------
-- 5) Double-swipe accidentel (double clic, re-render Streamlit) : un même
-- utilisateur ne peut avoir qu'UNE ligne par avis dans `swipes`. Le code
-- Python passe d'un `insert` à un `upsert` sur ce couple.
-- ------------------------------------------------------------
-- Postgres n'a pas de `ADD CONSTRAINT IF NOT EXISTS` : on passe par un bloc
-- qui avale l'erreur "déjà existante" pour rester ré-exécutable sans risque.
DO $$ BEGIN
    ALTER TABLE swipes ADD CONSTRAINT swipes_offre_user_unique UNIQUE (appel_offre_id, user_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
