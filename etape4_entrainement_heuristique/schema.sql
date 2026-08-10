-- ============================================================
-- Étape 4 — mots-clés / acheteurs partagés + historique des raisons
-- À exécuter dans le SQL Editor de Supabase.
-- ============================================================

-- ------------------------------------------------------------
-- Mots-clés recherchés dans l'objet des avis (liste partagée,
-- source unique de vérité pour la recherche BOAMP/TED ET pour le score).
-- ------------------------------------------------------------
CREATE TABLE mots_cles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mot         text NOT NULL UNIQUE,
    date_ajout  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE mots_cles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Lecture publique mots_cles" ON mots_cles FOR SELECT USING (true);
CREATE POLICY "Insertion publique mots_cles" ON mots_cles FOR INSERT WITH CHECK (true);
CREATE POLICY "Suppression publique mots_cles" ON mots_cles FOR DELETE USING (true);

INSERT INTO mots_cles (mot) VALUES
    ('rénovation'), ('construction'), ('école'), ('collège'), ('lycée'),
    ('université'), ('laboratoire'), ('paillasse'), ('cuisine'),
    ('placard'), ('agencement'), ('mobilier'), ('infrastructure'),
    ('réhabilitation'), ('salle de bain'), ('meuble'), ('cloison sanitaire'),
    ('saniclip'), ('équipement spécialisé'), ('matériel de laboratoire'),
    ('plan vasque'), ('aménagement'), ('menuiserie'), ('plan de travail'),
    ('restructuration')
ON CONFLICT (mot) DO NOTHING;


-- ------------------------------------------------------------
-- Acheteurs/promoteurs suivis : élargit la recherche BOAMP/TED en
-- OR du filtre mots-clés (un avis matche si son objet contient un
-- mot-clé, OU si son acheteur figure dans cette liste).
-- ------------------------------------------------------------
CREATE TABLE acheteurs_suivis (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nom         text NOT NULL UNIQUE,
    date_ajout  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE acheteurs_suivis ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Lecture publique acheteurs_suivis" ON acheteurs_suivis FOR SELECT USING (true);
CREATE POLICY "Insertion publique acheteurs_suivis" ON acheteurs_suivis FOR INSERT WITH CHECK (true);
CREATE POLICY "Suppression publique acheteurs_suivis" ON acheteurs_suivis FOR DELETE USING (true);

INSERT INTO acheteurs_suivis (nom) VALUES
    ('SIDR'), ('CBo'), ('SHLMR'), ('SEDRE'), ('SPAG'), ('ICADE'), ('OPALE'), ('JWH')
ON CONFLICT (nom) DO NOTHING;


-- ------------------------------------------------------------
-- Raisons/commentaires associés à une décision (accept/reject) sur
-- un appel d'offre. Un avis peut accumuler plusieurs raisons dans le
-- temps (historique). Supprimer l'appel d'offre supprime ses raisons.
-- ------------------------------------------------------------
CREATE TABLE raisons (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    appel_offre_id  uuid NOT NULL REFERENCES appels_offres(id) ON DELETE CASCADE,
    decision        text NOT NULL,
    commentaire     text,
    date_creation   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE raisons ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Lecture publique raisons" ON raisons FOR SELECT USING (true);
CREATE POLICY "Insertion publique raisons" ON raisons FOR INSERT WITH CHECK (true);
