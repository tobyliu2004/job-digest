"""Persistent seen-job state.

Stored as JSON and committed back to the repo by the GitHub Actions workflow,
so state survives across runs on ephemeral runners.

Three behaviours here exist to prevent specific, real failure modes:

* Atomic write (temp file + os.replace) -- an interrupted run cannot leave a
  truncated seen.json, which would look like "no jobs ever seen" and cause a
  mass re-send.
* First-run guard -- if the store is missing or empty, the caller seeds it and
  sends a short summary instead of ~1,600 links.
* Pruning -- entries older than the retention window are dropped so the file
  does not grow without bound. The window is far longer than any posting stays
  open, so pruning cannot resurrect a live job.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

RETENTION_DAYS = 180


class SeenStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.keys: dict[str, str] = {}
        self.last_run: str = ""
        self.was_empty: bool = True
        # Local dates (YYYY-MM-DD) of the last AM / PM digest actually sent.
        # Used to send a digest whenever it's *due and unsent*, rather than only
        # at an exact minute -- so a delayed or dropped cron trigger can't skip
        # a whole slot.
        self.last_am_sent: str = ""
        self.last_pm_sent: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("No state file at %s - treating as first run", self.path)
            return

        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            # Do NOT silently reset: that would mass-resend. Surface it, and
            # let the first-run guard send a summary rather than a flood.
            log.error("State file %s unreadable (%s); treating as first run", self.path, exc)
            return

        self.keys = data.get("keys", {}) or {}
        self.last_run = data.get("last_run", "")
        self.last_am_sent = data.get("last_am_sent", "")
        self.last_pm_sent = data.get("last_pm_sent", "")
        self.was_empty = not self.keys
        log.info("Loaded %d seen keys (last run: %s)", len(self.keys), self.last_run or "never")

    def has_any(self, keys: list[str]) -> bool:
        """True if ANY key was seen before. A job is new only if none match."""
        return any(k in self.keys for k in keys)

    def add(self, keys: list[str], when: str | None = None) -> None:
        stamp = when or datetime.now(timezone.utc).isoformat()
        for key in keys:
            self.keys.setdefault(key, stamp)

    def prune(self, days: int = RETENTION_DAYS) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        for key, stamp in list(self.keys.items()):
            try:
                if datetime.fromisoformat(stamp) < cutoff:
                    del self.keys[key]
                    removed += 1
            except (ValueError, TypeError):
                # Unparseable timestamp: re-stamp rather than drop, so we never
                # lose dedup coverage because of a bad write.
                self.keys[key] = datetime.now(timezone.utc).isoformat()
        if removed:
            log.info("Pruned %d entries older than %d days", removed, days)
        return removed

    def save(self) -> None:
        self.prune()
        payload = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_am_sent": self.last_am_sent,
            "last_pm_sent": self.last_pm_sent,
            "count": len(self.keys),
            "keys": self.keys,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic: write to a temp file in the same directory, then rename.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

        log.info("Saved %d keys to %s", len(self.keys), self.path)
