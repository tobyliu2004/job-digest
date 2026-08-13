"""LinkedIn guest-API scraper.

Known limitation, verified during planning and unavoidable without an
authenticated session: LinkedIn gates the *external* apply URL behind a
`contextual-sign-in-modal` for logged-out requests. We can read the title,
company, location and LinkedIn job URL, but not the employer's own apply link.

So these jobs are marked `indirect=True` and rendered in their own labelled
email section. Many LinkedIn postings are Easy Apply, where the LinkedIn page
genuinely *is* the application page.

LinkedIn also throttles aggressively. A non-200 here is treated as a soft
failure by main.py: the run continues and these jobs are not marked as seen,
so they reappear in the next successful run instead of being lost.
"""

from __future__ import annotations

import html
import logging
import re
import time

from ..http import request
from ..locations import is_us
from ..models import Job

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# LinkedIn does NOT return a fixed page size -- observed live returning 10
# cards for a request, not the 25 the parameter name implies. Advancing
# `start` by a hardcoded 25 would silently skip every job past the tenth on
# each page. So we advance by the number of cards actually parsed.


_CARD = re.compile(r'<li>\s*(.*?)</li>', re.S)
_JOB_URL = re.compile(r'href="(https://www\.linkedin\.com/jobs/view/[^"?]+)', re.I)
_TITLE = re.compile(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)<', re.S)
_COMPANY = re.compile(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*(?:<a[^>]*>)?(.*?)<', re.S)
_LOCATION = re.compile(r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)<', re.S)
_DATE = re.compile(r'datetime="([\d-]+)"')
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", text))).strip()


def _parse_page(markup: str) -> list[Job]:
    jobs: list[Job] = []
    for card in _CARD.findall(markup):
        url_match = _JOB_URL.search(card)
        title_match = _TITLE.search(card)
        if not url_match or not title_match:
            continue

        company_match = _COMPANY.search(card)
        location_match = _LOCATION.search(card)
        date_match = _DATE.search(card)

        jobs.append(
            Job(
                company=_clean(company_match.group(1) if company_match else ""),
                title=_clean(title_match.group(1)),
                apply_url=url_match.group(1),
                source="LinkedIn",
                location=_clean(location_match.group(1) if location_match else ""),
                posted=date_match.group(1) if date_match else "",
                indirect=True,
            )
        )
    return jobs


def fetch(session, cfg: dict) -> list[Job]:
    seen_urls: set[str] = set()
    jobs: list[Job] = []
    max_pages = int(cfg.get("max_pages", 6))
    offset = 0

    for page in range(max_pages):
        params = {
            "keywords": cfg.get("keywords", "software engineer"),
            "f_E": cfg.get("f_E", "1"),
            "f_TPR": cfg.get("f_TPR", "r86400"),
            "sortBy": cfg.get("sort_by", "DD"),
            "start": str(offset),
            # No geo filter at all was being sent, so results were worldwide.
            # geoId is what LinkedIn actually filters on; `location` alone is
            # advisory. 103644278 is the United States.
            "location": cfg.get("location", "United States"),
            "geoId": str(cfg.get("geo_id", "103644278")),
        }
        resp = request(session, "GET", SEARCH_URL, params=params, timeout=30)

        if resp.status_code == 429:
            log.warning("LinkedIn rate-limited at page %d; keeping %d jobs", page, len(jobs))
            break
        if resp.status_code != 200:
            if page == 0:
                raise RuntimeError(f"LinkedIn returned HTTP {resp.status_code}")
            log.warning("LinkedIn HTTP %d at page %d; stopping", resp.status_code, page)
            break

        page_jobs = _parse_page(resp.text)
        if not page_jobs:
            break

        new_this_page = 0
        for job in page_jobs:
            if job.apply_url in seen_urls:
                continue
            seen_urls.add(job.apply_url)
            jobs.append(job)
            new_this_page += 1

        # Advance by what we actually got, never by an assumed page size.
        offset += len(page_jobs)

        # LinkedIn repeats the last page instead of returning empty.
        if new_this_page == 0:
            break

        time.sleep(1.5)

    # Belt and braces: the guest endpoint does not always honour geoId. is_us
    # is a foreign-DENYLIST that returns True for anything it cannot place, so
    # it can drop an obvious London posting but never a US one it fails to
    # recognise -- the safe direction for a filter that runs unattended.
    if cfg.get("us_only", True):
        kept = [j for j in jobs if is_us([j.location])]
        if len(kept) != len(jobs):
            log.info("LinkedIn: dropped %d non-US posting(s)", len(jobs) - len(kept))
        jobs = kept

    log.info("LinkedIn: %d postings", len(jobs))
    return jobs
