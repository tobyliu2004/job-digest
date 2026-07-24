"""Store of jobs you've marked as applied.

Deliberately a SEPARATE file from state/seen.json. seen.json is the digest's
dedup state and is rewritten by every run; corrupting it would cause missed or
repeated emails. The applied log is yours, hand-maintained, and must never be
at risk from an automated run -- so it lives on its own.

Keyed by the same canonical ATS id used for deduplication, so a job you marked
applied via one URL is recognised even if you later see it under a slightly
different URL.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class AppliedStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.jobs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Applied log %s unreadable (%s); starting empty", self.path, exc)
            return
        self.jobs = data.get("jobs", {}) or {}

    def status(self, keys: list[str]) -> dict | None:
        """Return the applied record if any of these keys was marked, else None."""
        for key in keys:
            if key in self.jobs:
                return self.jobs[key]
        return None

    def mark(self, keys: list[str], *, url: str, company: str = "", title: str = "") -> dict:
        """Record an application. Idempotent: keeps the first applied_at."""
        existing = self.status(keys)
        record = existing or {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "company": company,
            "title": title,
        }
        # Backfill metadata if it wasn't known before.
        record.setdefault("url", url)
        if company and not record.get("company"):
            record["company"] = company
        if title and not record.get("title"):
            record["title"] = title
        for key in keys:
            self.jobs[key] = record
        return record

    def unmark(self, keys: list[str]) -> bool:
        removed = False
        for key in list(self.jobs):
            if key in keys:
                del self.jobs[key]
                removed = True
        return removed

    def all_records(self) -> list[dict]:
        # One job is stored under several keys (url key + identity key). In
        # memory those share one object, but after a save/reload each key holds
        # a separate equal dict -- so dedupe by content (applied_at + url),
        # which uniquely identifies an application, not by object identity.
        seen = set()
        out = []
        for record in self.jobs.values():
            fingerprint = (record.get("applied_at", ""), record.get("url", ""))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(record)
        return sorted(out, key=lambda r: r.get("applied_at", ""), reverse=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": self.jobs}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
