"""Serialise scraped jobs to JSON, so tests can replay a real run offline.

The dedup and relevance logic is only as good as the data it is tuned against,
and a hand-written fixture cannot reproduce the messy URL shapes that actually
break it (Workday shards, query-param ATSes, mixed markdown tables). So a live
run is frozen into tests/fixtures/live_jobs.json once and replayed forever:

    python -m src.main --dry-run --no-store --force --dump-jobs tests/fixtures/live_jobs.json

The dump happens BEFORE dedup and filtering, so the fixture is the raw input to
every stage under test. Refresh it when sources change shape; the pinned
threshold tests will tell you if the refresh moved something unexpectedly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Job, SourceResult

# Bumped when the Job dataclass gains a field that fixtures must carry.
SCHEMA_VERSION = 1


def dump_results(results: list[SourceResult], path: str | Path) -> int:
    """Write every scraped job, grouped by source, to `path`. Returns the count."""
    payload = {
        "schema": SCHEMA_VERSION,
        "sources": [
            {
                "name": r.name,
                "ok": r.ok,
                "error": r.error,
                "jobs": [asdict(j) for j in r.jobs],
            }
            for r in results
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
    return sum(len(r.jobs) for r in results)


def load_results(path: str | Path) -> list[SourceResult]:
    """Rebuild SourceResults from a dump. The inverse of dump_results."""
    with open(path) as fh:
        payload = json.load(fh)

    if payload.get("schema") != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: fixture schema {payload.get('schema')} != {SCHEMA_VERSION}. "
            "Regenerate it with --dump-jobs."
        )

    results = []
    for src in payload["sources"]:
        jobs = [Job(**_forwards_compatible(j)) for j in src["jobs"]]
        results.append(SourceResult(src["name"], jobs, src["ok"], src["error"]))
    return results


def load_jobs(path: str | Path) -> list[Job]:
    """Every job in a dump, flattened."""
    return [j for r in load_results(path) for j in r.jobs]


_JOB_FIELDS = set(Job.__dataclass_fields__)


def _forwards_compatible(raw: dict) -> dict:
    """Drop fields a newer dump carries that this Job no longer has."""
    return {k: v for k, v in raw.items() if k in _JOB_FIELDS}
