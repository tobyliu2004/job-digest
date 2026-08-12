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


def _keyword_list(cfg: dict) -> list[str]:
    """Config accepts either a single keyword string or a list of them."""
    kw = cfg.get("keywords", "software engineer intern")
    if isinstance(kw, str):
        return [kw]
    return [k for k in kw if k]


class _Budget:
    """Shared request budget across all keyword queries.

    Each query paginates until exhausted, so without a shared ceiling a day
    with many postings could issue hundreds of requests and hit both the job
    timeout and LinkedIn's throttle.
    """

    def __init__(self, limit: int):
        self.remaining = limit

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _fetch_query(session, cfg: dict, keyword: str, seen_urls: set[str],
                 budget: _Budget) -> tuple[list[Job], bool]:
    """Paginate one keyword query. Returns (jobs, hit_hard_failure)."""
    jobs: list[Job] = []
    max_pages = int(cfg.get("max_pages", 25))
    delay = float(cfg.get("page_delay_seconds", 1.2))
    offset = 0

    for page in range(max_pages):
        if not budget.take():
            log.info("LinkedIn: request budget spent, stopping at '%s'", keyword)
            break

        params = {
            "keywords": keyword,
            "f_E": cfg.get("f_E", "1"),
            "f_TPR": cfg.get("f_TPR", "r86400"),
            "sortBy": cfg.get("sort_by", "DD"),
            "start": str(offset),
        }
        if cfg.get("geo_id"):
            params["geoId"] = str(cfg["geo_id"])

        resp = request(session, "GET", SEARCH_URL, params=params, timeout=30)

        if resp.status_code == 429:
            log.warning("LinkedIn rate-limited on '%s' at page %d", keyword, page)
            break
        if resp.status_code != 200:
            if page == 0:
                return jobs, True
            log.warning("LinkedIn HTTP %d on '%s' at page %d; stopping",
                        resp.status_code, keyword, page)
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

        # LinkedIn repeats the last page instead of returning empty. Only stop
        # on a page that was entirely duplicate *within this query* -- across
        # queries heavy overlap is expected and must not end pagination early.
        if new_this_page == 0 and offset >= len(page_jobs) * 2:
            break

        time.sleep(delay)

    return jobs, False


def fetch(session, cfg: dict) -> list[Job]:
    seen_urls: set[str] = set()
    jobs: list[Job] = []
    keywords = _keyword_list(cfg)
    budget = _Budget(int(cfg.get("max_total_requests", 90)))
    hard_failures = 0

    for keyword in keywords:
        found, failed = _fetch_query(session, cfg, keyword, seen_urls, budget)
        if failed:
            hard_failures += 1
            log.warning("LinkedIn query '%s' failed on its first page", keyword)
        else:
            log.info("LinkedIn['%s']: %d new unique", keyword, len(found))
        jobs.extend(found)

    # Only a total wipeout is a source failure. If some queries returned
    # results, a throttled one must not discard the rest of the run.
    if hard_failures == len(keywords):
        raise RuntimeError(f"LinkedIn: all {len(keywords)} queries failed")

    log.info("LinkedIn: %d unique postings across %d queries", len(jobs), len(keywords))
    return jobs
