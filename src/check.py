"""'Have I already seen / applied to this job?' checker.

    python -m src.check <url>                 # status of one job
    python -m src.check --applied <url>       # mark a job as applied
    python -m src.check --applied <url> --company "Stripe" --title "SWE Intern"
    python -m src.check --unapply <url>       # undo a mark
    python -m src.check --list                # list everything you've applied to

Status combines two facts:
  * was this URL ever emailed to you by the digest?  (state/seen.json)
  * have you marked it applied?                       (state/applied.json)

Matching uses the same canonical ATS id as the digest, so a job is recognised
even when the URL differs slightly (tracking params, path slugs, Workday host
shards) from the one you originally received.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import canonical
from .applied import AppliedStore
from .store import SeenStore

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "state" / "seen.json"
APPLIED_PATH = ROOT / "state" / "applied.json"

GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
)


def _keys_for_url(url: str, company: str = "", title: str = "",
                  location: str = "", season: str = "") -> list[str]:
    keys = [canonical.canonical_url_key(url)]
    ident = canonical.identity_key(company, title, location, season)
    if ident:
        keys.append(ident)
    return [k for k in keys if k]


def _identity_prefix(company: str, title: str) -> str:
    """The leading, location-free part of an identity key.

    Stored identity keys carry the posting's location and season, but you
    almost never type those at the command line -- so an exact --company
    /--title lookup matched nothing at all. Comparing on the prefix instead
    lets `--company Stripe --title "SWE Intern"` find the stored
    `id:stripe|swe|new york ny|intern|su2027`.
    """
    c = canonical.normalize_company(company)
    t = canonical.normalize_title(title)
    return f"id:{c}|{t}|" if c and t else ""


def _find(stored: dict, keys: list[str], company: str, title: str) -> str:
    """The stored key matching this job: exact if possible, else by prefix."""
    for key in keys:
        if key in stored:
            return key
    prefix = _identity_prefix(company, title)
    if prefix:
        return next((k for k in sorted(stored) if k.startswith(prefix)), "")
    return ""


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso or "unknown date"


def check(url: str, company: str, title: str,
          location: str = "", season: str = "") -> int:
    keys = _keys_for_url(url, company, title, location, season)
    seen = SeenStore(SEEN_PATH)
    applied = AppliedStore(APPLIED_PATH)

    applied_key = _find(applied.jobs, keys, company, title)
    applied_rec = applied.jobs.get(applied_key) if applied_key else None
    matched = _find(seen.keys, keys, company, title)
    was_emailed = bool(matched)

    print()
    if applied_rec:
        print(f"  {RED}{BOLD}✗ ALREADY APPLIED{RESET} on {_fmt_date(applied_rec.get('applied_at',''))}")
        if applied_rec.get("company") or applied_rec.get("title"):
            print(f"    {DIM}{applied_rec.get('company','')} — {applied_rec.get('title','')}{RESET}")
        print(f"    {DIM}Don't apply again.{RESET}")
        result = 2
    elif was_emailed:
        when = seen.keys.get(matched, "")
        print(f"  {YELLOW}● Seen before{RESET} — the digest emailed you this on {_fmt_date(when)}.")
        print(f"    {DIM}You have not marked it applied. Mark it with:{RESET}")
        print(f"    {DIM}python -m src.check --applied \"{url}\"{RESET}")
        result = 1
    else:
        print(f"  {GREEN}✓ New to you{RESET} — never emailed, never applied.")
        result = 0

    print(f"    {DIM}match key: {canonical.canonical_url_key(url)}{RESET}\n")
    return result


def mark_applied(url: str, company: str, title: str,
                 location: str = "", season: str = "") -> int:
    keys = _keys_for_url(url, company, title, location, season)
    applied = AppliedStore(APPLIED_PATH)
    existing = applied.status(keys)
    record = applied.mark(keys, url=url, company=company, title=title)
    applied.save()

    verb = "Already recorded" if existing else "Marked applied"
    print(f"\n  {GREEN}✓ {verb}{RESET} on {_fmt_date(record.get('applied_at',''))}")
    if company or title:
        print(f"    {DIM}{company} — {title}{RESET}")
    print()
    return 0


def unapply(url: str, company: str, title: str,
            location: str = "", season: str = "") -> int:
    keys = _keys_for_url(url, company, title, location, season)
    applied = AppliedStore(APPLIED_PATH)
    if applied.unmark(keys):
        applied.save()
        print(f"\n  {GREEN}✓ Removed{RESET} from your applied list.\n")
        return 0
    print(f"\n  {DIM}That job wasn't in your applied list.{RESET}\n")
    return 1


def list_applied() -> int:
    applied = AppliedStore(APPLIED_PATH)
    records = applied.all_records()
    if not records:
        print(f"\n  {DIM}You haven't marked any jobs as applied yet.{RESET}\n")
        return 0

    print(f"\n  {BOLD}{len(records)} job{'s' if len(records) != 1 else ''} applied to:{RESET}\n")
    for r in records:
        label = " — ".join(x for x in (r.get("company"), r.get("title")) if x) or r.get("url", "")
        print(f"  {GREEN}✓{RESET} {_fmt_date(r.get('applied_at','')):14} {label}")
        print(f"    {DIM}{r.get('url','')}{RESET}")
    print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m src.check",
        description="Check whether you've seen or applied to a job.",
    )
    p.add_argument("url", nargs="?", help="the job application URL")
    p.add_argument("--applied", action="store_true", help="mark this URL as applied")
    p.add_argument("--unapply", action="store_true", help="undo an applied mark")
    p.add_argument("--list", action="store_true", help="list everything you've applied to")
    p.add_argument("--company", default="", help="optional, improves matching")
    p.add_argument("--title", default="", help="optional, improves matching")
    p.add_argument("--location", default="", help="optional, improves matching")
    p.add_argument("--season", default="", help="optional, e.g. \"Summer 2027\"")
    args = p.parse_args()

    if args.list:
        return list_applied()

    if not args.url:
        p.error("a job URL is required (or use --list)")

    if args.applied:
        return mark_applied(args.url, args.company, args.title,
                        args.location, args.season)
    if args.unapply:
        return unapply(args.url, args.company, args.title,
                   args.location, args.season)
    return check(args.url, args.company, args.title,
                 args.location, args.season)


if __name__ == "__main__":
    sys.exit(main())
