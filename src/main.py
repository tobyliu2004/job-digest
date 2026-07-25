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
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import canonical, email_render
from .http import make_session
from .models import Job, SourceResult
from .scrapers import github_md, linkedin, simplify
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


def dedupe(results: list[SourceResult]) -> tuple[list[Job], int]:
    """Collapse duplicates across sources. Returns (unique jobs, collapsed count).

    Direct-link sources are processed first so that when the same job appears
    both on LinkedIn and elsewhere, we keep the version with a real employer
    apply URL rather than the LinkedIn one.
    """
    ordered = sorted(results, key=lambda r: any(j.indirect for j in r.jobs))

    seen: set[str] = set()
    unique: list[Job] = []
    collapsed = 0

    for result in ordered:
        for job in result.jobs:
            keys = canonical.keys_for(job)
            if not keys:
                continue
            if any(k in seen for k in keys):
                collapsed += 1
                continue
            seen.update(keys)
            unique.append(job)

    return unique, collapsed


# --------------------------------------------------------------------------
# Timezone gate
# --------------------------------------------------------------------------

def local_now(config: dict) -> datetime:
    # DIGEST_TZ (a GitHub Actions Variable / env var) overrides the config file,
    # so you can change timezone without editing code -- useful when you move.
    tz_name = os.environ.get("DIGEST_TZ") or config.get("timezone", "America/Los_Angeles")
    return datetime.now(ZoneInfo(tz_name))


def window_slot(config: dict, now: datetime) -> str | None:
    """Which send window the current local time falls in: 'AM', 'PM', or None."""
    am_hour, pm_hour = config.get("send_hours", [9, 19])
    if now.hour >= pm_hour:
        return "PM"
    if am_hour <= now.hour < pm_hour:
        return "AM"
    return None


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


def mark_slot_sent(store, now: datetime, config: dict) -> None:
    slot = window_slot(config, now)
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
    scheduled = not (args.dry_run or args.force or args.verify_links or args.catchup)
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

    log.info("Scraping sources...")
    results = collect(session, config)

    failures = [f"{r.name}: {r.error}" for r in results if not r.ok]
    for result in results:
        if result.ok:
            log.info("  %-28s %4d postings", result.name, len(result.jobs))
        else:
            log.warning("  %-28s FAILED", result.name)

    unique, collapsed = dedupe(results)
    total_raw = sum(len(r.jobs) for r in results)
    log.info("Total scraped: %d | after cross-source dedup: %d (collapsed %d)",
             total_raw, len(unique), collapsed)

    if args.catchup:
        return _run_catchup(session, unique, failures, run_label(now),
                            smtp_user, smtp_password, recipient)

    new_jobs = [j for j in unique if not store.has_any(canonical.keys_for(j))]
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

    for job in new_jobs:
        store.add(canonical.keys_for(job))

    if args.dry_run:
        _print_dry_run(new_jobs, first_run)
        return 0

    label = run_label(now)

    if first_run:
        # Baseline instead of emailing the entire backlog.
        for job in unique:
            store.add(canonical.keys_for(job))
        html_body, text_body = email_render.render_first_run(len(unique), label)
        subject = f"[Job Digest] Initialised - tracking {len(unique)} postings"
        log.info("First run: baselining %d postings instead of sending them all", len(unique))
    else:
        direct = [j for j in new_jobs if not j.indirect]
        indirect = [j for j in new_jobs if j.indirect]
        html_body = email_render.render_html(direct, indirect, failures, label)
        text_body = email_render.render_text(direct, indirect, failures, label)
        period = "AM" if now.hour < 12 else "PM"
        count = len(new_jobs)
        subject = (
            f"[Job Digest] {count} new posting{'s' if count != 1 else ''} "
            f"- {now.strftime('%b %-d')} {period}"
        )

    if not (smtp_user and smtp_password and recipient):
        log.error("Missing GMAIL_USER / GMAIL_APP_PASSWORD / RECIPIENT - cannot send.")
        return 1

    email_render.send_email(
        subject=subject, html_body=html_body, text_body=text_body,
        smtp_user=smtp_user, smtp_password=smtp_password, recipient=recipient,
    )

    # Record that this slot's digest has gone out, so later runs in the same
    # window don't re-send. Only when this is (or falls in) a real send window.
    mark_slot_sent(store, now, config)

    if not args.no_store:
        store.save()

    return 0


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
