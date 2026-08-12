"""Canonical job identity, for cross-source and cross-day deduplication.

The sources deliberately overlap, and they link to the *same* job with
*different* URLs. Measured on live data, plain URL matching caught almost
nothing while canonical ATS-ID extraction collapsed 25 duplicates out of 535.

The case that proves it -- one Google job, two repos:

    speedyapply: .../jobs/results/85564713261245126
    sndsh404:    .../jobs/results/85564713261245126-software-engineering-intern/

Same posting, different URL. Stripping tracking params would not merge these,
because the difference is a slug in the *path*.

Two tiers are used, and a job is new only if BOTH miss the store:

  tier 1  canonical ATS id  -- exact, cheap, catches the common case
  tier 2  (company, title, location) -- catches the same role posted to two
          different ATS systems, which tier 1 structurally cannot see
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Each entry: (compiled pattern, formatter over the match).
# Ordered most-specific first. All patterns verified against live source data.
_ATS_PATTERNS: list[tuple[re.Pattern, callable]] = [
    # Greenhouse: job-boards.greenhouse.io/org/jobs/123, boards.greenhouse.io/...,
    # and the embed form greenhouse.io/embed/job_app?for=org&token=123
    (
        re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_.-]+)/jobs/(\d+)", re.I),
        lambda m: f"gh:{m.group(1).lower()}:{m.group(2)}",
    ),
    (
        re.compile(r"greenhouse\.io/embed/job_app\?for=([a-z0-9_.-]+).*?[?&]token=(\d+)", re.I),
        lambda m: f"gh:{m.group(1).lower()}:{m.group(2)}",
    ),
    # Lever
    (
        re.compile(r"lever\.co/([a-z0-9_.-]+)/([0-9a-f-]{36})", re.I),
        lambda m: f"lv:{m.group(1).lower()}:{m.group(2).lower()}",
    ),
    # Ashby
    (
        re.compile(r"ashbyhq\.com/([a-z0-9_.-]+)/([0-9a-f-]{36})", re.I),
        lambda m: f"ab:{m.group(1).lower()}:{m.group(2).lower()}",
    ),
    # Workday: the trailing _R0000379955 / _JR2015779 requisition id is stable
    # across the many wd1..wd103 host shards, which is exactly why we key on it.
    (
        re.compile(r"myworkdayjobs\.com/.*?_((?:JR|R|REQ)[-_]?\d[\w-]*)", re.I),
        lambda m: f"wd:{m.group(1).lower().replace('-', '').replace('_', '')}",
    ),
    # Google careers -- the numeric id, ignoring any trailing slug
    (
        re.compile(
            r"google\.com/about/careers/applications/jobs/results/(\d+)"
            r"|careers\.google\.com/jobs/results/(\d+)",
            re.I,
        ),
        lambda m: f"goog:{m.group(1) or m.group(2)}",
    ),
    (
        re.compile(r"jobs\.apple\.com/[a-z-]+/details/(\d+)", re.I),
        lambda m: f"appl:{m.group(1)}",
    ),
    (
        re.compile(r"metacareers\.com/jobs/(\d+)", re.I),
        lambda m: f"meta:{m.group(1)}",
    ),
    (
        re.compile(r"smartrecruiters\.com/[^/]+/(\d+)", re.I),
        lambda m: f"sr:{m.group(1)}",
    ),
    # Greenhouse embedded on a company's own domain
    # (tower-research.com/open-positions/?gh_jid=8044334)
    (
        re.compile(r"[?&]gh_jid=(\d+)", re.I),
        lambda m: f"gh:jid:{m.group(1)}",
    ),
    # JazzHR (aapc.applytojob.com/apply/Bwioas9wbW/...)
    (
        re.compile(r"([a-z0-9-]+)\.applytojob\.com/apply/([A-Za-z0-9]+)", re.I),
        lambda m: f"atj:{m.group(1).lower()}:{m.group(2)}",
    ),
    # Rippling ATS (ats.rippling.com/en-GB/spreeai/jobs/<uuid>)
    (
        re.compile(r"ats\.rippling\.com/(?:[a-z-]+/)?([a-z0-9_.-]+)/jobs/([0-9a-f-]{36})", re.I),
        lambda m: f"rip:{m.group(1).lower()}:{m.group(2).lower()}",
    ),
    (
        re.compile(r"amazon\.jobs/(?:[a-z-]+/)?jobs/(\d+)", re.I),
        lambda m: f"amzn:{m.group(1)}",
    ),
    (
        re.compile(r"lifeattiktok\.com/(?:search|position)/(\d+)", re.I),
        lambda m: f"tt:{m.group(1)}",
    ),
    (
        re.compile(r"jobs\.jobvite\.com/[^/]+/job/([a-zA-Z0-9]+)", re.I),
        lambda m: f"jv:{m.group(1)}",
    ),
    (
        re.compile(r"icims\.com/jobs/(\d+)", re.I),
        lambda m: f"icims:{m.group(1)}",
    ),
    (
        re.compile(r"workable\.com/[^/]*j(?:obs)?/([A-Z0-9]{8,})", re.I),
        lambda m: f"wk:{m.group(1).upper()}",
    ),
    (
        re.compile(r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d{6,})", re.I),
        lambda m: f"li:{m.group(1)}",
    ),
]

# Query params that carry no identity -- purely tracking.
_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|gh_src|gh_jid|src|source|ref|referrer|trk|trackingId|embed|"
    r"lever-source|ashby_jid|jobPipeline|iis|iisn)$",
    re.I,
)


def canonical_url_key(url: str) -> str:
    """Tier 1: a stable identity for a job application URL.

    Falls back to a normalised host+path when no known ATS pattern matches,
    which still merges pure tracking-param differences.
    """
    if not url:
        return ""

    for pattern, fmt in _ATS_PATTERNS:
        m = pattern.search(url)
        if m:
            return fmt(m)

    # Fallback: normalise host + path, drop query/fragment and trailing slash.
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    return f"url:{host}{path}"


_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|"
    r"group|holdings|plc|gmbh|sa|ag|nv|the)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")

# Title noise that varies between sources for the same underlying role.
_TITLE_NOISE = re.compile(
    r"\b(20\d\d|summer|fall|winter|spring|intern|internship|co-?op|"
    r"bs|ms|phd|bachelors?|masters?|undergrad(uate)?|grad(uate)?|new\s*grad)\b",
    re.I,
)


def _norm(text: str) -> str:
    text = _PUNCT.sub(" ", (text or "").lower())
    return _WS.sub(" ", text).strip()


def normalize_company(name: str) -> str:
    name = _norm(name)
    name = _COMPANY_SUFFIXES.sub(" ", name)
    return _WS.sub(" ", name).strip()


def normalize_title(title: str) -> str:
    title = _norm(title)
    title = _TITLE_NOISE.sub(" ", title)
    return _WS.sub(" ", title).strip()


def normalize_location(loc: str) -> str:
    """Reduce a location to 'city st' so 'New York, NY, USA' == 'New York, NY'."""
    loc = _norm(loc)
    for filler in ("united states", "usa", "us", "remote in", "hybrid"):
        loc = loc.replace(filler, " ")
    return _WS.sub(" ", loc).strip()


def identity_key(company: str, title: str, location: str = "") -> str:
    """Tier 2: fuzzy identity for the same role posted to two different ATSes."""
    c = normalize_company(company)
    t = normalize_title(title)
    loc = normalize_location(location)
    if not c or not t:
        return ""
    return f"id:{c}|{t}|{loc}"


def simplify_uuid_key(job) -> str:
    """Tier 0: Simplify's own posting UUID, when the job carries one.

    Simplify's Typesense index and the Pitt CSC feed are two views of the same
    database and use the SAME posting UUIDs -- 452 of ~470 live postings appear
    in both. Nothing else can join them: the Typesense entry's apply_url is a
    `/jobs/click/<id>` stub (resolved to a real ATS URL only for jobs we are
    about to send), while the feed already holds the employer's URL, so tier 1
    sees two unrelated links. Their locations are written differently too
    ("SF, NYC" vs "San Francisco, CA, USA, New York, NY"), so tier 2 misses as
    well.

    Without this key, adding the feed listed 35 postings twice -- Citadel, Two
    Sigma and JP Morgan each appearing once as a Simplify click stub and once
    as a direct employer link.
    """
    extra = getattr(job, "extra", None) or {}
    uuid = extra.get("simplify_id") or extra.get("feed_id")
    return f"sj:{uuid}" if uuid else ""


def keys_for(job) -> list[str]:
    """All dedup keys for a job. Empty keys are dropped."""
    keys = [simplify_uuid_key(job), canonical_url_key(job.apply_url)]
    ident = identity_key(job.company, job.title, job.location)
    if ident:
        keys.append(ident)
    return [k for k in keys if k]
