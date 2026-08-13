-- ============================================================================
-- schema2.sql — migration additive : mots-clés associés à un avis
-- ============================================================================
-- À exécuter UNE FOIS, après schema.sql, sur la même base Supabase. Ne
-- touche à rien d'existant (colonne ajoutée avec une valeur par défaut) —
-- sans risque à rejouer (`if not exists`).
--
-- Sert à afficher sur la fiche (voir app.py) pourquoi un avis a été
-- proposé : quels mots-clés/lots/acheteur ont fait matcher cet avis au
-- moment de sa récupération (voir sources/commun.py::trouver_correspondances,
-- pipeline.py::formater_pour_supabase).
-- ============================================================================

alter table appels_offres
    add column if not exists mots_cles_trouves jsonb not null default '[]';
