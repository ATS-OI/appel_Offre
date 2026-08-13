-- ============================================================================
-- schema.sql — schéma Supabase complet de l'étape 7 (base rase)
-- ============================================================================
-- À exécuter UNE FOIS sur un projet Supabase vidé de tout (voir README.md).
-- Contient TOUT ce dont le site a besoin, rien de plus :
--   - le stockage des avis (appels_offres) et de leur détail par lot,
--   - le cache d'embedding par avis (appels_offres_features),
--   - l'historique des décisions humaines (swipes = signal du scoring KNN,
--     raisons = historique "métier" avec commentaire),
--   - les listes partagées qui pilotent la recherche (mots_cles,
--     mots_cles_lots, acheteurs_suivis),
--   - la fonction de recherche KNN par similarité d'embedding.
--
-- Pas de modèle entraîné à sauvegarder ici (voir scoring.py) : la méthode
-- KNN n'a rien à apprendre à l'avance, un embedding par avis suffit.
-- ============================================================================

create extension if not exists vector;

-- ----------------------------------------------------------------------------
-- appels_offres — un avis = une ligne (BOAMP/TED/AWS/PLACE fusionnés)
-- ----------------------------------------------------------------------------
create table if not exists appels_offres (
    id                    uuid primary key default gen_random_uuid(),
    identifiants          text not null,              -- identifiants d'origine (une ou plusieurs sources), unique
    objet                 text not null,
    description           text,
    lots                  jsonb not null default '[]', -- [{"identifiant","titre","description"}, ...]
    source                text not null,               -- ex. "BOAMP+TED"
    acheteur              text,
    departement           text[] not null default '{}',
    date_parution         date,
    date_limite_reponse   date,
    urls                  text,
    nb_versions           integer not null default 1,
    score                 real,                        -- score final (heuristique ou KNN, voir scoring.py) — NULL = pas encore calculé
    decision              text not null default 'n/A', -- dernière décision connue, tous utilisateurs confondus (informatif uniquement)
    created_at            timestamptz not null default now(),
    unique (identifiants)
);

-- ----------------------------------------------------------------------------
-- appels_offres_features — cache de l'embedding (coûteux à recalculer)
-- ----------------------------------------------------------------------------
create table if not exists appels_offres_features (
    appel_offre_id  uuid primary key references appels_offres(id) on delete cascade,
    embedding       vector(1024) not null
);

-- ----------------------------------------------------------------------------
-- swipes — un swipe = un exemple pour le KNN (comparé aux embeddings)
-- ----------------------------------------------------------------------------
create table if not exists swipes (
    id              uuid primary key default gen_random_uuid(),
    appel_offre_id  uuid not null references appels_offres(id) on delete cascade,
    user_id         text not null,
    decision        text not null check (decision in ('like', 'dislike')),
    created_at      timestamptz not null default now(),
    unique (appel_offre_id, user_id)  -- un même utilisateur ne compte qu'une fois par avis
);

-- ----------------------------------------------------------------------------
-- raisons — historique "métier" (décision d'origine + commentaire libre)
-- ----------------------------------------------------------------------------
create table if not exists raisons (
    id              uuid primary key default gen_random_uuid(),
    appel_offre_id  uuid not null references appels_offres(id) on delete cascade,
    decision        text not null,       -- "accepted" / "rejected" (valeur affichée sur le site, pas binarisée)
    commentaire     text,
    user_id         text not null,
    created_at      timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- Listes partagées (identiques pour tout le monde) qui pilotent la recherche
-- ----------------------------------------------------------------------------
create table if not exists mots_cles (
    mot         text primary key,
    ajoute_le   timestamptz not null default now()
);

create table if not exists mots_cles_lots (
    mot         text primary key,
    ajoute_le   timestamptz not null default now()
);

create table if not exists acheteurs_suivis (
    nom         text primary key,
    ajoute_le   timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- RPC : k plus proches voisins déjà swipés d'un embedding donné
-- ----------------------------------------------------------------------------

-- Ajoutez cette ligne pour forcer la suppression de l'ancienne version :
DROP FUNCTION IF EXISTS match_swipes_proches(vector, int, uuid);

create or replace function match_swipes_proches(vecteur vector(1024), k int, exclu uuid default null)
returns table (appel_offre_id uuid, decision text, similarite real)
language sql stable
as $$
    with premier_swipe_par_offre as (
        select distinct on (s.appel_offre_id)
            s.appel_offre_id, s.decision
        from swipes s
        where s.appel_offre_id is distinct from exclu
        order by s.appel_offre_id, s.created_at asc
    )
    select p.appel_offre_id, p.decision, (1 - (f.embedding <=> vecteur))::real as similarite
    from premier_swipe_par_offre p
    join appels_offres_features f on f.appel_offre_id = p.appel_offre_id
    order by f.embedding <=> vecteur
    limit k;
$$;
-- ----------------------------------------------------------------------------
-- RLS — pas d'authentification, accès public en lecture/écriture
-- ----------------------------------------------------------------------------
-- Note apprise sur l'étape 5 : un upsert (INSERT ... ON CONFLICT DO UPDATE)
-- a besoin à la fois d'une policy INSERT *et* d'une policy UPDATE, sinon il
-- échoue silencieusement dès qu'un conflit réel se présente. Les 4 policies
-- ci-dessous sont donc complètes dès le départ.

alter table appels_offres enable row level security;
alter table appels_offres_features enable row level security;
alter table swipes enable row level security;
alter table raisons enable row level security;
alter table mots_cles enable row level security;
alter table mots_cles_lots enable row level security;
alter table acheteurs_suivis enable row level security;

create policy "public select" on appels_offres for select using (true);
create policy "public insert" on appels_offres for insert with check (true);
create policy "public update" on appels_offres for update using (true) with check (true);

create policy "public select" on appels_offres_features for select using (true);
create policy "public insert" on appels_offres_features for insert with check (true);
create policy "public update" on appels_offres_features for update using (true) with check (true);

create policy "public select" on swipes for select using (true);
create policy "public insert" on swipes for insert with check (true);
create policy "public update" on swipes for update using (true) with check (true);

create policy "public select" on raisons for select using (true);
create policy "public insert" on raisons for insert with check (true);

create policy "public select" on mots_cles for select using (true);
create policy "public insert" on mots_cles for insert with check (true);
create policy "public update" on mots_cles for update using (true) with check (true);
create policy "public delete" on mots_cles for delete using (true);

create policy "public select" on mots_cles_lots for select using (true);
create policy "public insert" on mots_cles_lots for insert with check (true);
create policy "public update" on mots_cles_lots for update using (true) with check (true);
create policy "public delete" on mots_cles_lots for delete using (true);

create policy "public select" on acheteurs_suivis for select using (true);
create policy "public insert" on acheteurs_suivis for insert with check (true);
create policy "public update" on acheteurs_suivis for update using (true) with check (true);
create policy "public delete" on acheteurs_suivis for delete using (true);

-- ----------------------------------------------------------------------------
-- Listes par défaut — périmètre de recherche connu de l'équipe.
-- ----------------------------------------------------------------------------
-- Gardées telles quelles (accents, casse, espaces d'origine) : la comparaison
-- au moment du filtrage (sources/commun.py::normaliser_texte) ignore déjà
-- accents/casse/espaces multiples des deux côtés, donc l'orthographe exacte
-- ici n'a pas d'importance pour le matching — seule la présence du mot
-- compte. `on conflict do nothing` : rejouable sans dupliquer si la table
-- contient déjà des valeurs (ajoutées depuis le site).

insert into mots_cles (mot) values
    ('rénovation'), ('construction'), ('école'), ('collège'), ('lycée'),
    ('université'), ('laboratoire'), ('logement'), ('paillasse'), ('cuisine'),
    ('placard'), ('agencement'), ('mobilier'), ('infrastructure'), ('réhabilitation'),
    ('salle de classe'), ('salle de bain'), ('meuble'), ('cloison sanitaire'),
    ('saniclip'), ('équipement spécialisé'), ('materiel de laboratoire'),
    ('plan vasque'), ('aménagement'), ('menuiserie'), ('plan de travail'),
    ('restructuration')
on conflict (mot) do nothing;

insert into acheteurs_suivis (nom) values
    ('SIDR'), ('CBO'), ('SHLMR'), ('SEDRE'), ('SPAG'), ('ICADE'), ('OPALE'),
    ('JWH'), ('CIRAD')
on conflict (nom) do nothing;

-- Exemples réels de libellés de lots déjà vus sur des marchés pertinents —
-- gardés tels quels (voir remarque ci-dessus), seuls les doublons exacts
-- (une fois accents/casse/espaces ignorés) ont été retirés.
insert into mots_cles_lots (mot) values
    ('menuiseries bois'), ('menuiseries intérieures'), ('Meuble cuisine SDB Placard'),
    ('AMENAGEMENT'), ('MOBILIERS'), ('CUISINES'), ('AMÉNAGEMENT INTÉRIEUR'),
    ('MENUISERIES INTERIEURES BOIS'), ('MEUBLES DE CUISINE - PLACARDS'),
    ('Meuble cuisine / Placards'), ('Agencement'), ('MOBILIER CUISINE - PLACARD'),
    ('Equipements des salles spécialisées'), ('EQUIPEMENTS SALLES SPECIALISEES'),
    ('MENUISERIE BOIS'), ('MOBILIER'), ('Agencement cuisine'),
    ('Cloisons légères / Aménagements - Mobiliers intégrés'), ('mobilier cuisine'),
    ('Aménagements'), ('Placard'), ('Equipements des salles sciences spécialisées'),
    ('Mobilier d’agencement'), ('Paillasses'), ('MOBILIER SUR MESURE'),
    ('Meubles cuisine/placards/salle de bain'), ('MEUBLES CUISINES-SDB ET PLACARDS')
on conflict (mot) do nothing;
