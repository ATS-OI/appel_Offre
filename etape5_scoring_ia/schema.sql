-- ============================================================
-- Étape 5 — scoring par apprentissage de préférence
-- À exécuter dans le SQL Editor de Supabase.
-- ============================================================

-- Extension nécessaire pour stocker les embeddings nativement
CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- Étage 1 : cache des features calculées par avis (pour ne pas
-- recalculer l'embedding/les features à chaque prédiction).
-- ------------------------------------------------------------
CREATE TABLE appels_offres_features (
    appel_offre_id    uuid PRIMARY KEY REFERENCES appels_offres(id) ON DELETE CASCADE,
    embedding         vector(1024),       -- multilingual-e5-large
    jours_urgence     integer,            -- date_limite_reponse - aujourd'hui
    jours_fraicheur   integer,            -- aujourd'hui - date_parution
    score_mots_cles   real,               -- correspondance avec mots_cles (comptage)
    departement       text[],
    source            text,
    longueur_objet    integer,
    calcule_le        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE appels_offres_features ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique features" ON appels_offres_features FOR SELECT USING (true);
CREATE POLICY "Ecriture publique features" ON appels_offres_features FOR INSERT WITH CHECK (true);
CREATE POLICY "MAJ publique features" ON appels_offres_features FOR UPDATE USING (true);


-- ------------------------------------------------------------
-- Étage 2 : journal des swipes (like/dislike), toutes personnes
-- confondues — alimente le modèle partagé unique. Distinct de
-- `raisons` (étape 4, historique métier accepted/rejected/... +
-- commentaire) : `swipes` est dédié à l'entraînement du modèle,
-- décision binaire stricte.
-- ------------------------------------------------------------
CREATE TABLE swipes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    appel_offre_id  uuid NOT NULL REFERENCES appels_offres(id) ON DELETE CASCADE,
    user_id         text NOT NULL DEFAULT 'anonyme',
    decision        text NOT NULL CHECK (decision IN ('like', 'dislike')),
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE swipes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique swipes" ON swipes FOR SELECT USING (true);
CREATE POLICY "Insertion publique swipes" ON swipes FOR INSERT WITH CHECK (true);


-- ------------------------------------------------------------
-- Persistance du modèle River partagé (une seule ligne, id fixe
-- = 1). pickle_b64 = modèle sérialisé (pickle) encodé en base64.
-- ------------------------------------------------------------
CREATE TABLE modele_preference_etat (
    id            integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    pickle_b64    text,
    nb_swipes_vus integer NOT NULL DEFAULT 0,
    version       integer NOT NULL DEFAULT 1,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE modele_preference_etat ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique modele" ON modele_preference_etat FOR SELECT USING (true);
CREATE POLICY "Ecriture publique modele" ON modele_preference_etat FOR INSERT WITH CHECK (true);
CREATE POLICY "MAJ publique modele" ON modele_preference_etat FOR UPDATE USING (true);
