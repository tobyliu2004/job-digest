"""Job Digest orchestration.

Run:
    python -m src.main                 # normal scheduled run (obeys timezone gate)
    python -m src.main --dry-run       # scrape + report, no email, no state write
    python -m src.main --force         # ignore the timezone gate
    python -m src.main --verify-links  # assert apply links are direct, not aggregator
    python -m src.main --test-email    # send a sample digest to prove SMTP works
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import canonical, canonical_legacy, corpus, dedupe, email_render, migrate, relevance
from .http import make_session
from .models import Job, SourceResult
from .scrapers import github_md, linkedin, simplify, simplify_repo
from .store import SeenStore

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"
STATE_PATH = ROOT / "state" / "seen.json"

# Hosts that mean "we failed to get a direct employer link".
AGGREGATOR_HOSTS = ("simplify.jobs", "github.com", "raw.githubusercontent.com")

log = logging.getLogger("digest")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config() -> dict:
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------

def collect(session, config: dict) -> list[SourceResult]:
    """Run every enabled source. A failure in one must not abort the others."""
    results: list[SourceResult] = []

    # The Pitt CSC feed runs BEFORE the Typesense index deliberately. Both
    # carry the same posting UUIDs, so whichever runs first wins the dedup --
    # and the feed's entries already hold the employer's real ATS URL, while
    # Typesense entries hold a click stub needing an extra redirect request
    # each to resolve. Feed-first means fewer requests and a direct Apply link
    # even when a redirect fails.
    feed_cfg = config.get("simplifyjobs_feed", {})
    if feed_cfg.get("enabled", True):
        name = feed_cfg.get("name", "SimplifyJobs")
        try:
            results.append(SourceResult(name, simplify_repo.fetch(session, feed_cfg)))
        except Exception as exc:
            log.error("%s failed: %s", name, exc)
            results.append(SourceResult(name, ok=False, error=str(exc)))

    simplify_cfg = config.get("simplify", {})
    if simplify_cfg.get("enabled", True):
        try:
            results.append(SourceResult("Simplify", simplify.fetch(session, simplify_cfg)))
        except Exception as exc:
            log.error("Simplify failed: %s", exc)
            results.append(SourceResult("Simplify", ok=False, error=str(exc)))

    gh_cfg = config.get("github_lists", {})
    if gh_cfg.get("enabled", True):
        for source in gh_cfg.get("sources", []):
            if not source.get("enabled", True):
                continue
            try:
                results.append(SourceResult(source["name"], github_md.fetch_one(session, source)))
            except Exception as exc:
                log.error("%s failed: %s", source["name"], exc)
                results.append(SourceResult(source["name"], ok=False, error=str(exc)))

    li_cfg = config.get("linkedin", {})
    if li_cfg.get("enabled", True):
        try:
            results.append(SourceResult("LinkedIn", linkedin.fetch(session, li_cfg)))
        except Exception as exc:
            log.error("LinkedIn failed: %s", exc)
            results.append(SourceResult("LinkedIn", ok=False, error=str(exc)))

    return results


# Whether a title names an internship. Both regexes live in canonical because
# the identity key needs the same intern/new-grad judgement to build its level
# field, and two copies would inevitably drift apart.
_NEW_GRAD_RE = canonical.NEW_GRAD_RE
_INTERN_RE = canonical.INTERN_RE


def is_internship(job: Job) -> bool:
    """Best-effort 'is this an internship?' for intern-only mode.

    This answers ONLY the seniority question. "Tax Intern" is an internship and
    is kept here; filtering it out for being off-domain is relevance.py's job.
    Conflating the two would make both untestable.

    LinkedIn's experience filter is noisy, so LinkedIn roles must explicitly say
    intern/co-op. Other sources are internship-curated or Simplify type=Internship,
    so we trust them unless the title clearly names a new-grad/full-time role.
    """
    title = job.title
    if _INTERN_RE.search(title):
        return True
    if job.source == "LinkedIn":
        return False  # no "intern" in title + noisy filter -> drop
    return not _NEW_GRAD_RE.search(title)


def filter_internships(jobs: list[Job]) -> tuple[list[Job], int]:
    kept = [j for j in jobs if is_internship(j)]
    return kept, len(jobs) - len(kept)


def filter_results(results: list[SourceResult], predicate) -> tuple[list[SourceResult], int]:
    """Apply a per-job predicate to every source, keeping the source structure.

    Filtering happens BEFORE dedupe, and the ordering is load-bearing. Run the
    other way round and a "SWE, New Grad" row can win the collapse against a
    "SWE Intern, Summer 2027" row from another source, and then be dropped by
    the intern filter -- so neither is emailed, and the cluster's keys are never
    stored, so the same thing happens again on every future run. The internship
    becomes permanently invisible.
    """
    dropped = 0
    out = []
    for result in results:
        kept = [j for j in result.jobs if predicate(j)]
        dropped += len(result.jobs) - len(kept)
        out.append(SourceResult(result.name, kept, result.ok, result.error))
    return out, dropped


# --------------------------------------------------------------------------
# Timezone gate
# --------------------------------------------------------------------------

def _seen_keys(group, include_legacy: bool) -> list[str]:
    """Keys to test a cluster against the store with.

    During the migration window the legacy formats are consulted too, covering
    postings that were not in the scrape when --migrate-keys ran (LinkedIn only
    shows the last 24h, and a source can be down that day). Legacy keys are
    never WRITTEN -- that would reintroduce the collisions they caused.
    """
    keys = sorted(group.keys)
    if include_legacy:
        keys += [k for m in group.members for k in canonical_legacy.legacy_keys_for(m)]
    return keys


def _legacy_window_open(config: dict, now: datetime) -> bool:
    until = (config.get("dedup") or {}).get("legacy_keys_until", "")
    return bool(until) and now.strftime("%Y-%m-%d") <= str(until)


def local_now(config: dict) -> datetime:
    # DIGEST_TZ (a GitHub Actions Variable / env var) overrides the config file,
    # so you can change timezone without editing code -- useful when you move.
    tz_name = os.environ.get("DIGEST_TZ") or config.get("timezone", "America/Los_Angeles")
    return datetime.now(ZoneInfo(tz_name))


def _schedule(config: dict) -> dict:
    return config.get("schedule") or {}


def send_targets(config: dict, now: datetime) -> dict[str, datetime]:
    """Today's exact local send times, e.g. {'AM': 07:00, 'PM': 19:00}."""
    am_hour, pm_hour = config.get("send_hours", [7, 19])
    return {
        "AM": now.replace(hour=am_hour, minute=0, second=0, microsecond=0),
        "PM": now.replace(hour=pm_hour, minute=0, second=0, microsecond=0),
    }


def window_slot(config: dict, now: datetime) -> str | None:
    """Which send window the current local time falls in: 'AM', 'PM', or None.

    A window OPENS before its send time -- `prep_minutes` early -- so the run
    can scrape, dedupe and render, then wait and hand the finished email to
    Gmail exactly on the hour. GitHub Actions cron drifts 10-50 minutes, so
    firing at the target and hoping is not good enough.

    Each window CLOSES at its deadline. Without one, the AM window ran until
    the PM window opened, so a morning whose triggers were all dropped could
    deliver a "morning" digest at 5pm. Past the deadline the slot is simply
    skipped and the evening digest sweeps up its jobs -- they are still "new
    since the last email", so nothing is lost.
    """
    sched = _schedule(config)
    prep = timedelta(minutes=sched.get("prep_minutes", 25))
    targets = send_targets(config, now)

    for slot, deadline_key in (("PM", "pm_deadline"), ("AM", "am_deadline")):
        target = targets[slot]
        if now < target - prep:
            continue
        deadline = _parse_local_time(sched.get(deadline_key), now)
        if deadline is not None and now > deadline:
            continue
        return slot
    return None


def _parse_local_time(value, now: datetime) -> datetime | None:
    """'23:30' -> today at 23:30 local. None when unset or malformed."""
    if not value:
        return None
    try:
        hour, _, minute = str(value).partition(":")
        return now.replace(hour=int(hour), minute=int(minute or 0),
                           second=59, microsecond=999999)
    except ValueError:
        log.warning("Bad schedule time %r - ignoring", value)
        return None


def hold_until_send_time(config: dict, now: datetime, slot: str | None,
                         *, enabled: bool = True, sleep=time.sleep) -> float:
    """Wait so the email lands on the target minute. Returns seconds slept.

    All the slow work is already done by the time this is called, so the wait
    is pure idle. If the trigger arrived late (cron dropped the early ones),
    the target is already past and this returns immediately.
    """
    if not (enabled and slot):
        return 0.0
    target = send_targets(config, now).get(slot)
    if target is None:
        return 0.0

    delay = (target - datetime.now(target.tzinfo)).total_seconds()
    cap = _schedule(config).get("max_hold_minutes", 45) * 60
    if delay <= 0:
        log.info("Past the %s target (%s) - sending now.", slot, target.strftime("%H:%M"))
        return 0.0
    if delay > cap:
        log.warning("Target %s is %.0f min away, over the %.0f min cap - sending now.",
                    target.strftime("%H:%M"), delay / 60, cap / 60)
        return 0.0

    log.info("Holding %.0f s so the %s digest lands at %s.",
             delay, slot, target.strftime("%H:%M %Z"))
    sleep(delay)
    return delay


def due_slot(config: dict, now: datetime, store) -> str | None:
    """Which digest is due right now and hasn't been sent yet today.

    GitHub Actions cron is UTC-only, ignores DST, and -- critically -- routinely
    delays or DROPS scheduled triggers (which is why a 7pm email can silently go
    missing). So instead of firing only at an exact hour, we send whenever a slot
    is *due and unsent*. Any run in the window sends the digest, so a missed
    on-the-hour trigger is simply covered by the next run. Being "new since last
    email", an evening digest also sweeps up anything a missed morning would have
    carried -- nothing is lost.
    """
    slot = window_slot(config, now)
    if slot is None:
        return None
    today = now.strftime("%Y-%m-%d")
    if slot == "PM" and store.last_pm_sent != today:
        return "PM"
    if slot == "AM" and store.last_am_sent != today:
        return "AM"
    return None


def mark_slot_sent(store, slot: str | None, now: datetime) -> None:
    """Record the slot the run actually DECIDED to send, not one re-derived
    from the clock -- a digest that started at 06:40 and sent at 07:00 must
    mark AM, and a forced run outside any window must mark nothing."""
    today = now.strftime("%Y-%m-%d")
    if slot == "AM":
        store.last_am_sent = today
    elif slot == "PM":
        store.last_pm_sent = today


def run_label(now: datetime) -> str:
    return now.strftime("%A, %b %-d %Y at %-I:%M %p %Z")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Twice-daily job digest")
    parser.add_argument("--dry-run", action="store_true", help="scrape and report; no email, no state write")
    parser.add_argument("--no-store", action="store_true", help="do not write state")
    parser.add_argument("--force", action="store_true", help="ignore the timezone gate")
    parser.add_argument("--verify-links", action="store_true", help="check apply links are direct")
    parser.add_argument("--test-email", action="store_true", help="send a sample digest")
    parser.add_argument("--catchup", action="store_true",
                        help="one-time: email ALL currently-open jobs as links, in batches, "
                             "without changing dedup state")
    parser.add_argument("--migrate-keys", action="store_true",
                        help="one-time: translate seen-state onto the fixed key "
                             "formats (combine with --dry-run to preview)")
    parser.add_argument("--dump-jobs", metavar="PATH",
                        help="write every scraped job to PATH as JSON (pre-dedup, "
                             "pre-filter) to refresh the test corpus")
    parser.add_argument("--audit-filter", action="store_true",
                        help="report exactly what the relevance filter would "
                             "drop or demote, and why; no email, no state write")
    parser.add_argument("--from", dest="from_file", metavar="PATH",
                        help="read jobs from a --dump-jobs file instead of scraping")
    parser.add_argument("--no-hold", action="store_true",
                        help="send immediately instead of waiting for the target time")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    config = load_config()

    smtp_user = os.environ.get("GMAIL_USER", "").strip()
    recipient = os.environ.get("RECIPIENT", "").strip()
    # Gmail shows the app password as "xxxx xxxx xxxx xxxx". Copy-paste often
    # brings the grouping spaces in as non-breaking spaces (\xa0), which SMTP's
    # ascii auth cannot encode. The 16 characters are the password; strip ALL
    # whitespace (regular and non-breaking) so a spaced paste just works.
    smtp_password = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())

    if args.test_email:
        return _send_test_email(smtp_user, smtp_password, recipient, config)

    now = local_now(config)
    store = SeenStore(STATE_PATH)
    first_run = store.was_empty

    # Scheduled runs only send when a digest is due and hasn't gone out yet.
    scheduled = not (args.dry_run or args.force or args.verify_links
                     or args.catchup or args.migrate_keys or args.audit_filter)
    slot = window_slot(config, now)
    if scheduled:
        slot = due_slot(config, now, store)
        if slot is None:
            log.info(
                "Nothing due at local %s (AM sent: %s, PM sent: %s) - exiting.",
                now.strftime("%H:%M %Z"),
                store.last_am_sent or "never", store.last_pm_sent or "never",
            )
            return 0
        log.info("Digest due: %s slot", slot)

    session = make_session()

    if args.from_file:
        log.info("Loading jobs from %s (no scrape)", args.from_file)
        results = corpus.load_results(args.from_file)
    else:
        log.info("Scraping sources...")
        results = collect(session, config)

    if args.dump_jobs:
        count = corpus.dump_results(results, args.dump_jobs)
        log.info("Dumped %d raw jobs to %s", count, args.dump_jobs)

    failures = [f"{r.name}: {r.error}" for r in results if not r.ok]

    # A source that returns ZERO postings without raising is the dangerous
    # case: nothing errors, the digest looks normal, and one of seven sources
    # has quietly stopped contributing. It has happened here already -- the
    # SimplifyJobs repo switched its README to HTML tables and the markdown
    # parser returned 0 jobs silently (see scrapers/simplify_repo.py). Every
    # healthy source returns 50+ postings, so zero means broken, not empty.
    for result in results:
        if result.ok and not result.jobs:
            log.warning("  %-28s returned 0 postings - likely broken", result.name)
            failures.append(f"{result.name}: returned 0 postings (source may have "
                            f"changed format)")

    for result in results:
        if result.ok:
            log.info("  %-28s %4d postings", result.name, len(result.jobs))
        else:
            log.warning("  %-28s FAILED", result.name)

    if args.migrate_keys:
        return _run_migration(results, failures, store, args.dry_run)

    total_raw = sum(len(r.jobs) for r in results)

    # Filters run per-source, BEFORE dedupe. See filter_results.
    judge = relevance.load(config)

    # The audit judges RAW scraped jobs -- running it after the filters would
    # show an empty DROP list, because the drops already happened.
    if args.audit_filter:
        return _run_audit(results, judge)

    results, rel_dropped = filter_results(results, judge.tag)
    if rel_dropped:
        log.info("Relevance: dropped %d off-domain posting(s)", rel_dropped)

    if config.get("intern_only"):
        results, dropped = filter_results(results, is_internship)
        log.info("Intern-only: dropped %d non-internship roles", dropped)

    clusters, collapsed, unusable = dedupe.cluster(results)
    unique = [c.job for c in clusters]
    log.info("Total scraped: %d | after cross-source dedup: %d (collapsed %d)",
             total_raw, len(unique), collapsed)

    if args.catchup:
        return _run_catchup(session, unique, failures, run_label(now),
                            smtp_user, smtp_password, recipient)

    legacy = _legacy_window_open(config, now)
    # Store the union of every cluster member's keys, not just the survivor's:
    # a member merged in transitively would otherwise go unrecorded and be
    # emailed again on every future run.
    keys_for_job = {id(c.job): sorted(c.keys) for c in clusters}
    new_jobs = [
        c.job for c in clusters
        if not store.has_any(_seen_keys(c, legacy))
    ]
    log.info("New since last email: %d", len(new_jobs))

    if args.verify_links:
        # Sample round-robin across sources so one source cannot dominate the
        # check. Resolve the Simplify entries first, otherwise they still hold
        # click URLs and would be reported as aggregator links.
        sample = _stratified_sample(new_jobs or unique, 20)
        simplify.resolve_apply_urls(session, [j for j in sample if j.extra.get("simplify_id")])
        return _verify_links(sample, session)

    # Resolve Simplify click-redirects only for jobs we will actually send.
    simplify_new = [j for j in new_jobs if j.extra.get("simplify_id")]
    if simplify_new and not first_run:
        log.info("Resolving %d Simplify apply links...", len(simplify_new))
        simplify.resolve_apply_urls(session, simplify_new)

    if args.dry_run:
        _print_dry_run(new_jobs, first_run)
        return 0

    label = run_label(now)

    if first_run:
        # Baseline instead of emailing the entire backlog.
        for group in clusters:
            store.add(sorted(group.keys))
        html_body, text_body = email_render.render_first_run(len(unique), label)
        messages = [DigestPart(f"[Job Digest] Initialised - tracking {len(unique)} postings",
                               html_body, text_body, [])]
        log.info("First run: baselining %d postings instead of sending them all", len(unique))
    else:
        messages = _digest_messages(new_jobs, failures, label,
                                    now.strftime("%b %-d"), slot)

    if not (smtp_user and smtp_password and recipient):
        log.error("Missing GMAIL_USER / GMAIL_APP_PASSWORD / RECIPIENT - cannot send.")
        return 1

    # Land the digest on the exact minute the user expects it, having done all
    # the slow work already. Only scheduled runs wait: someone who clicked "Run
    # workflow" or passed --force wants the email now, not in twenty minutes.
    hold_until_send_time(config, now, slot,
                         enabled=scheduled and not args.no_hold)

    def send(part):
        email_render.send_email(
            subject=part.subject, html_body=part.html, text_body=part.text,
            smtp_user=smtp_user, smtp_password=smtp_password, recipient=recipient,
        )

    if not deliver(messages, store, send, keys_for_job, persist=not args.no_store):
        # Leave the slot unsent so the next run finishes the job, and go red.
        return 1

    # Record that this slot's digest has gone out, so later runs in the same
    # window don't re-send.
    mark_slot_sent(store, slot, now)

    if not args.no_store:
        store.save()

    return 0


# Gmail truncates a message body over roughly 102KB, hiding everything past
# that behind a "[Message clipped] View entire message" link. A clipped digest
# silently withholds exactly the postings it exists to deliver -- and it fails
# quietly, since the email still looks complete until you scroll. So the send
# is split into parts before reaching that size.
#
# 92KB leaves headroom for the subject, headers and quoted-printable encoding,
# which inflate the wire size above len(html).
GMAIL_CLIP_BYTES = 92_000


@dataclass
class DigestPart:
    """One outgoing email, and the jobs it delivers.

    Carrying the jobs is what makes a partial failure recoverable: the send
    loop records a part's keys only once that part is actually accepted by
    Gmail, so a failure on part 3 of 4 re-sends part 3 onward and nothing else.
    """

    subject: str
    html: str
    text: str
    jobs: list[Job] = field(default_factory=list)


def _pack(ordered: list[Job], render, label: str = "") -> list[list[Job]]:
    """Split jobs into batches that each render under the clip threshold.

    Dividing by the average entry size is not enough: entries vary several-fold
    (a role with a long title, two locations and a salary against a bare one),
    so an average-sized split can still produce an oversized part when the large
    entries cluster. Each candidate batch is therefore measured, and any batch
    that still renders too big is halved until it fits.

    Measuring uses the same run label the real send will carry. Measuring with
    an empty one understates every part by the label's length, which is enough
    to let a batch measured at just under the limit go out just over it.
    """
    if not ordered:
        return [ordered]

    def size(batch):
        return len(render(batch, label, [])[0])

    full = size(ordered)
    n = len(ordered)

    # Work out how many parts are needed, then spread the jobs evenly across
    # them. Filling each part to the brim instead would leave a lopsided tail --
    # an 88KB email followed by a 7KB one -- which reads like something broke.
    parts = max(1, -(-full // GMAIL_CLIP_BYTES))

    for _ in range(8):  # a handful of widenings is always enough in practice
        per_part = -(-n // parts)
        batches = [ordered[i:i + per_part] for i in range(0, n, per_part)]
        if all(size(b) <= GMAIL_CLIP_BYTES for b in batches):
            return batches
        parts += 1

    # Fell through: split anything still oversized in half until it fits, so a
    # pathological batch can never produce a clipped email.
    result: list[list[Job]] = []
    pending = list(batches)
    while pending:
        batch = pending.pop(0)
        if len(batch) > 1 and size(batch) > GMAIL_CLIP_BYTES:
            mid = len(batch) // 2
            pending[:0] = [batch[:mid], batch[mid:]]
            continue
        result.append(batch)
    return result


def _digest_messages(jobs: list[Job], failures: list[str], label: str,
                     date_label: str, period: str) -> list[DigestPart]:
    """Render the digest as one email, or several if it would be clipped.

    Each part carries the jobs it contains, so the caller can record exactly
    what was delivered if a later part fails to send.

    The split point is measured from the actual rendered size rather than
    assumed from a job count, because entries vary a lot in length -- a role
    with a long title, two locations and a salary is several times the size of
    a bare one.
    """
    def render(batch, extra_label, batch_failures):
        direct = [j for j in batch if not j.indirect and j.relevance != relevance.MAYBE]
        indirect = [j for j in batch if j.indirect and j.relevance != relevance.MAYBE]
        maybe = [j for j in batch if j.relevance == relevance.MAYBE]
        return (email_render.render_html(direct, indirect, batch_failures, extra_label, maybe),
                email_render.render_text(direct, indirect, batch_failures, extra_label, maybe))

    n_maybe = sum(1 for j in jobs if j.relevance == relevance.MAYBE)
    count = len(jobs) - n_maybe
    plural = "s" if count != 1 else ""
    suffix = f" (+{n_maybe} maybe)" if n_maybe else ""
    html, text = render(jobs, label, failures)

    if len(html) <= GMAIL_CLIP_BYTES or not jobs:
        return [DigestPart(
            f"[Job Digest] {count} new posting{plural}{suffix} - {date_label} {period}",
            html, text, list(jobs))]

    # Sort before splitting so each part stays grouped by employer rather than
    # interleaving one company's roles across two emails, and so the Maybe
    # section lands together at the end rather than once per part.
    ordered = sorted(jobs, key=lambda j: (j.relevance == relevance.MAYBE, j.indirect,
                                          j.company.lower(), j.title.lower()))
    # Pack against a worst-case part label, since "part 9 of 9" is longer than
    # the plain label and the real send will carry it.
    batches = _pack(ordered, render, f"{label}  ·  part 88 of 88")
    total = len(batches)
    log.info("Digest is %dKB - splitting into %d emails so Gmail cannot clip it",
             len(html) // 1024, total)

    messages = []
    for idx, batch in enumerate(batches, 1):
        # Source failures belong on the first part only, not repeated on each.
        part_html, part_text = render(batch, f"{label}  ·  part {idx} of {total}",
                                      failures if idx == 1 else [])
        messages.append(DigestPart(
            f"[Job Digest] {count} new posting{plural}{suffix} - {date_label} "
            f"{period} ({idx}/{total})",
            part_html, part_text, list(batch),
        ))
    return messages


CATCHUP_BATCH = 150


def _run_catchup(session, unique, failures, label, smtp_user, smtp_password, recipient) -> int:
    """One-time: email every currently-open job as a real link, in batches.

    Deliberately does NOT touch state/seen.json. The backlog is already
    baselined there, so this send is a pure one-off and the twice-daily digest
    keeps working unchanged afterward -- it still only sends genuinely new jobs.
    """
    if not (smtp_user and smtp_password and recipient):
        log.error("Missing GMAIL_USER / GMAIL_APP_PASSWORD / RECIPIENT - cannot send.")
        return 1

    # Resolve every Simplify click-redirect to its real ATS URL before sending.
    simplify_jobs = [j for j in unique if j.extra.get("simplify_id")]
    if simplify_jobs:
        log.info("Resolving %d Simplify apply links...", len(simplify_jobs))
        simplify.resolve_apply_urls(session, simplify_jobs)

    # Stable ordering: employer, then role. Split into batches so no single
    # email is an unreadable wall of hundreds of links.
    ordered = sorted(unique, key=lambda j: (j.indirect, j.company.lower(), j.title.lower()))
    batches = [ordered[i:i + CATCHUP_BATCH] for i in range(0, len(ordered), CATCHUP_BATCH)]
    total_parts = len(batches)
    log.info("Catch-up: sending %d jobs across %d emails", len(ordered), total_parts)

    for idx, batch in enumerate(batches, 1):
        direct = [j for j in batch if not j.indirect]
        indirect = [j for j in batch if j.indirect]
        part_label = f"{label}  ·  Current openings, part {idx} of {total_parts}"
        # Only surface source failures on the first email, not repeated on each.
        batch_failures = failures if idx == 1 else []
        html_body = email_render.render_html(direct, indirect, batch_failures, part_label)
        text_body = email_render.render_text(direct, indirect, batch_failures, part_label)
        subject = (
            f"[Job Digest] Current openings ({idx}/{total_parts}) - {len(batch)} jobs"
        )
        email_render.send_email(
            subject=subject, html_body=html_body, text_body=text_body,
            smtp_user=smtp_user, smtp_password=smtp_password, recipient=recipient,
        )
        log.info("Sent catch-up email %d/%d (%d jobs)", idx, total_parts, len(batch))

    log.info("Catch-up complete. State unchanged; normal digests continue as usual.")
    return 0


def _keys_to_store(job: Job, keys_for_job: dict) -> list[str]:
    """What to record for a delivered job.

    The cluster's keys, plus the job's apply-URL key as it stands NOW -- a
    Simplify click stub is resolved to the employer's real URL after
    clustering, and both forms must be recorded or the resolved one looks new
    tomorrow.

    Only the URL key is added back, never the whole of keys_for(job). dedupe
    deliberately strips keys it proved ambiguous, and Boeing shows why: it runs
    one Data Analytics internship as two Workday requisitions (JR2026520976 on
    its intern board, JR2026520976-1 on its external one). Both produce the
    same tier-2 identity key, so that key cannot identify either of them.
    Re-adding it here would store a key matching both, and the moment one
    requisition drops out of the feed the other would match it and be
    suppressed for good -- the exact silent loss the stripping exists to
    prevent. A resolved URL key has no such problem: it belongs to one posting.
    """
    keys = set(keys_for_job.get(id(job), ()))
    url_key = canonical.canonical_url_key(job.apply_url)
    if url_key:
        keys.add(url_key)
    return sorted(keys)


def deliver(messages, store, send, keys_for_job: dict | None = None,
            *, persist: bool = True) -> bool:
    """Send each part, recording what actually arrived. True if all went out.

    State is written after EACH part, not once at the end. A digest split into
    three emails that failed on part two used to save nothing, so the next run
    re-sent all three -- a transient SMTP error produced a duplicate digest.
    Saving per part means a failure re-sends only what did not arrive.
    """
    keys_for_job = keys_for_job or {}
    for index, part in enumerate(messages, 1):
        try:
            send(part)
        except Exception:
            log.exception("Part %d of %d failed to send; %d part(s) already "
                          "recorded", index, len(messages), index - 1)
            return False
        for job in part.jobs:
            store.add(_keys_to_store(job, keys_for_job))
        if persist:
            store.save()
    return True


def _run_migration(results, failures, store, preview: bool) -> int:
    """--migrate-keys. Returns a process exit code."""
    if failures:
        # A source that failed contributes no jobs, so none of its stored
        # legacy keys can be translated -- and every one of its postings would
        # then look new and be re-emailed. Refuse rather than flood.
        log.error("Not migrating: %d source(s) failed (%s). Re-run when all "
                  "sources are healthy.", len(failures), "; ".join(failures))
        return 1

    report = migrate.run(store, results, apply=not preview)
    print(report.render())

    if preview:
        log.info("Preview only - state not written. Re-run without --dry-run to apply.")
        return 0

    store.save()
    log.info("Migration applied. The next digest sends %d one-time catch-up "
             "posting(s).", report.one_time_resends)
    return 0


def _run_audit(results, judge) -> int:
    """--audit-filter: show exactly what the relevance rules do, and why.

    Run this before turning the filter on. The SAVED section is the important
    one -- it lists jobs the allowlist rescued from an off-domain pattern, which
    is where a rule that is too broad shows itself.
    """
    if not judge.enabled:
        print("\nrelevance.enabled is false in config/sources.yaml - "
              "everything is kept. Showing what the rules WOULD do.\n")
        judge.enabled = True

    buckets: dict[tuple[str, str, str], list] = {}
    counts = {relevance.KEEP: 0, relevance.MAYBE: 0, relevance.DROP: 0}
    rescued = 0

    for result in results:
        for job in result.jobs:
            verdict = judge.judge(job)
            counts[verdict.action] += 1
            if verdict.rescued:
                rescued += 1
                buckets.setdefault(("SAVED", verdict.rule, verdict.pattern), []).append(job)
            elif verdict.action != relevance.KEEP:
                label = verdict.action.upper()
                buckets.setdefault((label, verdict.rule, verdict.pattern), []).append(job)

    for label in ("DROP", "MAYBE", "SAVED"):
        for (kind, rule, pattern), jobs in sorted(buckets.items()):
            if kind != label:
                continue
            print(f"\n{kind:5} {rule}  {pattern}")
            for job in sorted(jobs, key=lambda j: (j.company.lower(), j.title.lower())):
                print(f"      {job.company[:34]:34} {job.title[:56]:56} [{job.source}]")
                print(f"        {job.apply_url}")

    total = sum(counts.values())
    print(f"\nSUMMARY  {total} judged | keep {counts[relevance.KEEP]} | "
          f"maybe {counts[relevance.MAYBE]} | drop {counts[relevance.DROP]} | "
          f"saved-by-allowlist {rescued}")
    print("No email sent, no state written.")
    return 0


def _print_dry_run(new_jobs: list[Job], first_run: bool) -> None:
    if first_run:
        print("\n*** FIRST RUN: real run would baseline these, not email them ***")
    print(f"\n{'=' * 78}\n{len(new_jobs)} NEW JOBS\n{'=' * 78}")
    for job in sorted(new_jobs, key=lambda j: (j.source, j.company.lower())):
        flag = " [LinkedIn-only link]" if job.indirect else ""
        print(f"\n  {job.company} - {job.title}{flag}")
        meta = " | ".join(b for b in (job.location, job.season, job.salary, job.posted) if b)
        if meta:
            print(f"    {meta}")
        print(f"    {job.apply_url}")
        print(f"    via {job.source}")


def _stratified_sample(jobs: list[Job], limit: int) -> list[Job]:
    """Round-robin across sources so one source cannot dominate a sample."""
    by_source: dict[str, list[Job]] = {}
    for job in jobs:
        by_source.setdefault(job.source, []).append(job)

    sample: list[Job] = []
    index = 0
    while len(sample) < limit and any(index < len(v) for v in by_source.values()):
        for bucket in by_source.values():
            if index < len(bucket) and len(sample) < limit:
                sample.append(bucket[index])
        index += 1
    return sample


def _verify_links(jobs: list[Job], session) -> int:
    """Assert apply links are employer links, not links back to an aggregator."""
    sample = [j for j in jobs if not j.indirect]
    if not sample:
        print("No direct-apply jobs available to verify.")
        return 0

    print(f"\nVerifying {len(sample)} apply links...\n")
    bad = 0
    blocked = 0

    for job in sample:
        host_bad = any(h in job.apply_url for h in AGGREGATOR_HOSTS)
        try:
            resp = session.head(job.apply_url, timeout=20, allow_redirects=True)
            code = resp.status_code
            if code in (403, 405):  # some ATSes reject HEAD outright
                code = session.get(job.apply_url, timeout=20, stream=True).status_code
        except Exception as exc:
            code = f"ERR({type(exc).__name__})"

        # 403/429 means the employer's site is refusing *our* automated
        # request. The link is still correct and works in a browser, so this
        # is not a digest defect -- flagging it as one would be a false alarm.
        anti_bot = code in (403, 429)
        dead = isinstance(code, int) and code >= 400 and not anti_bot

        if host_bad:
            status, bad = "FAIL", bad + 1
        elif dead or not isinstance(code, int):
            status, bad = "FAIL", bad + 1
        elif anti_bot:
            status, blocked = "BOT?", blocked + 1
        else:
            status = "OK  "

        print(f"  {status} [{code}] {job.company[:24]:24} {job.apply_url[:74]}")
        if host_bad:
            print("       ^ points at an aggregator, not the employer -- real defect")
        elif anti_bot:
            print("       ^ employer blocks automated requests; link is fine in a browser")

    good = len(sample) - bad - blocked
    print(f"\n{good} direct+reachable, {blocked} bot-blocked (link still valid), {bad} broken.")
    return 1 if bad else 0


def _send_test_email(smtp_user: str, smtp_password: str, recipient: str, config: dict) -> int:
    if not (smtp_user and smtp_password and recipient):
        log.error("Set GMAIL_USER, GMAIL_APP_PASSWORD and RECIPIENT first.")
        return 1

    samples = [
        Job("Palantir Technologies", "Software Engineer Intern, Infrastructure",
            "https://jobs.lever.co/palantir/f221738b-e97c-4ce3-a12a-17ada2b855e4",
            "Simplify", "Palo Alto, CA", "Summer 2027", "$9,000/mo", "Jul 24"),
        Job("Jane Street", "Quantitative Trading Intern",
            "https://job-boards.greenhouse.io/janestreet/jobs/1234567",
            "sndsh404", "New York, NY", "Summer 2027", "", "Jul 23"),
    ]
    li = [Job("OpenAI", "Software Engineer, Web Layer",
              "https://www.linkedin.com/jobs/view/software-engineer-web-layer-at-openai-4426686037",
              "LinkedIn", "San Francisco, CA", indirect=True)]

    now = local_now(config)
    label = run_label(now)
    email_render.send_email(
        subject="[Job Digest] Test - 3 sample postings",
        html_body=email_render.render_html(samples, li, [], label),
        text_body=email_render.render_text(samples, li, [], label),
        smtp_user=smtp_user, smtp_password=smtp_password, recipient=recipient,
    )
    print(f"Test email sent to {recipient}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
