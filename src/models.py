"""Shared data model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    company: str
    title: str
    apply_url: str
    source: str
    location: str = ""
    season: str = ""
    salary: str = ""
    posted: str = ""
    # True when apply_url points at an aggregator rather than the employer's
    # own application page. Only LinkedIn sets this, and those jobs are
    # rendered in their own labelled email section.
    indirect: bool = False
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.company = (self.company or "").strip()
        self.title = (self.title or "").strip()
        self.apply_url = (self.apply_url or "").strip()
        self.location = (self.location or "").strip()


@dataclass
class SourceResult:
    """Outcome of one source. A failed source must not poison the run."""

    name: str
    jobs: list[Job] = field(default_factory=list)
    ok: bool = True
    error: str = ""
