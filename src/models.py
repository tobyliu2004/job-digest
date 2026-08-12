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
    # True when the source has not tagged the posting's season, so we cannot
    # confirm it is a 2027 role. Freshly-posted jobs are the ones most often
    # untagged, so these are surfaced in their own section rather than dropped.
    unconfirmed: bool = False
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.company = (self.company or "").strip()
        self.title = (self.title or "").strip()
        self.apply_url = (self.apply_url or "").strip()
        self.location = (self.location or "").strip()

    # --- serialization, for the pending queue in state/seen.json -----------
    # Hourly runs accumulate new jobs between the two daily emails, so a Job
    # has to survive a process restart.

    def to_dict(self) -> dict:
        return {
            "company": self.company, "title": self.title, "apply_url": self.apply_url,
            "source": self.source, "location": self.location, "season": self.season,
            "salary": self.salary, "posted": self.posted, "indirect": self.indirect,
            "unconfirmed": self.unconfirmed, "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {f for f in (
            "company", "title", "apply_url", "source", "location", "season",
            "salary", "posted", "indirect", "unconfirmed", "extra",
        )}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class SourceResult:
    """Outcome of one source. A failed source must not poison the run."""

    name: str
    jobs: list[Job] = field(default_factory=list)
    ok: bool = True
    error: str = ""
