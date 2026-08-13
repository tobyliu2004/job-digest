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

A NOTE ON WHICH WAY TO ERR
--------------------------
The two failure modes are not symmetric.

  under-merge -> the same job is emailed twice. Visible, mildly annoying.
  over-merge  -> two different jobs share a key, so one of them is marked seen
                 without ever being sent. It is then suppressed FOREVER,
                 silently, and nothing in the digest reveals the loss.

So every judgement call here is biased toward keys that are too specific rather
than too loose. An ATS id is only trustworthy when it is qualified by the
employer: Workday requisition ids (JR0000037453) and iCIMS job ids (10717) are
per-tenant counters, and low-numbered ones collide across companies constantly.
Every ATS key below therefore carries the tenant, whether it lives in the host
(acme.wd5.myworkdayjobs.com) or the path (jobs.jobvite.com/altamiracorps/...).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import canonical_legacy

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
    # Workday. The trailing _R0000379955 / _JR2015779 requisition id is stable
    # across the many wd1..wd103 host shards, which is why we key on it -- but
    # it is only unique WITHIN a tenant. Live state held wd:jr002718 and
    # wd:jr012201 style keys with no tenant at all, and short counters like
    # those collide across employers, silently burying one company's job under
    # another's. The tenant is the first host label and is stable across shards
    # (nvidia.wd1 and nvidia.wd5 are the same tenant), so capture both.
    (
        re.compile(
            r"//(?:www\.)?([a-z0-9][a-z0-9-]*)\.wd\d+\.myworkdayjobs\.com"
            r"/.*?_((?:JR|R|REQ)[-_]?\d[\w-]*)",
            re.I,
        ),
        lambda m: f"wd:{m.group(1).lower()}:{_squash(m.group(2))}",
    ),
    # Workday's internal API form: myworkdayjobs.com/wday/cxs/<tenant>/<site>/...
    (
        re.compile(
            r"myworkdayjobs\.com/wday/cxs/([a-z0-9-]+)/.*?_((?:JR|R|REQ)[-_]?\d[\w-]*)",
            re.I,
        ),
        lambda m: f"wd:{m.group(1).lower()}:{_squash(m.group(2))}",
    ),
    # Workday tenant-hosted variant (wd1.myworkdaysite.com/recruiting/<tenant>/
    # <site>/job/...), used by Wells Fargo among others. Here the host shard
    # carries no tenant at all -- it is the first path segment after
    # /recruiting/ -- so the host-based pattern above cannot fire.
    (
        re.compile(
            r"myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([a-z0-9_-]+)/"
            r".*?_((?:JR|R|REQ)[-_]?\d[\w-]*)",
            re.I,
        ),
        lambda m: f"wd:{m.group(1).lower()}:{_squash(m.group(2))}",
    ),
    # Any remaining Workday shape: keep the requisition id but qualify it with
    # the host so it can still never collide across employers.
    (
        re.compile(
            r"//([a-z0-9.-]*?myworkday(?:jobs|site)\.com)/.*?_((?:JR|R|REQ)[-_]?\d[\w-]*)",
            re.I,
        ),
        lambda m: f"wd:{m.group(1).lower().removeprefix('www.')}:{_squash(m.group(2))}",
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
    # SmartRecruiters -- the company is the path segment before the id.
    (
        re.compile(r"smartrecruiters\.com/([^/?#]+)/(\d+)", re.I),
        lambda m: f"sr:{m.group(1).lower()}:{m.group(2)}",
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
        re.compile(r"jobs\.jobvite\.com/([^/]+)/job/([a-zA-Z0-9]+)", re.I),
        lambda m: f"jv:{m.group(1).lower()}:{m.group(2)}",
    ),
    # iCIMS job ids are a per-tenant counter -- careers-a.icims.com/jobs/1234
    # and careers-b.icims.com/jobs/1234 are unrelated postings. The tenant is
    # the first host label (careers-sig, careers-acme, ...).
    (
        re.compile(r"//([a-z0-9][a-z0-9-]*)\.icims\.com/jobs/(\d+)", re.I),
        lambda m: f"icims:{m.group(1).lower()}:{m.group(2)}",
    ),
    # Workable: apply.workable.com/<tenant>/j/<ID>/
    (
        re.compile(r"workable\.com/([^/?#]+)/j(?:obs)?/([A-Z0-9]{6,})", re.I),
        lambda m: f"wk:{m.group(1).lower()}:{m.group(2).upper()}",
    ),
    (
        re.compile(r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d{6,})", re.I),
        lambda m: f"li:{m.group(1)}",
    ),
]

def _squash(s: str) -> str:
    """JR-000-123 / JR_000_123 / jr000123 all mean the same requisition."""
    return s.lower().replace("-", "").replace("_", "")


# Query params that carry no identity -- purely tracking or presentation.
# Everything NOT matching this is kept in the fallback key, because on many
# job boards the posting id lives in the query string and nowhere else.
_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|gh_src|gh_jid|src|source|ref|referrer|trk|trackingId|embed|"
    r"lever-source|ashby_jid|jobPipeline|iis|iisn|"
    r"mobile|needsRedirect|hl|lang|locale|nl|fr|from|_ga|fbclid|gclid)$",
    re.I,
)


def _query_signature(query: str) -> str:
    """The identity-bearing part of a query string, order-normalised."""
    pairs = [
        (k, v) for k, v in parse_qsl(query, keep_blank_values=False)
        if not _TRACKING_PARAMS.match(k)
    ]
    return ("?" + urlencode(sorted(pairs))) if pairs else ""


# Keys that identify a posting on an AGGREGATOR rather than an employer's own
# ATS. They are perfectly good dedup keys, but they say nothing about which
# requisition an employer opened, so they must not veto a tier-2 match -- every
# LinkedIn copy of a job carries one, and vetoing on it would stop LinkedIn
# duplicates merging with the employer link at all.
_AGGREGATOR_PREFIXES = ("li:",)


def employer_ats_key(url: str) -> str:
    """A strong key issued by the EMPLOYER's ATS. "" for aggregators.

    This is the evidence the tier-2 veto runs on. See main.dedupe.
    """
    key = strong_url_key(url)
    return "" if key.startswith(_AGGREGATOR_PREFIXES) else key


def strong_url_key(url: str) -> str:
    """The ATS key when a real ATS pattern matched; "" when it did not.

    The distinction is what lets tier 2 stay safe. A tier-2 identity key is a
    guess built from company/title/location, and some employers post many
    genuinely separate requisitions under a byte-identical title: TikTok
    currently lists six distinct "Software Engineer Intern" roles in San Jose.
    Nothing in the title separates them, so tier 2 merges all six and five are
    never emailed.

    An employer-issued requisition id does separate them. So when two jobs each
    carry a confident ATS id and those ids differ, that is hard evidence they
    are different postings, and it overrules a tier-2 match. See main.dedupe.
    """
    if not url:
        return ""
    for pattern, fmt in _ATS_PATTERNS:
        m = pattern.search(url)
        if m:
            return fmt(m)
    return ""


def canonical_url_key(url: str) -> str:
    """Tier 1: a stable identity for a job application URL.

    Falls back to a normalised host + path + identity-bearing query when no
    known ATS pattern matches.

    That query part matters more than it looks. Dropping the whole query
    string, as this used to, means every posting on a board that identifies
    jobs by parameter -- SuccessFactors (?career_job_req_id=), Oracle, Point72,
    Microsoft -- collapses onto ONE key. Live state still shows the damage:
    keys like `url:career41.sapsf.com/career` and
    `url:careers.point72.com/csjobdetail` are single entries standing in for
    every job those boards will ever post, so all but the first were silently
    marked seen and never sent.
    """
    strong = strong_url_key(url)
    if strong:
        return strong
    if not url:
        return ""

    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    return f"url:{host}{path}{_query_signature(parts.query)}"


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


# Location words that carry no geography. Matched as whole TOKENS: doing this
# as substring replacement turned "Austin, TX" into "a tin tx" and
# "Houston, TX" into "ho ton tx", because "us" appears inside both.
_LOC_NOISE = {
    "united", "states", "usa", "us", "u.s.", "u.s.a.", "america",
    "remote", "hybrid", "onsite", "on-site", "in",
}


def normalize_location(loc: str) -> str:
    """Reduce a location to 'city st' so 'New York, NY, USA' == 'New York, NY'."""
    return " ".join(t for t in _norm(loc).split() if t not in _LOC_NOISE)


# Season/level are stripped from the title by _TITLE_NOISE (so that
# "SWE Intern - BS - Summer 2027" and "SWE Intern, Summer 2027" still match),
# then re-added to the key as explicit fields by identity_key. Stripping them
# without re-adding them is what made "SWE Intern Summer 2027", "SWE Intern
# Fall 2027" and "SWE, New Grad" one single key.
_SEASONS = (("summer", "su"), ("fall", "fa"), ("autumn", "fa"),
            ("winter", "wi"), ("spring", "sp"))
_YEAR_RE = re.compile(r"\b(20\d\d)\b")

INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|co-?ops?|co\s+ops?)\b", re.I)
NEW_GRAD_RE = re.compile(
    r"\b(new\s*grad(uate)?|graduate\s+(program|role|position|scheme)|\d{4}\s+grads?"
    r"|associate|entry[\s-]?level|full[\s-]?time|staff|principal|senior|sr\.?|lead"
    r"|trainee|volunteer|apprentice(ship)?)\b",
    re.I,
)


def season_token(*texts: str) -> str:
    """'Summer 2027' -> 'su2027'. Takes the first text that yields a season."""
    for text in texts:
        if not text:
            continue
        low = text.lower()
        name = next((abbr for word, abbr in _SEASONS if word in low), "")
        if not name:
            continue
        year = _YEAR_RE.search(text)
        return f"{name}{year.group(1)}" if year else name
    return ""


def level_token(title: str) -> str:
    """'intern' | 'newgrad' | '' -- read from the RAW title, before stripping."""
    if INTERN_RE.search(title or ""):
        return "intern"
    if NEW_GRAD_RE.search(title or ""):
        return "newgrad"
    return ""


def identity_key(company: str, title: str, location: str = "",
                 season: str = "", level: str = "") -> str:
    """Tier 2: fuzzy identity for the same role posted to two different ATSes.

    Deliberately conservative. If a source omits the season, its key differs
    from a source that supplies one and the job may be emailed twice -- which
    is a far better outcome than merging a Summer role with a Fall one and
    never sending the second.
    """
    c = normalize_company(company)
    t = normalize_title(title)
    if not c or not t:
        return ""
    loc = normalize_location(location)
    return f"id:{c}|{t}|{loc}|{level or level_token(title)}|{season_token(season, title)}"


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


def keys_for(job, *, include_legacy: bool = False) -> list[str]:
    """All dedup keys for a job. Empty keys are dropped, order preserved.

    `include_legacy` appends the keys this job would have had before the
    tenant/query/season fixes. It is for the seen-*read* path only, during the
    migration window -- writing legacy keys would re-introduce the very
    collisions the new formats exist to prevent. See src/migrate.py.
    """
    keys = [
        simplify_uuid_key(job),
        canonical_url_key(job.apply_url),
        identity_key(job.company, job.title, job.location, job.season),
    ]
    if include_legacy:
        keys += canonical_legacy.legacy_keys_for(job)
    # dict.fromkeys dedupes while keeping order, so the strongest key stays first.
    return [k for k in dict.fromkeys(keys) if k]
