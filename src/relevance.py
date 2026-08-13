"""Is this posting worth reading? (Separate from "is it an internship?")

is_internship in main.py answers seniority. This answers domain: a "Tax Intern"
is a real internship and passes that filter, but it is not a software job.

WHY THE ALLOWLIST IS CHECKED FIRST, AND WINS OUTRIGHT

The obvious design -- a blocklist of off-domain words -- quietly deletes real
jobs. Measured against 1,533 titles from live state, a broad off-domain regex
matched 94, and most were roles worth seeing: "hardware engineer", "embedded
software", "software quality engineer", "trust and safety engineer". A
tech-keyword allowlist used as the *only* gate is just as bad in the other
direction; it would have dropped "Infrastructure Intern" (Etched), "Video
Algorithms Intern" (Netflix) and "Gameplay Programmer Intern" (Epic Games),
all real postings scraped on 2026-08-13.

So the rules are ordered, and a title that looks like software engineering is
immune to everything below it:

    1. allow_titles     -> keep, unconditionally
    2. block_*          -> drop
    3. maybe_titles     -> demote to the Maybe section
    4. otherwise        -> keep

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

        maybe_hit = _labelled(_first(self._maybe, job.title), "maybe_titles")
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
