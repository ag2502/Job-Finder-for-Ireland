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


-- ------------------------------------------------------------------ profiles
--
-- One row per account: what the searcher told us about themselves, and what was read
-- from their CV, so the CV goes up once rather than on every search.
--
-- **The CV document is still never stored.** `cv` holds the reading of it - skill
-- keywords, the fields it points to, a seniority guess, a rough number of years, a
-- one-line summary, and the file's name and size so the profile can show which CV it
-- is. The file itself is parsed in memory and dropped, exactly as before. Removing the
-- CV sets `cv` to null, which deletes the reading outright.

create table if not exists public.profiles (
    user_id          uuid primary key references auth.users (id) on delete cascade,

    -- What they want next: field keys from `jobfinder.normalize.taxonomy.FIELDS`.
    fields           text[] not null default '{}',
    -- Years of experience as they stated it. Null means not stated.
    years            smallint check (years between 0 and 50),
    include_remote   boolean not null default false,
    internships_only boolean not null default false,
    graduate_only    boolean not null default false,

    -- The reading of their CV, or null when there is none.
    cv               jsonb,

    updated_at       timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "read own profile"   on public.profiles;
drop policy if exists "insert own profile" on public.profiles;
drop policy if exists "update own profile" on public.profiles;
drop policy if exists "delete own profile" on public.profiles;

create policy "read own profile"
    on public.profiles for select
    using (auth.uid() = user_id);

create policy "insert own profile"
    on public.profiles for insert
    with check (auth.uid() = user_id);

create policy "update own profile"
    on public.profiles for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own profile"
    on public.profiles for delete
    using (auth.uid() = user_id);


-- ------------------------------------------------------------------ CV files
--
-- The CV document itself, and every CV tailored from it, as files in a private bucket.
-- Kept since 2026-09-25 at the owner's request: tailoring a CV to a job keeps its
-- layout, and that needs the original document, not only the reading of it.
--
-- Each account's files live under a folder named for its user id - `<uid>/original/`
-- for the CV they uploaded, `<uid>/tailored/` for the versions made for particular
-- jobs - and the policies below let an account touch its own folder and nothing else.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'cvs', 'cvs', false, 5242880,
    array[
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword',
        'text/plain'
    ]
)
on conflict (id) do nothing;

drop policy if exists "read own cv files"   on storage.objects;
drop policy if exists "insert own cv files" on storage.objects;
drop policy if exists "update own cv files" on storage.objects;
drop policy if exists "delete own cv files" on storage.objects;

create policy "read own cv files"
    on storage.objects for select to authenticated
    using (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "insert own cv files"
    on storage.objects for insert to authenticated
    with check (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "update own cv files"
    on storage.objects for update to authenticated
    using (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text)
    with check (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "delete own cv files"
    on storage.objects for delete to authenticated
    using (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text);


-- -------------------------------------------------------------- tailored CVs
--
-- A CV rewritten for one job. Kept apart from `profiles.cv` on purpose: searching
-- ranks against the CV the person uploaded and never against a version bent toward
-- one advert.
--
-- A row starts as a `draft` while the person reviews it and suggests changes, and
-- becomes `saved` when they keep it, at which point the finished file is written to
-- storage. Drafts that are cancelled are deleted; ones simply abandoned are cleared
-- after a week.

create table if not exists public.tailored_cvs (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references auth.users (id) on delete cascade,
    status       text not null default 'draft' check (status in ('draft', 'saved')),

    -- The job it was made for, stored rather than looked up, for the same reason
    -- applications store theirs: the advert leaves the snapshot when it closes.
    advert_key   text not null default '',
    job_title    text not null,
    company      text not null,
    job_url      text not null default '',
    job_text     text not null,

    -- The CV it was made from, and what was changed in it: the paragraph edits are the
    -- tailoring, and the file is rebuilt from the source with them applied.
    source_path  text not null,
    source_name  text not null,
    edits        jsonb not null default '{}'::jsonb,
    report       jsonb not null default '{}'::jsonb,
    ats_before   smallint,
    ats_after    smallint,
    rounds       smallint not null default 0,

    -- Set once saved: the finished file in the bucket, and what to call it.
    file_path    text,
    file_name    text,

    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists tailored_cvs_user_created_idx
    on public.tailored_cvs (user_id, created_at desc);

alter table public.tailored_cvs enable row level security;

drop policy if exists "read own tailored cvs"   on public.tailored_cvs;
drop policy if exists "insert own tailored cvs" on public.tailored_cvs;
drop policy if exists "update own tailored cvs" on public.tailored_cvs;
drop policy if exists "delete own tailored cvs" on public.tailored_cvs;

create policy "read own tailored cvs"
    on public.tailored_cvs for select
    using (auth.uid() = user_id);

create policy "insert own tailored cvs"
    on public.tailored_cvs for insert
    with check (auth.uid() = user_id);

create policy "update own tailored cvs"
    on public.tailored_cvs for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own tailored cvs"
    on public.tailored_cvs for delete
    using (auth.uid() = user_id);
