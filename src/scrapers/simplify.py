"""Simplify.jobs scraper.

Simplify is a Next.js SPA whose job search is served by a public Typesense
index. No headless browser is needed. The host, collection and search-only
scoped key live in config/sources.yaml (extracted from Simplify's own client
bundle).

Two things here are load-bearing and non-obvious:

1. The scoped key's `exclude_fields` list hides `countries`, `degrees` and
   `url` from *results*, but FILTERING on those fields still works. That is
   what lets us reproduce Toby's URL filters exactly rather than approximate
   them.

2. Because `url` is excluded, the apply link is not in the search response.
   It is recovered from `simplify.jobs/jobs/click/{id}`, which 307s to the
   real ATS URL. We only resolve IDs that survive the seen-check, so redirect
   volume tracks the number of *new* jobs, not the result-set size.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..http import FetchError, get_json, resolve_redirect
from ..locations import is_us
from ..models import Job

log = logging.getLogger(__name__)

CLICK_URL = "https://simplify.jobs/jobs/click/{id}"
PER_PAGE = 250
MAX_PAGES = 8


def _backtick_list(values: list[str]) -> str:
    """Render a Typesense value list.

    Backtick-quoting is what makes values containing parentheses, ampersands
    and apostrophes safe -- e.g. `Natural Language Processing (NLP)`,
    `AI & Machine Learning`, `Bachelor's`. Do not hand-escape these.
    """
    return "[" + ",".join(f"`{v}`" for v in values) + "]"


def build_filter(filters: dict[str, list[str]]) -> str:
    return " && ".join(
        f"{field}:={_backtick_list(values)}"
        for field, values in filters.items()
        if values
    )


def _search(session, cfg: dict, filter_by: str, page: int) -> dict:
    params = {
        "q": "*",
        "query_by": "title",
        "per_page": str(PER_PAGE),
        "page": str(page),
        "sort_by": "start_date:desc",
        "filter_by": filter_by,
    }
    headers = {
        "X-TYPESENSE-API-KEY": cfg["api_key"],
        # Cloudflare on this host rejects non-browser signatures with
        # `403 error code: 1010`. The UA comes from the shared session;
        # Origin/Referer are required on top of it.
        "Origin": "https://simplify.jobs",
        "Referer": "https://simplify.jobs/",
    }

    hosts = [cfg["host"], *cfg.get("fallback_hosts", [])]
    last_exc: Exception | None = None
    for host in hosts:
        url = f"{host}/collections/{cfg['collection']}/documents/search"
        try:
            return get_json(session, url, params=params, headers=headers)
        except FetchError as exc:
            last_exc = exc
            log.warning("Simplify host %s failed: %s", host, exc)

    raise FetchError(
        f"All Simplify hosts failed. If this is a 401/403, the public search "
        f"key may have been rotated -- see README 'Simplify key rotation'. "
        f"Last error: {last_exc}"
    )


def fetch(session, cfg: dict) -> list[Job]:
    """Fetch all enabled Simplify profiles, unioned and deduped by posting id."""
    jobs: dict[str, Job] = {}

    for profile in cfg.get("profiles", []):
        if not profile.get("enabled", True):
            continue

        filter_by = build_filter(profile["filters"])
        name = profile["name"]
        # Profiles that deliberately relax a gate (untagged season, untagged
        # country) mark their results so the email can separate "confirmed
        # 2027 role" from "might be one".
        unconfirmed = bool(profile.get("unconfirmed", False))
        # A profile with no `countries` gate must be filtered on location
        # instead, otherwise relaxing that gate lets the whole world in.
        us_postfilter = "countries" not in profile["filters"]
        found_total = None
        skipped_non_us = 0

        for page in range(1, MAX_PAGES + 1):
            data = _search(session, cfg, filter_by, page)
            if found_total is None:
                found_total = data.get("found", 0)
                log.info("Simplify[%s]: %d matches", name, found_total)

            hits = data.get("hits", [])
            if not hits:
                break

            for hit in hits:
                doc = hit.get("document", {})
                job_id = doc.get("id")
                if not job_id or job_id in jobs:
                    continue

                locations = doc.get("locations") or []
                seasons = doc.get("seasons") or []

                if us_postfilter and not is_us(locations):
                    skipped_non_us += 1
                    continue

                jobs[job_id] = Job(
                    company=doc.get("company_name", ""),
                    title=doc.get("title", ""),
                    # Resolved to the real ATS URL later, only if new.
                    apply_url=CLICK_URL.format(id=job_id),
                    source="Simplify",
                    location=", ".join(locations[:2]),
                    season=", ".join(s for s in seasons if s and s != "N/A"),
                    salary=_format_salary(doc),
                    posted=_format_date(doc.get("start_date")),
                    unconfirmed=unconfirmed,
                    extra={"simplify_id": job_id, "profile": name},
                )

            if len(hits) < PER_PAGE:
                break

        if skipped_non_us:
            log.info("Simplify[%s]: dropped %d non-US postings", name, skipped_non_us)

    log.info("Simplify: %d unique postings across profiles", len(jobs))
    return list(jobs.values())


def resolve_apply_urls(session, jobs: list[Job]) -> None:
    """Replace click-redirect URLs with the real ATS URL, in place.

    Call this only for jobs that survived the seen-check. Jobs whose redirect
    cannot be resolved keep the click URL, which still works in a browser.
    """
    for job in jobs:
        sid = job.extra.get("simplify_id")
        if not sid:
            continue
        target = resolve_redirect(session, CLICK_URL.format(id=sid))
        if target:
            job.apply_url = target
        else:
            log.warning("Keeping click URL for %s - %s", job.company, job.title)


# Simplify encodes salary_period as an integer enum, not a string. Values
# observed live: 1 -> hourly ($18-19.75), 3 -> monthly (1200), 4 -> yearly
# (30000-35000). Unknown codes fall back to no unit rather than guessing.
_SALARY_PERIOD = {1: "/hr", 2: "/day", 3: "/mo", 4: "/yr"}


def _format_salary(doc: dict) -> str:
    lo, hi = doc.get("min_salary"), doc.get("max_salary")
    if not lo and not hi:
        return ""
    unit = _SALARY_PERIOD.get(doc.get("salary_period"), "")

    def fmt(v):
        if not v:
            return ""
        return f"${int(v):,}" if v >= 1000 else f"${int(v)}"

    if lo and hi and lo != hi:
        return f"{fmt(lo)}-{fmt(hi)}{unit}"
    return f"{fmt(lo or hi)}{unit}"


def _format_date(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%b %d")
    except (ValueError, OSError, OverflowError):
        return ""
