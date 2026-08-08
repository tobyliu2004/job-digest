# "Already Applied?" browser extension

Shows a small banner on any job posting telling you whether you've already
applied — so you never apply to the same job twice, even if you find it on a
different site than where you first saw it.

## How it detects a job page

Rather than matching a fixed list of job sites (which always misses some), the
extension figures out that a page is a job posting from the page itself, in
this order:

1. A known ATS URL pattern (Greenhouse, Lever, Ashby, Workday, `gh_jid=`, …) —
   instant and gives a stable cross-site id.
2. **Schema.org `JobPosting` structured data** — the machine-readable data
   Google requires to list a job in search, which most job pages (including
   company-hosted ones) emit.
3. `og:type` = job.
4. A content heuristic — an "Apply" button plus at least two job-description
   sections (Responsibilities / Qualifications / Requirements / …).

Because job boards often load content asynchronously, it also watches the page
for a few seconds and re-checks. Tests for all of this are in `detect.test.js`.

## What it does

- On a detected job page, a banner appears top-right:
  - **○ Not applied yet** — with a "Mark as applied" button
  - **✗ Already applied** — with the date, and an "Unmark" button
- On any non-job page, it stays completely silent.
- Matching uses the **same canonical job-id logic as the digest** (verified
  identical to `src/canonical.py`), so a job is recognised even if the URL
  differs from the one you first opened — tracking params, path slugs, and
  Workday `wd1`…`wd103` host shards all still match.
- Your applied list is stored **locally in your browser** (`chrome.storage`),
  never uploaded anywhere.

## Keyword extractor

On any job page, the banner has a **🔑 Keywords** button. Click it and the
extension reads the whole job description and pulls out the real tech keywords
(languages, frameworks, tools, concepts) — entirely offline, nothing leaves
your browser.

If you paste your resume text into the extension popup once ("Your resume
text" → Save resume), the panel then splits those keywords into:

- **➕ Missing from your resume** — with a "Copy missing" button
- **✓ Already in your resume**

so you can decide what, if anything, to add. You revise your resume yourself —
the extension only tells you what a given job emphasises.

## Install (Chrome, Edge, Brave, Arc — any Chromium browser)

1. Go to `chrome://extensions`
2. Turn on **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select this `extension/` folder
5. Done. Open any job posting to see the banner.

To install in **Firefox**: go to `about:debugging` → This Firefox → Load
Temporary Add-on → pick `manifest.json` in this folder. (Firefox unloads
temporary add-ons on restart; Chromium keeps it until you remove it.)

## Backup / sync with the CLI checker

The popup (click the toolbar icon) lists everything you've applied to, with
**Export** and **Import** buttons. The exported `applied.json` is the same
shape the command-line checker uses (`state/applied.json`), so you can move
your applied list between the extension and the terminal tool.

## Privacy

The extension's strongest privacy property: it makes **no network requests at
all**. Your resume text, your applied list, and the job descriptions it reads
never leave your browser — everything is stored locally on your device. There
is no code that could transmit anything.

- It loads on all pages (so company-hosted job boards on their own domains are
  covered), but it **only ever does anything on a recognised job posting** — on
  any other page it computes the URL's job-id, sees it isn't a job, and stops.
  It never reads page text except when you click the 🔑 Keywords button, which
  only exists on job pages. With zero network calls, there is no path for your
  data to leave the browser regardless of where the script runs.
- Requests only the `storage` permission — no access to tabs, history, cookies,
  or network.
- The applied-jobs list in the popup is collapsed by default ("Show all" to
  expand), so it stays compact even after hundreds of applications.

## Limitations

- Works on desktop browsers. It cannot run inside a phone app's in-app browser
  (e.g. tapping a link inside Instagram on your phone).
- It knows a job is "applied" only after you click **Mark as applied** — it
  can't detect an application you submitted without marking it.
- The banner needs a recognised job/ATS URL. On a company's own bespoke careers
  page with no known ATS pattern, it stays silent rather than guess.

## Why manifest.json has a `key`

Chrome derives an unpacked extension's ID from its **folder path**. So when this
repo was renamed (`~/job email` -> `~/job digest`) Chrome could no longer find
the extension, silently dropped it, and orphaned its stored applied-jobs data
under the old ID.

The `key` field pins the ID to `pephgdhbpdmimmgikggfmpenlgcchkmo` no matter where
the folder lives, so moving or renaming the repo can never orphan your data again.

If you ever do lose the extension, the data isn't gone — it's in
`~/Library/Application Support/Google/Chrome/Default/Local Extension Settings/<old-id>/`.
Reload the extension, quit Chrome, and copy that folder's contents over the new
ID's folder.
