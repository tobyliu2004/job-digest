"""One-time backfill of state/seen.json onto the fixed key formats.

WHY THIS EXISTS

canonical.py used to emit keys that merged unrelated jobs: Workday and iCIMS
keys carried no tenant, the URL fallback threw away the query string, and the
identity key erased season and level. Those are fixed, but state/seen.json
holds ~3,400 keys written in the OLD formats. Change the format and every
stored key stops matching, so the next digest treats the entire backlog as new
and emails hundreds of jobs already applied to.

WHY DUAL-EMITTING BOTH FORMATS IS NOT ENOUGH

The obvious fix -- have keys_for() emit new AND old keys, and let has_any()'s
OR do the rest -- preserves the exact bug it is meant to unblock. If
`wd:jr1234` is in state because Company A's job was emailed, then Company B's
unrelated `wd:jr1234` still matches and is still suppressed. Dual-emit would
keep that alive for the full 180-day retention window.

So the old keys are translated into new ones instead, once, deliberately.

THE POISONED-KEY PROBLEM

Translation is only sound when a legacy key means exactly one job. Some do not:
`url:career41.sapsf.com/career` is one stored entry standing in for every job
that board has ever posted, because the old code discarded the query string
that identified them. Backfilling that key onto all N of today's matching jobs
would mark N jobs as already-emailed when at most ONE ever was -- converting a
past bug into permanent, silent suppression of jobs you have never seen.

So a legacy key matching more than one live job is refused, reported, and left
alone. Those jobs are then simply new, and get sent once. That one-time resend
is the correct outcome: it is the digest catching up on what the old bug hid.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from . import canonical, canonical_legacy, dedupe

log = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    legacy_keys_translated: int = 0
    new_keys_written: int = 0
    poisoned_keys: int = 0
    poisoned_jobs: int = 0
    jobs_new_after: int = 0
    jobs_new_before: int = 0
    poisoned_examples: list[tuple[str, int]] = field(default_factory=list)

    @property
    def one_time_resends(self) -> int:
        """Jobs that will be emailed once because of the migration itself."""
        return max(0, self.jobs_new_after - self.jobs_new_before)

    def render(self) -> str:
        lines = [
            "",
            "=" * 70,
            "KEY MIGRATION",
            "=" * 70,
            f"  legacy keys translated : {self.legacy_keys_translated}",
            f"  new keys written       : {self.new_keys_written}",
            f"  poisoned keys refused  : {self.poisoned_keys} "
            f"(covering {self.poisoned_jobs} live postings)",
            "",
            f"  jobs 'new' before      : {self.jobs_new_before}",
            f"  jobs 'new' after       : {self.jobs_new_after}",
            f"  ONE-TIME EXTRA EMAILS  : {self.one_time_resends}",
        ]
        if self.poisoned_examples:
            lines += ["", "  worst poisoned keys (old bug: these hid real jobs):"]
            lines += [f"    {n:3}x  {k}" for k, n in self.poisoned_examples]
        lines.append("=" * 70)
        return "\n".join(lines)


def _count_legacy_new(clusters, store) -> int:
    """Postings that look new under the OLD key formats."""
    return sum(
        1 for c in clusters
        if not store.has_any([k for m in c.members
                              for k in canonical_legacy.legacy_keys_for(m)])
    )


def _count_new(clusters, store) -> int:
    """Postings that would be emailed now.

    Uses the cluster's own key set, which is what main.py checks against the
    store -- not a fresh keys_for() per member. Those differ: dedupe drops keys
    it proved ambiguous, so counting with the raw keys would understate the
    resend and report a migration cheaper than it is.
    """
    return sum(1 for c in clusters if not store.has_any(sorted(c.keys)))


def run(store, results, *, apply: bool = True) -> MigrationReport:
    """Translate legacy keys in `store` onto current formats.

    `apply=False` computes the same report without mutating the store, which is
    what --migrate-keys --dry-run uses to show the resend count up front.
    """
    clusters, _, _ = dedupe.cluster(results)

    report = MigrationReport()
    report.jobs_new_before = _count_legacy_new(clusters, store)

    # Which live POSTINGS does each legacy key point at? Grouping by cluster
    # rather than by scraped job matters: one Greenhouse role picked up by four
    # sources is a single posting, and counting it four times would flag its
    # perfectly good key as poisoned and refuse a safe translation.
    fanout: dict[str, set[int]] = defaultdict(set)
    cluster_keys: dict[int, set[str]] = {}
    for i, group in enumerate(clusters):
        cluster_keys[i] = group.keys
        for member in group.members:
            for key in canonical_legacy.legacy_keys_for(member):
                fanout[key].add(i)

    pending: dict[str, str] = {}          # new key -> original timestamp
    poisoned: list[tuple[str, int]] = []

    for legacy_key, matches in fanout.items():
        stamp = store.keys.get(legacy_key)
        if stamp is None:
            continue                       # never seen; nothing to carry over

        if len(matches) > 1:
            poisoned.append((legacy_key, len(matches)))
            report.poisoned_jobs += len(matches)
            continue

        report.legacy_keys_translated += 1
        for new_key in cluster_keys[next(iter(matches))]:
            # Keep the ORIGINAL first-seen timestamp: re-stamping with today
            # would push the 180-day prune horizon out and keep dead postings
            # in the file for a year.
            pending.setdefault(new_key, stamp)

    report.poisoned_keys = len(poisoned)
    report.poisoned_examples = sorted(poisoned, key=lambda kv: -kv[1])[:10]
    report.new_keys_written = sum(1 for k in pending if k not in store.keys)

    if apply:
        for key, stamp in pending.items():
            store.add([key], when=stamp)
        report.jobs_new_after = _count_new(clusters, store)
    else:
        # Same arithmetic, without touching the store.
        simulated = set(store.keys) | set(pending)
        report.jobs_new_after = sum(
            1 for c in clusters if not (c.keys & simulated)
        )

    return report
