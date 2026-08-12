"""Pitt CSC x Simplify list, read from its structured feed.

`SimplifyJobs/Summer2027-Internships` is the canonical internship list -- 46k
stars, updated continuously, and the list the other repos in this config
derive from. Measured on 2026-08-10, 331 of its 547 relevant listings were
absent from every other configured source, 15 of them posted within the
previous day.

It gets its own scraper rather than going through `github_md` for two reasons:

1. The repo renders its listings as HTML `<table>` markup, not the pipe-tables
   the markdown parser handles. Pointed at its README, `github_md` returns 0
   jobs -- silently, with no error.
2. The repo publishes `.github/scripts/listings.json`, which is what generates
   those READMEs. It carries a real `date_posted` epoch, an `active` flag and
   an `is_visible` flag, so reading the source of truth gives exact posting
   times and does not break when the page markup changes again.

The feed covers every season and category at once, so the filtering a URL
would do on the site has to happen here instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..http import get_json
from ..locations import is_us
from ..models import Job

log = logging.getLogger(__name__)

FEED_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def _wanted_season(terms: list[str], wanted_years: list[str]) -> bool:
    """Keep only postings tagged with a season we care about.

    Postings tagged `N/A` are dropped. The feed carries off-season and prior
    -year listings in the same file, so an untagged entry cannot be confirmed
    as a 2027 role -- and the other sources in this config are season-curated,
    so admitting unverifiable ones here would be the odd source out.
    """
    real = [t for t in (terms or []) if t and t != "N/A"]
    return any(any(year in t for year in wanted_years) for t in real)


def fetch(session, cfg: dict) -> list[Job]:
    url = FEED_URL.format(
        repo=cfg.get("repo", "SimplifyJobs/Summer2027-Internships"),
        branch=cfg.get("branch", "dev"),
        path=cfg.get("path", ".github/scripts/listings.json"),
    )
    data = get_json(session, url, timeout=60)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list from {url}, got {type(data).__name__}")

    categories = set(cfg.get("categories") or [])
    wanted_years = [str(y) for y in (cfg.get("years") or ["2027"])]
    us_only = cfg.get("us_only", True)
    name = cfg.get("name", "SimplifyJobs")

    jobs: list[Job] = []
    skipped = {"inactive": 0, "category": 0, "season": 0, "non_us": 0}

    for rec in data:
        if not (rec.get("active") and rec.get("is_visible")):
            skipped["inactive"] += 1
            continue

        if categories and rec.get("category") not in categories:
            skipped["category"] += 1
            continue

        if not _wanted_season(rec.get("terms"), wanted_years):
            skipped["season"] += 1
            continue

        locations = rec.get("locations") or []
        if us_only and not is_us(locations):
            skipped["non_us"] += 1
            continue

        apply_url = (rec.get("url") or "").strip()
        company = (rec.get("company_name") or "").strip()
        title = (rec.get("title") or "").strip()
        if not (apply_url and company and title):
            continue

        terms = [t for t in (rec.get("terms") or []) if t and t != "N/A"]
        jobs.append(
            Job(
                company=company,
                title=title,
                apply_url=apply_url,
                source=name,
                location=", ".join(locations[:2]),
                season=", ".join(terms),
                posted=_format_date(rec.get("date_posted")),
                # The feed and Simplify's Typesense index use the SAME posting
                # UUIDs. Carrying it is what lets the two be recognised as one
                # job -- see canonical.keys_for.
                extra={"feed_id": rec.get("id", ""),
                       "category": rec.get("category", "")},
            )
        )

    log.info(
        "%s: %d postings [skipped %d inactive, %d off-category, %d wrong season, "
        "%d non-US]", name, len(jobs), skipped["inactive"], skipped["category"],
        skipped["season"], skipped["non_us"],
    )
    return jobs


def _format_date(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%b %d")
    except (ValueError, OSError, OverflowError, TypeError):
        return ""
