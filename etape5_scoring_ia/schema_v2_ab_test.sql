-- ============================================================
-- Étape 5 (extension) — A/B test de deux architectures de scoring
-- À exécuter APRÈS schema.sql (nécessite les tables appels_offres_features,
-- swipes, modele_preference_etat déjà créées).
-- ============================================================

-- Deux scores au lieu d'un, pour comparaison le temps du test.
-- L'ancienne colonne `score` n'est plus mise à jour par le pipeline mais
-- reste en base (dépréciée, pas supprimée pour ne rien casser).
ALTER TABLE appels_offres ADD COLUMN IF NOT EXISTS score_modele_a real;
ALTER TABLE appels_offres ADD COLUMN IF NOT EXISTS score_modele_b real;

-- Champs BOAMP officiels réintégrés (nullable ; vides pour les avis TED,
-- qui n'ont pas d'équivalent direct collecté actuellement).
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS type_procedure text;
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS nature_libelle text;
ALTER TABLE appels_offres_features ADD COLUMN IF NOT EXISTS descripteur_libelle text[];

-- modele_preference_etat : une ligne par modèle ('A' / 'B') au lieu d'une
-- ligne fixe id=1, + état propre au modèle B (centroïdes like/dislike).
ALTER TABLE modele_preference_etat DROP CONSTRAINT IF EXISTS modele_preference_etat_pkey;
-- le CHECK (id = 1) d'origine (schema.sql) doit sauter avant le changement de
-- type, sinon Postgres essaie de le revalider en comparant text = integer.
ALTER TABLE modele_preference_etat DROP CONSTRAINT IF EXISTS modele_preference_etat_id_check;
ALTER TABLE modele_preference_etat ALTER COLUMN id DROP DEFAULT;
ALTER TABLE modele_preference_etat ALTER COLUMN id TYPE text USING 'A';
ALTER TABLE modele_preference_etat RENAME COLUMN id TO nom_modele;
ALTER TABLE modele_preference_etat ADD PRIMARY KEY (nom_modele);
ALTER TABLE modele_preference_etat ADD COLUMN IF NOT EXISTS centroide_like vector(1024);
ALTER TABLE modele_preference_etat ADD COLUMN IF NOT EXISTS centroide_dislike vector(1024);
ALTER TABLE modele_preference_etat ADD COLUMN IF NOT EXISTS nb_like integer NOT NULL DEFAULT 0;
ALTER TABLE modele_preference_etat ADD COLUMN IF NOT EXISTS nb_dislike integer NOT NULL DEFAULT 0;

-- Initialise la ligne du modèle B (celle du modèle A garde l'ancien id=1 -> 'A')
INSERT INTO modele_preference_etat (nom_modele) VALUES ('B')
ON CONFLICT (nom_modele) DO NOTHING;


-- ------------------------------------------------------------
-- Profil de recherche pour le modèle A (texte écrit à la main +
-- son embedding, calculé une fois puis mis en cache).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profil_cible (
    id         integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    texte      text NOT NULL,
    embedding  vector(1024),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profil_cible ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique profil" ON profil_cible FOR SELECT USING (true);
CREATE POLICY "Ecriture publique profil" ON profil_cible FOR INSERT WITH CHECK (true);
CREATE POLICY "MAJ publique profil" ON profil_cible FOR UPDATE USING (true);


-- ------------------------------------------------------------
-- Fonction RPC : k plus proches voisins d'un vecteur PARMI les avis déjà
-- swipés (jointure swipes + appels_offres_features), pour le modèle B.
-- Exposée automatiquement en RPC par PostgREST/Supabase.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_swipes_proches(vecteur vector(1024), k integer)
RETURNS TABLE(appel_offre_id uuid, decision text, similarite float)
LANGUAGE sql STABLE AS $$
    SELECT s.appel_offre_id, s.decision, 1 - (f.embedding <=> vecteur) AS similarite
    FROM swipes s
    JOIN appels_offres_features f ON f.appel_offre_id = s.appel_offre_id
    WHERE f.embedding IS NOT NULL
    ORDER BY f.embedding <=> vecteur
    LIMIT k;
$$;
