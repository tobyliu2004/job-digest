"""Collapse the same posting seen through several sources into one entry.

Seven sources deliberately overlap, so a single Citadel internship can arrive
from the Pitt CSC feed, Simplify's index, two GitHub lists and LinkedIn. The
digest must show it once.

TWO RULES DO THE WORK

1. Clustering is TRANSITIVE. If A and B share a key, and B and C share a
   different key, all three are one posting. The old code compared each job
   only against keys already claimed by a surviving job and never recorded a
   collapsed job's own keys, so an A-B-C chain leaked C as a duplicate.

2. A confident ATS id VETOES a title-based match. canonical's tier-2 key is a
   guess assembled from company, title and location, and some employers post
   many separate requisitions under one title -- TikTok currently lists six
   distinct "Software Engineer Intern" roles in San Jose. Merging those loses
   five jobs permanently. When two jobs each carry an employer-issued
   requisition id and the ids differ, that hard evidence wins.

Rule 1 alone would be dangerous: making weak keys transitive lets one bad title
match chain unrelated postings together. Rule 2 is what makes it safe, which is
why they landed together.

WHAT A CLUSTER MUST CARRY

Cluster.keys is the union of EVERY member's keys, not just the survivor's.
Callers store that union. Storing only the survivor's keys would leave a
transitively-merged member unrecorded, so it would look new on the next run and
be emailed again forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import canonical
from .models import Job, SourceResult

log = logging.getLogger(__name__)


@dataclass
class Cluster:
    """One real-world posting, plus every copy of it we saw."""

    job: Job                                   # the representative we email
    keys: set[str] = field(default_factory=set)
    members: list[Job] = field(default_factory=list)
    strong: str = ""                           # employer-issued ATS id, if any

    @property
    def sources(self) -> list[str]:
        return sorted({m.source for m in self.members})


class _UnionFind:
    def __init__(self) -> None:
        self._parent: list[int] = []

    def add(self) -> int:
        self._parent.append(len(self._parent))
        return len(self._parent) - 1

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra
        return ra


def _ordered_jobs(results: list[SourceResult]) -> list[Job]:
    """Direct-link sources first, so the survivor keeps a real employer URL
    rather than a LinkedIn one that needs a sign-in to read."""
    ordered = sorted(results, key=lambda r: any(j.indirect for j in r.jobs))
    return [job for result in ordered for job in result.jobs]


def cluster(results: list[SourceResult]) -> tuple[list[Cluster], int, int]:
    """Group every scraped job. Returns (clusters, collapsed, unusable).

    Two passes, because the veto has to compare finished groups rather than a
    job against a half-built one.

    Pass 1 merges on HARD keys only -- the Simplify posting UUID and employer
    requisition ids. These are facts, so merging on them is always safe.

    Pass 2 then merges those groups on the soft tier-2 identity key, but only
    where the groups do not hold different requisition ids.

    Doing the veto incrementally in one pass is not enough. A Simplify click
    stub carries no requisition id, so nothing contradicts a title match and it
    joins whichever TikTok cluster it meets first; its feed twin then arrives
    holding the SAME posting UUID and drags a second requisition in behind it.
    Four unrelated TikTok roles ended up in one cluster that way. Grouping on
    hard keys first puts each stub with its own twin before any title matching
    happens.

    `collapsed` counts jobs merged into another. `unusable` counts jobs with no
    usable identity at all (no apply URL and no company/title).
    """
    jobs = _ordered_jobs(results)

    uf = _UnionFind()
    clusters: list[Cluster] = []
    owner: dict[str, int] = {}
    unusable = 0

    # ---- pass 1: hard keys ------------------------------------------------
    for job in jobs:
        keys = canonical.keys_for(job)
        if not keys:
            unusable += 1
            continue

        hard = [k for k in keys if not k.startswith("id:")]
        hits = {uf.find(owner[k]) for k in hard if k in owner}

        if hits:
            root = min(hits)
            for other in hits:
                root = uf.union(root, other)
            _absorb(clusters, root, hits)
        else:
            root = uf.add()
            clusters.append(Cluster(job=job))

        target = clusters[root]
        target.keys.update(keys)
        target.members.append(job)
        target.strong = target.strong or canonical.employer_ats_key(job.apply_url)
        for key in hard:
            owner.setdefault(key, root)

    # ---- pass 2: soft identity keys, vetoed by conflicting requisitions ----
    by_identity: dict[str, list[int]] = {}
    for idx, group in enumerate(clusters):
        for key in group.keys:
            if key.startswith("id:"):
                by_identity.setdefault(key, []).append(idx)

    for candidates in by_identity.values():
        roots: list[int] = []
        for idx in candidates:
            root = uf.find(idx)
            if root not in roots:
                roots.append(root)
        for other in roots[1:]:
            first = uf.find(roots[0])
            other = uf.find(other)
            if first == other:
                continue
            a, b = clusters[first].strong, clusters[other].strong
            if a and b and a != b:
                continue          # two real requisitions: different postings
            root = uf.union(first, other)
            _absorb(clusters, root, {first, other})

    live = [c for c in clusters if c.members]
    collapsed = sum(len(c.members) for c in live) - len(live)
    _drop_ambiguous_keys(live)
    if unusable:
        log.debug("Dropped %d job(s) with no usable identity", unusable)
    return live, collapsed, unusable


def _absorb(clusters: list[Cluster], root: int, group: set[int]) -> None:
    """Fold every cluster in `group` into `clusters[root]`."""
    for other in group:
        if other == root or not (clusters[other].members or clusters[other].keys):
            continue
        clusters[root].keys |= clusters[other].keys
        clusters[root].members += clusters[other].members
        clusters[root].strong = clusters[root].strong or clusters[other].strong
        clusters[other].members = []
        clusters[other].keys = set()


def _drop_ambiguous_keys(clusters: list[Cluster]) -> None:
    """Remove any key held by more than one cluster.

    The veto keeps TikTok's six identically-titled San Jose roles apart during
    a run, but all six still generate the SAME tier-2 identity key. Storing
    that key with every one of them would hand the next run a key that matches
    all six, and `SeenStore.has_any` is a plain OR with no veto -- so five of
    them would look "already sent" forever. The silent loss would come back via
    the state file.

    A key that matches two different postings in one run has demonstrated it is
    not an identity. Dropping it costs nothing: each cluster keeps its own
    requisition id, and in the rare case a cluster is left with no keys at all
    it is simply re-emailed rather than silently dropped -- the safe direction.
    """
    counts: dict[str, int] = {}
    for group in clusters:
        for key in group.keys:
            counts[key] = counts.get(key, 0) + 1

    ambiguous = {k for k, n in counts.items() if n > 1}
    if not ambiguous:
        return

    stripped = 0
    for group in clusters:
        remaining = group.keys - ambiguous
        if remaining:
            group.keys = remaining
        else:
            # Nothing unique to key on. Keep what we have so the posting is at
            # least tracked; it may be re-sent once, which beats vanishing.
            stripped += 1
    log.debug("Dropped %d ambiguous key(s); %d cluster(s) had no unique key",
              len(ambiguous), stripped)


def keys_by_job(clusters: list[Cluster]) -> dict[int, set[str]]:
    """id(representative job) -> the full key union to store for it."""
    return {id(c.job): c.keys for c in clusters}
