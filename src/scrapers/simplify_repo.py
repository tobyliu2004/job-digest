"""Pitt CSC x Simplify GitHub list, read from its structured feed.

`SimplifyJobs/Summer2027-Internships` is the canonical internship list -- the
other repos in config are smaller derivatives of it. Two reasons this gets its
own scraper instead of going through `github_md`:

1. That repo renders its listings as HTML `<table>` markup, not the pipe-tables
   the markdown parser handles. Pointed at its README, `github_md` returns 0
   jobs -- silently.
2. The repo publishes `.github/scripts/listings.json`, which is what actually
   generates those READMEs. It carries a real `date_posted` epoch, an `active`
   flag and an `is_visible` flag. Reading the source of truth means we get exact
   posting times (not "0d") and never fight a README format change.

The feed covers every season at once, so the season/location filtering that a
URL would do on Simplify's own site has to happen here instead. Postings whose
`terms` are missing or `N/A` are NOT dropped -- they are the newest, least
tagged, most easily missed ones. They are flagged `unconfirmed` so the digest
can show them in their own section rather than silently discarding them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..http import get_json
from ..locations import is_us
from ..models import Job

log = logging.getLogger(__name__)

FEED_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def _season_status(terms: list[str], wanted_years: list[str]) -> str | None:
    """'match', 'unconfirmed', or None (drop).

    A posting is 'unconfirmed' when the list has not tagged its season yet.
    Those are disproportionately the freshest postings, so dropping them would
    defeat the point of the digest.
    """
    real = [t for t in (terms or []) if t and t != "N/A"]
    if not real:
        return "unconfirmed"
    if any(any(year in t for year in wanted_years) for t in real):
        return "match"
    return None


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
    counts = {"inactive": 0, "category": 0, "season": 0, "non_us": 0, "unconfirmed": 0}

    for rec in data:
        if not (rec.get("active") and rec.get("is_visible")):
            counts["inactive"] += 1
            continue

        if categories and rec.get("category") not in categories:
            counts["category"] += 1
            continue

        status = _season_status(rec.get("terms"), wanted_years)
        if status is None:
            counts["season"] += 1
            continue

        locations = rec.get("locations") or []
        if us_only and not is_us(locations):
            counts["non_us"] += 1
            continue

        apply_url = (rec.get("url") or "").strip()
        company = (rec.get("company_name") or "").strip()
        title = (rec.get("title") or "").strip()
        if not (apply_url and company and title):
            continue

        if status == "unconfirmed":
            counts["unconfirmed"] += 1

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
                unconfirmed=(status == "unconfirmed"),
                extra={
                    "feed_id": rec.get("id", ""),
                    "date_posted": rec.get("date_posted") or 0,
                    "category": rec.get("category", ""),
                    "sponsorship": rec.get("sponsorship", ""),
                },
            )
        )

    log.info(
        "%s: %d postings (%d unconfirmed season) "
        "[skipped %d inactive, %d off-category, %d wrong season, %d non-US]",
        name, len(jobs), counts["unconfirmed"], counts["inactive"],
        counts["category"], counts["season"], counts["non_us"],
    )
    return jobs


def _format_date(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%b %d")
    except (ValueError, OSError, OverflowError, TypeError):
        return ""
