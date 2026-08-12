# Job Digest

Scrapes eight job sources **every hour**, keeps only postings you have never
been sent, deduplicates across sources, and emails you a digest where every
entry is a **direct link to the employer's application page**.

Sends at **9:00 AM** and **7:00 PM** in your configured timezone — two emails a
day, regardless of how often it scrapes. Anything found between those times is
banked in `state/seen.json` and delivered with the next digest, so a posting
that drops at 10:14 AM is captured at 11:00 rather than waiting until evening.

## Sources

| Source | How it's read | Link type |
|---|---|---|
| `SimplifyJobs/Summer2027-Internships` | Structured feed (`.github/scripts/listings.json`) | Direct |
| Simplify.jobs (3 filter sets) | Public Typesense search index | Direct — resolved through Simplify's click-redirect to the real ATS URL |
| `vanshb03/Summer2027-Internships` | Raw markdown (`OFFSEASON_README.md` + `README.md`) | Direct |
| `sndsh404/summer-2027-internships` | Raw markdown | Direct |
| `speedyapply/2027-SWE-College-Jobs` | Raw markdown (`README.md` + `NEW_GRAD_USA.md`) | Direct |
| LinkedIn (5 keyword queries) | Guest search API | **LinkedIn link only** — see below |

`SimplifyJobs/Summer2027-Internships` is the Pitt CSC × Simplify list — the
canonical one, and the list the other repos derive from. It is read from the
JSON feed that generates its READMEs rather than the READMEs themselves,
because the feed carries an exact `date_posted` and an `active` flag, and does
not break when the page markup changes. (That repo renders HTML `<table>`
markup, which a markdown table parser reads as zero jobs.)

### Coverage

The audit that added these sources measured what was being missed. Against the
Pitt CSC feed alone, **331 of 547 relevant listings** were absent from every
other configured source — 15 of them posted within the previous day. Three
separate causes, all now fixed:

- **LinkedIn was truncated.** A single 24-hour query had 300+ unique results
  still paginating while the config stopped at 60.
- **Simplify filters were over-restrictive.** Every filter is an AND-gate
  against Simplify's own tagging, so requiring `degrees` and `majors` dropped
  66 postings for having a *blank* degree tag rather than a wrong one. Both
  gates are gone; see the comments in `config/sources.yaml`.
- **Untagged seasons were invisible.** 5,983 US internships carry season
  `N/A`, and new postings are the likeliest to be untagged. They are now
  collected and shown in a separate "Season not confirmed" email section
  instead of being silently dropped.

### Season not confirmed

Postings whose source has not tagged a season get their own email section. They
cannot be verified as 2027 roles, but discarding them would lose exactly the
freshest listings. Treat that section as a "worth a glance" list.

### Why LinkedIn entries link to LinkedIn

LinkedIn hides the employer's apply URL behind a sign-in wall for logged-out
requests. There is no way to reach the real apply link without an authenticated
session. Those jobs therefore appear in their own clearly-labelled email
section. Many are Easy Apply postings, where the LinkedIn page genuinely *is*
the application page.

## Setup

### 1. Push to a private GitHub repo

```bash
cd "job email"
git init && git add -A && git commit -m "Initial commit"
gh repo create job-digest --private --source=. --push
```

### 2. Create a Gmail App Password

Requires 2-Step Verification on your Google account.
Go to <https://myaccount.google.com/apppasswords>, create one for "Mail", and
copy the 16-character value.

### 3. Add repository secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `GMAIL_USER` | your Gmail address (the sender) |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `RECIPIENT` | where the digest goes |

### 4. Set your timezone

In `config/sources.yaml`:

```yaml
timezone: America/Los_Angeles   # or America/New_York, America/Chicago, ...
send_hours: [9, 19]
```

The workflow already fires at every UTC hour that could be 9 AM or 7 PM in any
continental US timezone, in either DST state, and the script decides which one
actually counts. **You do not need to edit the cron** when you change timezone.

### 5. Trigger the first run

**Actions → Job Digest → Run workflow** (leave "force" checked).

The first run **baselines** everything currently listed — roughly 840 postings —
and emails you a short summary instead of 840 links. Every run after that sends
only genuinely new postings.

## Local usage

```bash
pip install -r requirements.txt

python -m src.main --dry-run --no-store   # scrape + list, no email, no state
python -m src.main --verify-links         # check apply links are direct
python -m src.main --test-email           # send a sample digest
python -m src.main --force                # a real run, ignoring the time gate
pytest -q                                 # 33 tests
```

`--test-email` needs the three env vars set locally:

```bash
export GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx' RECIPIENT=you@gmail.com
```

## "Have I already applied to this?" checker

A companion command that answers, for any job URL, whether you've seen it or
already applied — so you never apply twice.

```bash
python -m src.check "<job url>"                       # status of one job
python -m src.check --applied "<job url>" --company "Stripe" --title "SWE Intern"
python -m src.check --unapply "<job url>"             # undo a mark
python -m src.check --list                            # everything you've applied to
```

Three possible answers:

- **✓ New to you** — never emailed, never applied.
- **● Seen before** — the digest emailed it on a given date; you haven't applied.
- **✗ ALREADY APPLIED** — with the date. Don't apply again.

Matching uses the same canonical ATS id as the digest, so a job is recognised
even if the URL you paste differs from the one you were emailed — tracking
params, path slugs, and Workday `wd1`…`wd103` host shards all still match.

Two separate files back this, on purpose:

- `state/seen.json` — what the digest emailed you (written by the cloud runs).
- `state/applied.json` — what you've marked applied (written only by you).

Keeping them apart means the checker can never corrupt the digest's dedup
state. If you run the checker locally, `git pull` first so `seen.json` reflects
the latest cloud runs, and commit `applied.json` if you want your applied list
synced across machines.

## How deduplication works

Sources deliberately overlap, and they link to the same job with *different*
URLs. Measured on live data, plain URL matching caught almost nothing while
canonical ATS-ID extraction collapsed **146 duplicates out of 988** scraped
postings.

The case that motivates it — one Google job, two repos:

```
speedyapply: .../jobs/results/85564713261245126
sndsh404:    .../jobs/results/85564713261245126-software-engineering-intern/
```

Same posting, different URL. Stripping tracking parameters would not merge
these, because the difference is a slug in the *path*.

Identity is **exact first, fuzzy only as a fallback**:

1. **Exact IDs** — a job can carry more than one, and all are compared:
   - **Canonical ATS ID** — `gh:appian:8041237`, `wd:jr2015779`,
     `lv:palantir:<uuid>` … extracted from the apply URL. Survives tracking
     params, URL slugs, and Workday's `wd1`…`wd103` host shards.
   - **Simplify posting UUID** — the Typesense index and the Pitt CSC feed use
     the *same* UUIDs (452 of ~470 live postings appear in both), which is what
     joins the two sources. Typesense entries only hold a click stub for a URL,
     so without the UUID there is nothing else to match them on.
2. **Fuzzy identity** — normalised `(company, title)`, with company suffixes
   (`Inc`, `LLC`) and title noise (`Summer 2027`, `BS/MS`) removed. Used *only*
   for postings with no exact ID — chiefly LinkedIn, which repeats one role
   once per city.

**Why exact IDs take priority.** The fuzzy key strips season and year from the
title, so these three live Palantir listings share one fuzzy identity:

```
Fall 2026    .../palantir/d582cd84-...
Fall 2026    .../palantir/ac0dc094-...
Summer 2028  .../palantir/e0010393-...
```

Measured on 1,622 live listings, 125 (7%) collapse under the fuzzy key and 31
of those groups are genuinely different requisitions. Because the store keeps
entries for 180 days, a collapse there is *permanent*: once the Fall 2026 role
had been emailed, the Summer 2027 one would never arrive. Comparing exact IDs
first keeps them apart — TikTok's seven distinct Software Engineer Intern reqs
stay seven entries.

**Why location is not part of the fuzzy key.** Every source writes it
differently for the same posting — `"San Francisco, CA, USA, New York, NY"` vs
`"SF, NYC"` vs LinkedIn's one row per city — which made a single Figma role
read as four entries. Dropping it removed 23 duplicates from a live 971-job run
while losing no requisition any source identified.

State lives in `state/seen.json`, committed back to the repo after each run.

## Design notes

**Independent source failures.** Each source is wrapped separately. If LinkedIn
throttles or a repo is renamed, the other sources still send, and a warning
appears in the email footer. Critically, a failed source's jobs are **not**
marked as seen, so they appear in the next successful run rather than being
lost permanently.

**A source returning zero is a failure, not a quiet day.** Every source here is
a continuously-updated list of hundreds of live jobs, so zero means the fetch or
the parser broke. Treating it as an empty success would look identical in the
email to a slow day, which is how a dead source goes unnoticed for weeks. It is
reported in the email's warning box instead.

**Emails are split before Gmail clips them.** Gmail truncates a body over
~102KB behind a "[Message clipped]" link — which would hide exactly the
postings the digest exists to surface. Above that size the digest is split into
numbered parts, measured from the actual rendered size. A normal digest is one
email.

**First-run guard.** If `state/seen.json` is missing *or unreadable*, the run
baselines instead of emailing the entire backlog. A corrupt state file cannot
cause an 800-link email.

**Atomic state writes.** State is written to a temp file and renamed, so an
interrupted run cannot leave truncated JSON.

## Maintenance

### Simplify key rotation

Simplify's search key is a public, search-only credential embedded in their
client bundle. If Simplify rotates it, the run fails with a 401/403 and a
message pointing here. To re-extract:

```bash
curl -s 'https://simplify.jobs/jobs' | grep -o '/_dunder/_next/static/chunks/[^"]*\.js' | sort -u \
  | while read c; do curl -s "https://simplify.jobs$c"; done \
  | grep -o 'apiKey:"[^"]*"' | sort -u
```

Take the key whose decoded suffix mentions `company_url,categories,…` (the
`jobs` collection, not `companies`) and put it in `config/sources.yaml`.

### If a GitHub list changes format

The markdown parser maps columns by **header name**, not position, so added or
reordered columns are handled automatically. A renamed header needs one entry
in `_HEADER_MAP` in `src/scrapers/github_md.py`.

### Adjusting what gets scraped

Everything is in `config/sources.yaml` — Simplify filter values, which GitHub
lists are enabled, LinkedIn keywords. The two lists that were not among the
original seven URLs (`vanshb03-summer2027`, `speedyapply-newgrad-usa`) each have
their own `enabled:` flag.

## Known limitations

- LinkedIn entries link to LinkedIn, not the employer (see above).
- Some employers (e.g. Tesla) return `403` to automated requests. `--verify-links`
  reports these as `BOT?` rather than failures — the links work fine in a browser.
- GitHub Actions scheduled runs can be delayed several minutes under load, so a
  digest may arrive at ~9:05 rather than exactly 9:00.
