# "Already Applied?" browser extension

Shows a small banner on any job posting telling you whether you've already
applied — so you never apply to the same job twice, even if you find it on a
different site than where you first saw it.

## What it does

- On a recognised job page (Greenhouse, Lever, Ashby, Workday, LinkedIn,
  Google, Amazon, Apple, and more), a banner appears top-right:
  - **○ Not applied yet** — with a "Mark as applied" button
  - **✗ Already applied** — with the date, and an "Unmark" button
- On any non-job page, it stays completely silent.
- Matching uses the **same canonical job-id logic as the digest** (verified
  identical to `src/canonical.py`), so a job is recognised even if the URL
  differs from the one you first opened — tracking params, path slugs, and
  Workday `wd1`…`wd103` host shards all still match.
- Your applied list is stored **locally in your browser** (`chrome.storage`),
  never uploaded anywhere.

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

## Limitations

- Works on desktop browsers. It cannot run inside a phone app's in-app browser
  (e.g. tapping a link inside Instagram on your phone).
- It knows a job is "applied" only after you click **Mark as applied** — it
  can't detect an application you submitted without marking it.
- The banner needs a recognised job/ATS URL. On a company's own bespoke careers
  page with no known ATS pattern, it stays silent rather than guess.
