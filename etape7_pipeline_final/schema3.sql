-- ============================================================================
-- schema3.sql — migration additive : historique des recherches lancées
-- ============================================================================
-- À exécuter UNE FOIS, après schema.sql et schema2.sql, sur la même base
-- Supabase. Sans risque à rejouer (`if not exists`).
--
-- Sert à l'onglet "🆕 Nouveautés" (voir app.py) : chaque exécution de
-- "🔍 Lancer la recherche" enregistre une ligne ici (voir
-- pipeline.py::lancer_recherche) — ça sert de repère temporel pour ne
-- montrer que les avis trouvés récemment, sans jamais perdre de vue les
-- résultats d'une recherche précédente lancée plus tôt dans la journée.
-- ============================================================================

create table if not exists recherches (
    id          uuid primary key default gen_random_uuid(),
    lancee_le   timestamptz not null default now()
);

alter table recherches enable row level security;

create policy "public select" on recherches for select using (true);
create policy "public insert" on recherches for insert with check (true);
