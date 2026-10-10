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

-- When the searcher was last here, so a search can mark what is new since (added
-- 2026-09-27). `seen_at` moves on while a visit lasts; `prev_seen_at` is where the visit
-- before this one ended, and what "new since your last visit" is measured from.
alter table public.profiles add column if not exists seen_at      timestamptz;
alter table public.profiles add column if not exists prev_seen_at timestamptz;
-- The "Part-time only" switch (added 2026-10-09).
alter table public.profiles add column if not exists part_time_only boolean not null default false;

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
-- The CV document itself, as a file in a private bucket. Kept since 2026-09-25 at the
-- owner's request, so a CV can be read again when the reader improves without asking
-- for it again.
--
-- Each account's files live under a folder named for its user id (`<uid>/original/`),
-- and the policies below let an account touch its own folder and nothing else.

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


-- -------------------------------------------------------- tailored CVs (removed)
--
-- CV tailoring was removed on 2026-09-28, so its table is no longer created. Projects
-- that ran this file before then still have it, holding the tailored CVs people kept.
-- To delete them, run the line below once, then delete each account's `tailored/`
-- folder in Storage -> cvs (stored files cannot be removed from the SQL editor).
--
-- drop table if exists public.tailored_cvs;


-- -------------------------------------------------------------------- alerts
--
-- Email alerts the searcher asked for (added 2026-09-27), one row per account. Nothing
-- is sent for anything not ticked here: new internships, new graduate programmes, new
-- part-time jobs, or new jobs in the fields and years saved on the profile. Sent after a crawl by
-- `jobfinder send-alerts` in GitHub Actions, the only place the service role key lives.
--
-- `email` must be the account's own sign-in address (the policies check it against the
-- signed-in token), so nobody can sign someone else up. `token` is the one-click
-- unsubscribe link's secret; `unsubscribe_alerts` below is the only way in with it.

create table if not exists public.alerts (
    user_id      uuid primary key references auth.users (id) on delete cascade,
    email        text not null,
    internships  boolean not null default false,
    graduate     boolean not null default false,
    jobs         boolean not null default false,
    frequency    text not null default 'daily' check (frequency in ('daily', 'weekly')),
    token        uuid not null default gen_random_uuid(),
    last_sent_at timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- The "New part-time jobs" alert (added 2026-10-10).
alter table public.alerts add column if not exists part_time boolean not null default false;

alter table public.alerts enable row level security;

drop policy if exists "read own alerts"   on public.alerts;
drop policy if exists "insert own alerts" on public.alerts;
drop policy if exists "update own alerts" on public.alerts;
drop policy if exists "delete own alerts" on public.alerts;

create policy "read own alerts"
    on public.alerts for select
    using (auth.uid() = user_id);

create policy "insert own alerts"
    on public.alerts for insert
    with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

create policy "update own alerts"
    on public.alerts for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

create policy "delete own alerts"
    on public.alerts for delete
    using (auth.uid() = user_id);

-- The unsubscribe link in every email. It works without signing in, so it cannot use
-- the policies above: this function runs as its owner, and does exactly one thing with
-- the token it is given. It reveals nothing, whether or not the token matches.
create or replace function public.unsubscribe_alerts(p_token uuid)
returns void
language sql
security definer
set search_path = public
as $$
    update public.alerts
       set internships = false, graduate = false, part_time = false, jobs = false,
           updated_at = now()
     where token = p_token;
$$;

revoke all on function public.unsubscribe_alerts(uuid) from public;
grant execute on function public.unsubscribe_alerts(uuid) to anon, authenticated;


-- ------------------------------------------------------------ event reminders
--
-- "Remind me" on a careers event (added 2026-10-10). One row per account and event: an
-- email daily or weekly until the event, always one the day before, and nothing once
-- it has happened. Sent after a crawl by `jobfinder send-alerts`, like the job alerts.
--
-- `event_key` is "<source>:<listing id>" (events.listing.EventView.key), which survives
-- the snapshot being rebuilt. The title, start and link are kept alongside it so a
-- reminder can still be listed if the event leaves the listing site. `email` must be
-- the account's own sign-in address, and `token` is the secret in the email's "stop
-- this reminder" link, used only through `stop_event_reminder` below.

create table if not exists public.event_reminders (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users (id) on delete cascade,
    email           text not null,
    event_key       text not null,
    title           text not null,
    starts_at       timestamptz not null,
    url             text not null,
    frequency       text not null default 'weekly' check (frequency in ('daily', 'weekly')),
    token           uuid not null default gen_random_uuid(),
    last_sent_at    timestamptz,
    day_before_sent boolean not null default false,
    created_at      timestamptz not null default now(),
    unique (user_id, event_key)
);

create index if not exists event_reminders_starts_at_idx on public.event_reminders (starts_at);

alter table public.event_reminders enable row level security;

drop policy if exists "read own event reminders"   on public.event_reminders;
drop policy if exists "insert own event reminders" on public.event_reminders;
drop policy if exists "update own event reminders" on public.event_reminders;
drop policy if exists "delete own event reminders" on public.event_reminders;

create policy "read own event reminders"
    on public.event_reminders for select
    using (auth.uid() = user_id);

create policy "insert own event reminders"
    on public.event_reminders for insert
    with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

create policy "update own event reminders"
    on public.event_reminders for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

create policy "delete own event reminders"
    on public.event_reminders for delete
    using (auth.uid() = user_id);

-- The "stop this reminder" link. Works without signing in, does one thing with the token
-- it is given, and reveals nothing either way.
create or replace function public.stop_event_reminder(p_token uuid)
returns void
language sql
security definer
set search_path = public
as $$
    delete from public.event_reminders where token = p_token;
$$;

revoke all on function public.stop_event_reminder(uuid) from public;
grant execute on function public.stop_event_reminder(uuid) to anon, authenticated;
