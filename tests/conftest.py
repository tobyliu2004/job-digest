"""Shared fixtures: the frozen live corpus and a scratch seen-store.

tests/fixtures/live_jobs.json is a real scrape of all seven sources, captured
pre-dedupe and pre-filter with `--dump-jobs`. Hand-written fixtures cannot
reproduce the URL shapes that actually break deduplication (Workday host
shards, query-string-identified ATSes, markdown tables that mix job rows with
programme rows), so the invariant tests run against real data.

Refresh with:
    python -m src.main --dry-run --no-store --force \
        --dump-jobs tests/fixtures/live_jobs.json
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.corpus import load_results
from src.store import SeenStore

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "live_jobs.json"
SEEN_SNAPSHOT = FIXTURES / "seen_snapshot.json"


@pytest.fixture(scope="session")
def corpus_results():
    """Every scraped job, grouped by source, exactly as collect() returns it."""
    return load_results(CORPUS)


@pytest.fixture(scope="session")
def corpus_jobs(corpus_results):
    return [job for result in corpus_results for job in result.jobs]


@pytest.fixture
def fresh_results(corpus_results):
    """A deep-ish copy, for tests that mutate Job.relevance."""
    return load_results(CORPUS)


@pytest.fixture
def seen_snapshot(tmp_path):
    """Real production state as it stood BEFORE the key migration (3,431 keys).

    Deliberately frozen at that moment and never refreshed: it is the input the
    migration tests need. Replacing it with current state would leave those
    tests asserting that already-migrated keys migrate fine, which proves
    nothing.
    """
    path = tmp_path / "seen.json"
    shutil.copy(SEEN_SNAPSHOT, path)
    return SeenStore(path)


@pytest.fixture
def empty_store(tmp_path):
    return SeenStore(tmp_path / "seen.json")
