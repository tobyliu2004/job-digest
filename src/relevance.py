"""Is this posting worth reading? (Separate from "is it an internship?")

is_internship in main.py answers seniority. This answers domain: a "Tax Intern"
is a real internship and passes that filter, but it is not a software job.

RULE ORDER (see judge() -- the first match wins)

    1. block_titles / block_companies / block_urls -> drop
    2. restrict_titles                             -> demote to Maybe
    3. maybe_titles, unless allow_titles rescues   -> demote to Maybe
    4. otherwise                                   -> keep

Steps 1 and 2 are facts about the posting and the allowlist cannot touch them.
Only step 3 is a guess about subject matter, so only step 3 consults it.

WHY STEP 3 NEEDS AN ALLOWLIST AT ALL

A blocklist of off-domain words used alone quietly deletes real jobs: measured
against 1,533 titles from live state, a broad off-domain regex matched 94, and
many were roles worth seeing -- "embedded software", "software quality
engineer", "trust and safety engineer". A tech-keyword allowlist used as the
*only* gate is just as bad in the other direction; it would have dropped
"Infrastructure Intern" (Etched), "Video Algorithms Intern" (Netflix) and
"Gameplay Programmer Intern" (Epic Games), all real postings from 2026-08-13.
Letting the allowlist override only the off-domain guess gets both right.

WHAT THE ALLOWLIST MUST NOT CONTAIN

Every word in allow_titles rescues a posting outright, so a word that is not
exclusively software silently disables the rule it was meant to refine. Bare
"engineer" did exactly that: it rescued every hardware title, and the
2026-08-14 digest opened with nine ASIC/FPGA/silicon/thermal roles. Adding the
hardware patterns to maybe_titles while leaving allow_titles alone moved
exactly one posting out of 1,033. Widen maybe_titles and allow_titles together,
or not at all -- and check with --audit-filter, not by reasoning about it.

DROPPED JOBS ARE NOT REMEMBERED

main.py never stores a dropped job's keys. They cost nothing to re-judge each
run, so loosening a pattern later brings them straight back. Demoted "maybe"
jobs ARE stored, because they were shown to you.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

KEEP, MAYBE, DROP = "keep", "maybe", "drop"


@dataclass(frozen=True)
class Verdict:
    action: str          # keep | maybe | drop
    rule: str = ""       # which list fired, e.g. "block_titles"
    pattern: str = ""    # the pattern itself, verbatim, for --audit-filter
    rescued: bool = False  # allowlist overrode a block/maybe hit


class Relevance:
    """Compiled rules. Construct via load()."""

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.mode: str = cfg.get("mode", "maybe")
        self._allow = _compile(cfg, "allow_titles")
        self._block_titles = _compile(cfg, "block_titles")
        self._block_companies = _compile(cfg, "block_companies")
        self._block_urls = _compile(cfg, "block_urls")
        self._restrict = _compile(cfg, "restrict_titles")
        self._maybe = _compile(cfg, "maybe_titles")
        # Exact names, not regexes: the feed's category vocabulary is a short
        # closed set, and an exact match makes an unrecognised name fall
        # through to the title rules rather than half-matching something.
        self._maybe_categories = set(cfg.get("maybe_categories") or [])

    def judge(self, job) -> Verdict:
        if not self.enabled or self.mode == "off":
            return Verdict(KEEP)

        # Blocks are ABSOLUTE -- the allowlist cannot override them. They answer
        # "is this a job posting I can apply to at all?", which no amount of
        # software vocabulary in the title changes: "Google - Summer of Code"
        # and "US Military Skillbridge Internship - Software Engineer" both read
        # as software and are both still useless here.
        blocked = (
            _labelled(_first(self._block_titles, job.title), "block_titles")
            or _labelled(_first(self._block_companies, job.company), "block_companies")
            or _labelled(_first(self._block_urls, job.apply_url), "block_urls")
        )
        if blocked:
            rule, pattern = blocked
            return Verdict(DROP, rule, pattern)

        # Restrictions are about eligibility, not subject matter, so the
        # allowlist has no bearing on them: "AI Engineer (Unpaid Internship)"
        # is unmistakably an AI job and still unpaid, and a co-op reserved for
        # Drexel students stays reserved however well it matches. Demoted
        # rather than dropped, because the judgement is yours to make.
        restricted = _labelled(_first(self._restrict, job.title), "restrict_titles")
        if restricted:
            rule, pattern = restricted
            return Verdict(DROP if self.mode == "drop" else MAYBE, rule, pattern)

        # A title rule fires first; the source's own category is the backstop
        # for titles whose vocabulary no rule anticipated. It is only ever a
        # HINT -- measured on the 2026-08-15 feed, 15 of 32 postings Simplify
        # files under "Hardware" are real software jobs (five RTX "Software
        # Engineer Intern", Varda "Flight Software", Specter "Embedded Software
        # Co-op", Tesla firmware). So it demotes exactly like a title rule
        # does, and allow_titles below rescues all of those.
        maybe_hit = (_labelled(_first(self._maybe, job.title), "maybe_titles")
                     or _labelled(self._category(job), "maybe_categories"))
        if not maybe_hit:
            return Verdict(KEEP)

        # The allowlist exists for exactly this decision: an off-domain word
        # only demotes a posting when nothing else in the title looks like
        # software. "Mechanical Systems Software Engineer Intern" is a software
        # job that happens to say "mechanical".
        allow_hit = _first(self._allow, job.title)
        if allow_hit:
            return Verdict(KEEP, "allow_titles", allow_hit, rescued=True)
        rule, pattern = maybe_hit
        # mode: drop treats the borderline list as a blocklist too.
        return Verdict(DROP if self.mode == "drop" else MAYBE, rule, pattern)

    def _category(self, job) -> str:
        """The job's source category, if it is one we demote on.

        Only the SimplifyJobs feed carries a category (src/scrapers/
        simplify_repo.py puts it in Job.extra). Every other source returns ""
        here and is judged on its title alone -- which is why the hardware
        title rules stay: they cover the ~70% of the corpus with no category.
        """
        category = (getattr(job, "extra", None) or {}).get("category") or ""
        return category if category in self._maybe_categories else ""

    def tag(self, job) -> bool:
        """Judge a job, record the verdict on it, and say whether to keep it.

        Used as the predicate in main.filter_results, so one pass both filters
        and annotates. Returns False only for a hard drop.
        """
        verdict = self.judge(job)
        job.relevance = verdict.action
        job.relevance_rule = verdict.rule if verdict.action == MAYBE else ""
        return verdict.action != DROP


def _compile(cfg: dict, key: str) -> list[re.Pattern]:
    out = []
    for raw in cfg.get(key) or []:
        try:
            out.append(re.compile(raw, re.I))
        except re.error as exc:
            # Fail loudly at load: a broken pattern that silently never matches
            # would look like a filter that works and quietly does nothing.
            raise ValueError(f"relevance.{key}: bad regex {raw!r}: {exc}") from exc
    return out


def _first(patterns: list[re.Pattern], text: str) -> str:
    for pattern in patterns:
        if pattern.search(text or ""):
            return pattern.pattern
    return ""


def _labelled(pattern: str, rule: str) -> tuple[str, str] | None:
    return (rule, pattern) if pattern else None


def load(config: dict) -> Relevance:
    rel = Relevance(config.get("relevance") or {})
    if rel.enabled:
        log.info("Relevance filter active (mode: %s)", rel.mode)
    return rel
