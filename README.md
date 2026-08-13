# Job Digest

Scrapes seven job sources twice a day, keeps only postings you have never been
sent, deduplicates across sources, and emails you one digest where every entry
is a **direct link to the employer's application page**.

Sends at **7:00 AM** and **7:00 PM** in your configured timezone, on the
minute — the run starts ~25 minutes early, does all the scraping and rendering,
then holds the finished email until the target time (GitHub Actions cron drifts
10-50 minutes, so firing at 7:00 and hoping does not work).

## Sources

| Source | How it's read | Link type |
|---|---|---|
| `SimplifyJobs/Summer2027-Internships` (Pitt CSC x Simplify) | Structured JSON feed (`.github/scripts/listings.json`) | Direct |
| Simplify.jobs (3 filter sets) | Public Typesense search index | Direct — resolved through Simplify's click-redirect to the real ATS URL |
| `vanshb03/Summer2027-Internships` | Raw markdown (`OFFSEASON_README.md` + `README.md`) | Direct |
| `sndsh404/summer-2027-internships` | Raw markdown | Direct |
| `speedyapply/2027-SWE-College-Jobs` | Raw markdown (`README.md` + `NEW_GRAD_USA.md`) | Direct |
| LinkedIn | Guest search API | **LinkedIn link only** — see below |

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
python -m src.main --audit-filter         # what the junk filter drops, and why
python -m src.main --migrate-keys --dry-run  # preview a dedup-key migration
pytest -q                                 # the full suite
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

Two keys are computed per job, and a job is new only if **both** miss:

1. **Canonical ATS ID** — `gh:appian:8041237`, `wd:jr2015779`, `lv:palantir:<uuid>` …
   extracted from the apply URL. Survives tracking params, URL slugs, and
   Workday's `wd1`…`wd103` host shards.
2. **Fuzzy identity** — normalised `(company, title, location)`, with company
   suffixes (`Inc`, `LLC`) and title noise (`Summer 2027`, `BS/MS`) removed.
   Catches the same role posted to two different ATS platforms, which key 1
   structurally cannot see.

State lives in `state/seen.json`, committed back to the repo after each run.

## Design notes

**Independent source failures.** Each source is wrapped separately. If LinkedIn
throttles or a repo is renamed, the other sources still send, and a warning
appears in the email footer. Critically, a failed source's jobs are **not**
marked as seen, so they appear in the next successful run rather than being
lost permanently.

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
lists are enabled, LinkedIn keywords and geography. The two lists that were not
among the original seven URLs (`vanshb03-summer2027`, `speedyapply-newgrad-usa`)
each have their own `enabled:` flag.

### Filtering out junk

Two independent stages, deliberately kept apart:

- `intern_only` asks **is this an internship?** "Tax Intern" passes.
- `relevance:` asks **is this a software job?** That is where "Tax Intern" goes.

The `relevance:` rules run in a fixed order — `block_*` (not a job posting at
all) → `restrict_titles` (unpaid, or reserved for one school) → `maybe_titles`
(off-domain), with `allow_titles` able to rescue only the last of those.
Anything filtered as borderline is **demoted to a "Maybe" section at the bottom
of the email, not deleted**, and tagged with the rule that caught it — so a rule
that is too aggressive is visible rather than silently costing you a job.

Before changing a rule, see exactly what it would do:

```bash
python -m src.main --audit-filter                              # live scrape
python -m src.main --audit-filter --from tests/fixtures/live_jobs.json   # offline
```

The `SAVED` section of that report lists every posting `allow_titles` rescued —
the best place to spot an over-broad rule.

### Deduplication keys

Jobs are identified by, in order of strength: Simplify's posting UUID, the
employer's own ATS requisition id, then a normalised
company/title/location/level/season tuple. Requisition ids always carry the
employer (`wd:nvidia:jr2015779`, not `wd:jr2015779`) because those counters are
per-tenant and short ones collide across companies.

Two failure modes, and they are not symmetric: sending a job twice is visible
and mildly annoying; merging two different jobs marks one as seen without ever
sending it, and hides it forever. Every judgement call in `src/canonical.py` and
`src/dedupe.py` is biased accordingly.

**Changing a key format invalidates every key already in `state/seen.json`**, so
it needs a migration or the next digest re-sends the whole backlog:

```bash
python -m src.main --migrate-keys --dry-run   # shows the one-time resend count
python -m src.main --migrate-keys             # applies it
```

`dedup.legacy_keys_until` in the config keeps the old formats readable (never
writable) for a transition period. After that date, delete
`src/canonical_legacy.py` and the config block.

## Known limitations

- LinkedIn entries link to LinkedIn, not the employer (see above).
- Some employers (e.g. Tesla) return `403` to automated requests. `--verify-links`
  reports these as `BOT?` rather than failures — the links work fine in a browser.
- GitHub Actions can drop a scheduled trigger entirely. The workflow fires
  repeatedly across each window and `main.py` sends whenever a slot is *due and
  unsent*, so a dropped trigger costs punctuality, never the digest.
