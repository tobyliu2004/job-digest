"""Is this employer worth an email? (Separate from "is it a software job?")

relevance.py judges the ROLE. This judges the COMPANY, against the curated list
in config/employers.yaml: the bar is "at least as good a resume line as the
Capital One SWE internship already in hand". Anything unlisted is dropped.

WHY A LIST AND NOT A MODEL

"Is Two Sigma better than Capital One?" is a stable judgement, not something a
title can reveal, and the answer should not change between runs. A hand-kept
list is deterministic, free, and auditable; `--audit-filter` prints the
unlisted companies that had software postings so the list can be topped up.

WHY EXACT MATCHING

Substring matching reads "Apple" into "Applied Materials" and "Epic" into
Epic Systems. Names are compared whole, after the same normalisation the dedup
keys use plus two extra steps -- dropping a "(...)" suffix and all spaces -- so
"D. E. Shaw & Co.", "DE Shaw" and "JPMorganChase"/"JP Morgan Chase" line up
without an alias apiece.

DROPPED JOBS ARE NOT REMEMBERED

Like relevance drops, main.py never stores these, so adding a company to the
YAML brings its currently-open postings back on the next run.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from . import canonical

log = logging.getLogger(__name__)

EMPLOYERS_PATH = Path(__file__).resolve().parent.parent / "config" / "employers.yaml"

_PAREN = re.compile(r"\([^)]*\)")


def normalize(name: str) -> str:
    """'Susquehanna International Group (SIG)' -> 'susquehannainternational'."""
    return canonical.normalize_company(_PAREN.sub(" ", name or "")).replace(" ", "")


class Employers:
    """The compiled list. Construct via load()."""

    def __init__(self, cfg: dict | None = None):
        self.names: dict[str, str] = {}   # normalised key -> display name
        groups = (cfg or {}).get("employers") or {}
        for group, entries in groups.items():
            for entry in entries or []:
                if isinstance(entry, str):
                    name, aliases = entry, []
                elif isinstance(entry, dict) and len(entry) == 1:
                    name, aliases = next(iter(entry.items()))
                    aliases = aliases or []
                else:
                    raise ValueError(f"employers.{group}: bad entry {entry!r}")
                for spelling in dict.fromkeys([name, *aliases]):
                    self._add(normalize(spelling), name, group)

    def _add(self, key: str, name: str, group: str) -> None:
        if not key:
            raise ValueError(f"employers.{group}: {name!r} normalises to nothing")
        other = self.names.get(key)
        if other is not None and other != name:
            # Fail loudly: two entries claiming one name means one of them is a
            # typo, and silently keeping either would hide the other.
            raise ValueError(
                f"employers.{group}: {name!r} and {other!r} both normalise to {key!r}")
        self.names[key] = name

    def match(self, company: str) -> str:
        """The listed display name for this company, or '' if unlisted."""
        return self.names.get(normalize(company), "")

    def listed(self, job) -> bool:
        return bool(self.match(job.company))


def load(path: Path = EMPLOYERS_PATH) -> Employers:
    with open(path) as fh:
        employers = Employers(yaml.safe_load(fh))
    log.info("Employer list: %d spellings of listed companies", len(employers.names))
    return employers
