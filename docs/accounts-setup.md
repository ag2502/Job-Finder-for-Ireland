# Turning on accounts

Everything here is one-time setup. Until it is done the finder works exactly as it
always has — `supabase.configured()` returns False, no sign-in is offered, and searching
and ranking are untouched. Nothing below can break the job search.

Budget about 20 minutes.

---

## Step 1 — Create the Supabase project

1. Go to **https://supabase.com** and sign in with GitHub.
2. **New project**. Fill in:
   - **Name** — `dublin-job-finder` (anything; it is only a label).
   - **Database password** — click Generate, then **save it in your password manager**.
     You will not need it for this setup, but it is the only way to reach the database
     directly later and Supabase will not show it again.
   - **Region** — pick **Europe (Frankfurt)** or **Europe (Ireland)**. This matters:
     you are serving Irish users, and keeping personal data in the EU makes GDPR far
     simpler. The region cannot be changed afterwards.
3. Wait ~2 minutes for provisioning.

---

## Step 2 — Create the table

1. In the left sidebar: **SQL Editor** → **New query**.
2. Open [`schema.sql`](../schema.sql) from this repo, copy all of it, paste it in.
3. Click **Run**.

You should see `Success. No rows returned`. To confirm, go to **Table Editor**: there
should be `applications`, `saved_jobs` and `profiles` tables, each with a shield icon
showing RLS is enabled.

It is safe to run this file again; every statement is guarded. **Run it again whenever
`schema.sql` changes.** The `profiles` table (the searcher's details and the reading of
their CV) was added after the others, and until it exists the profile says it could not
load, and adding a CV says it could not be saved.

---

## Step 3 — Decide how sign-up works

**Authentication** → **Sign In / Providers** → **Email**.

There is one setting that matters: **Confirm email**.

| | What happens | Use when |
|---|---|---|
| **On** (default) | New users get an email with a link and cannot sign in until they click it | Real use |
| **Off** | Sign-up signs you straight in | Trying it out |

The code handles both. With confirmation on, sign-up shows *"Account created. Check your
email for a confirmation link, then sign in."* rather than pretending you are signed in.

**The catch, and it is a real one.** Supabase's built-in email sender is for testing
only and is rate-limited to a handful of messages per hour. It is not good enough for
real users — they will silently stop receiving confirmation emails. Options:

- **Trying it out:** turn **Confirm email off**. Fastest path to a working sign-in.
- **Real use:** leave it on and set up **custom SMTP** under
  **Project Settings → Authentication → SMTP Settings**. Resend, Postmark and Brevo all
  have free tiers that are plenty for this.

While you are in Authentication, set **Site URL** to `https://dublin-job-finder.vercel.app`
under **URL Configuration**, so confirmation links come back to your site rather than
`localhost`.

---

## Step 4 — Copy the two keys

You need two values, and in the current dashboard they live on **different pages**.

**The key** — **Project Settings** (the gear, bottom of the icon strip) → **API Keys**.
Under **Publishable key**, copy the value beginning `sb_publishable_...`.

**The URL** — **Project Settings** → **Data API**. It looks like
`https://<your-ref>.supabase.co`. The page shows the endpoint
`https://<your-ref>.supabase.co/rest/v1/` more prominently; either is fine, because the
setting trims the `/rest/v1` for you.

**Do not copy anything under "Secret keys"** (`sb_secret_...`), and on the
**Legacy anon, service_role API keys** tab, not `service_role`. Those bypass every row
level security policy and can read and delete every user's data. They must never go into
Vercel, this repo, or a browser.

Older projects show `anon` `public` (a long `eyJhbGciOi...` JWT) instead of a publishable
key. Either works — the client sends a JWT key as a bearer token and an opaque
`sb_publishable_` key in `apikey` alone, because Supabase rejects the latter as a
credential.

The publishable key is designed to be public, but read the warning the dashboard prints
beside it: *"safe to use in a browser **if you have enabled Row Level Security**"*. That
is what step 2 did. Run `schema.sql` before this key goes anywhere.

---

## Step 5 — Give them to Vercel

1. **https://vercel.com** → your `dublin-job-finder` project → **Settings** →
   **Environment Variables**.
2. Add two, both ticked for **Production**, **Preview** and **Development**:

   | Name | Value |
   |---|---|
   | `JOBFINDER_SUPABASE_URL` | the Project URL from step 4 |
   | `JOBFINDER_SUPABASE_ANON_KEY` | the `anon` key from step 4 |

   The `JOBFINDER_` prefix is required — it is how settings are read (`env_prefix` in
   `core/config.py`). Without it they are ignored and sign-in will simply not appear.

3. While you are there, check **`JOBFINDER_SESSION_SECRET`** is set to a long random
   string. It signs the session cookie, which now carries login tokens. If it is still
   the default `dev-only-change-me`, anyone could forge a session. Generate one with:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

---

## Step 6 — Give them to GitHub Actions

This is for the keep-alive only. **A free Supabase project pauses after about a week
with no traffic**, and the first request after that fails — which for a job site means
someone cannot sign in at the moment they came back. The crawl already runs every six
hours, so it now pings Supabase to keep it awake.

1. This repo → **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
2. Add two — note these have **no** `JOBFINDER_` prefix:

   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | the Project URL |
   | `SUPABASE_ANON_KEY` | the `anon` key |

If you skip this the site still works; the project will just pause when traffic is quiet.

---

## Step 6b: Turn on "Continue with Google"

Google is the main way in: the sign-in page leads with a **Continue with Google** button
and folds the email form away underneath it. The button appears by itself as soon as
the Google provider is switched on in Supabase (the site asks Supabase which providers
are enabled), so until this step is done the page simply shows the email form.

**In Google Cloud** (https://console.cloud.google.com, any project):

1. **APIs & Services > OAuth consent screen.** User type **External**. App name
   `Sorted Place`, your email as the support and developer contact. Under
   **Authorized domains** add `supabase.co`. Scopes: the defaults (`email`, `profile`,
   `openid`) are all that is needed.
2. **Publish the app** (Publishing status > **In production**). While it is in
   *Testing*, only the test users you list can sign in; everyone else gets an error.
3. **APIs & Services > Credentials > Create credentials > OAuth client ID.**
   Application type **Web application**.
   - **Authorized JavaScript origins:** `https://dublin-job-finder.vercel.app`
   - **Authorized redirect URIs:** `https://<your-ref>.supabase.co/auth/v1/callback`
     (Supabase shows this exact address on its Google provider page; copy it from there.)
4. Copy the **Client ID** and **Client secret**.

**In Supabase:**

5. **Authentication > Sign In / Providers > Google.** Enable it, paste the Client ID
   and Client secret, save.
6. **Authentication > URL Configuration > Redirect URLs.** Add
   `https://dublin-job-finder.vercel.app/auth/callback`. For local testing also add
   `http://127.0.0.1:8000/auth/callback`. (If this is missed, Supabase sends people to
   the Site URL instead; the home page notices the code and finishes the sign-in anyway,
   but the allow-list entry is the proper route.)

Nothing needs adding to Vercel for Google: the client secret lives in Supabase only.

**How it works, briefly.** The site uses the PKCE code flow. It keeps a random
verifier in its own signed session, sends Google only a hash of it, and exchanges the
one-time code that comes back for a session by presenting the original verifier. The
tokens never appear in a URL. Google accounts store the name and picture Google shares;
the privacy page says so.

---

## Step 7 — Deploy

A `git push` deploys nothing — the Vercel project is not connected to the repository.
Environment variables also only apply to *new* deployments, so the ones you just added
need a fresh one:

```
npx vercel --prod
```

Or wait up to six hours for `crawl.yml`, which deploys at the end of every run.

---

## Step 8 — Check it

1. Open https://dublin-job-finder.vercel.app — **Sign in** should now be in the header.
   If it is not, the environment variables did not reach the deployment: re-check the
   `JOBFINDER_` prefix and that you redeployed after adding them.
2. Create an account.
3. Search, click **Apply** on something. The button becomes **applied · Undo**.
4. Search again — that job should be gone, and a **show applied** toggle should appear.
5. Your name in the header opens a menu; **Your profile** lists it under **Applied**
   with the date. (On a phone the same links are in the menu behind the burger.)
6. Hit **Undo** and confirm it returns to the results.

---

## If something is wrong

**`/healthz`** reports how the deployment resolved its data — useful for confirming the
function is serving the snapshot as expected.

| Symptom | Cause |
|---|---|
| No **Sign in** link | Env vars missing, wrong prefix, or no redeploy since adding them |
| "Could not reach the accounts service" | Project paused (Supabase dashboard → Restore), or wrong Project URL |
| Sign-up works, sign-in says invalid credentials | Email confirmation is on and the link was not clicked |
| No confirmation email arrives | Built-in sender rate limit — set up custom SMTP (step 3) |
| Applying says "could not save" | `schema.sql` was not run, or the RLS policies are missing |
| Adding a CV says it "could not be saved just now" | The `profiles` table is missing: run `schema.sql` again |
| Applied jobs still show | Sign-in is not actually active; check the header shows your name |
| No **Continue with Google** button | Google provider not enabled in Supabase (step 6b) |
| Google says "access blocked" or only some people can sign in | OAuth consent screen still in *Testing*; publish it |
| Google returns to the home page instead of signing in | `/auth/callback` missing from Supabase Redirect URLs |

---

## Still outstanding

The privacy page has a `TODO`. GDPR gives people the right to erase the **account
itself**, not only the application rows in it, and that needs a contact route someone
can actually use. This site has no contact address anywhere, so the page deliberately
does not promise one. Add an address, then say so in that paragraph.
