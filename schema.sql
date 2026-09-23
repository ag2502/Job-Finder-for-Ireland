-- Supabase schema for the finder's accounts and application history.
--
-- Run once, in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
-- It is safe to run again; every statement is guarded.
--
-- Accounts themselves need no table here. Supabase keeps them in `auth.users`, which it
-- manages, and this file only adds what that does not cover: which adverts a given
-- account has applied to.

create table if not exists public.applications (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,

    -- Identifies the *advert*, not the row that carried it. The same opening reaches
    -- the crawler from an employer's own board and from an aggregator, and the snapshot
    -- is rebuilt every six hours, so a posting id would stop matching almost at once.
    -- This is the hash of normalised company + canonical title + location that
    -- `jobfinder.normalize.dedup.compute_dedup_key` produces, which is what the search
    -- itself uses to decide two rows are the same job.
    advert_key  text not null,

    -- Kept alongside the key rather than looked up when the page is rendered. The
    -- snapshot carries only open Dublin roles, so an advert leaves it within days of
    -- being closed, and a history that blanked out as jobs closed would be worth little.
    title       text not null,
    company     text not null,
    url         text not null,

    applied_at  timestamptz not null default now(),

    -- Marking the same advert twice is an ordinary thing to do - clicking Apply again
    -- after coming back to it - so the second write updates rather than fails. The
    -- client relies on this with `Prefer: resolution=merge-duplicates`.
    unique (user_id, advert_key)
);

-- Every read is "this account's applications, newest first".
create index if not exists applications_user_applied_at_idx
    on public.applications (user_id, applied_at desc);

-- Row level security is what actually keeps one account out of another's history. The
-- application code always calls PostgREST with the signed-in person's own token and
-- never with the service role key, so these policies are enforced by the database
-- rather than by remembering to write `where user_id = ...` on every query.
alter table public.applications enable row level security;

drop policy if exists "read own applications"   on public.applications;
drop policy if exists "insert own applications" on public.applications;
drop policy if exists "update own applications" on public.applications;
drop policy if exists "delete own applications" on public.applications;

create policy "read own applications"
    on public.applications for select
    using (auth.uid() = user_id);

-- `with check` is the half that matters on writes: it stops an account inserting a row
-- carrying somebody else's user_id.
create policy "insert own applications"
    on public.applications for insert
    with check (auth.uid() = user_id);

create policy "update own applications"
    on public.applications for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own applications"
    on public.applications for delete
    using (auth.uid() = user_id);


-- ---------------------------------------------------------------- saved jobs
--
-- The other half of the same idea. `applications` records what has been acted on and
-- takes those adverts out of the results; `saved_jobs` records what someone wants to
-- come back to and deliberately leaves them in. A saved job is still a live opening
-- the searcher may want to compare against everything else.
--
-- Same shape as `applications` for the same reasons: the advert key rather than a
-- posting id, and the title/company/url stored alongside it so a saved list does not
-- blank out as the snapshot is rebuilt.

create table if not exists public.saved_jobs (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    advert_key  text not null,
    title       text not null,
    company     text not null,
    url         text not null,
    saved_at    timestamptz not null default now(),
    unique (user_id, advert_key)
);

create index if not exists saved_jobs_user_saved_at_idx
    on public.saved_jobs (user_id, saved_at desc);

alter table public.saved_jobs enable row level security;

drop policy if exists "read own saved jobs"   on public.saved_jobs;
drop policy if exists "insert own saved jobs" on public.saved_jobs;
drop policy if exists "update own saved jobs" on public.saved_jobs;
drop policy if exists "delete own saved jobs" on public.saved_jobs;

create policy "read own saved jobs"
    on public.saved_jobs for select
    using (auth.uid() = user_id);

create policy "insert own saved jobs"
    on public.saved_jobs for insert
    with check (auth.uid() = user_id);

create policy "update own saved jobs"
    on public.saved_jobs for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own saved jobs"
    on public.saved_jobs for delete
    using (auth.uid() = user_id);
