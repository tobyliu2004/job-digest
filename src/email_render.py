"""Digest email rendering (HTML + plaintext) and delivery over Gmail SMTP.

Layout uses tables and inline styles because that is what survives Gmail,
Apple Mail and Outlook. Dark-mode friendly colours, single column, large tap
targets for the Apply button on a phone.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from itertools import groupby

log = logging.getLogger(__name__)

_BG = "#f4f6f8"
_CARD = "#ffffff"
_TEXT = "#1a1d21"
_MUTED = "#5c6670"
_ACCENT = "#2557d6"
_BORDER = "#e3e8ee"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


@dataclass
class RoleEntry:
    """One role, and every posting of it. Usually exactly one posting."""

    job: object                  # the representative
    postings: list               # every posting, including the representative

    @property
    def locations(self) -> list[str]:
        seen = []
        for posting in self.postings:
            loc = _short_location(posting.location)
            if loc and loc not in seen:
                seen.append(loc)
        return seen


def _short_location(loc: str) -> str:
    return (loc or "").split(" +")[0].strip()


_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "tbd", "?"}


def _meaningful(value: str) -> bool:
    return (value or "").strip().lower() not in _PLACEHOLDERS


def group_same_role(jobs: list) -> list[RoleEntry]:
    """Collapse one role posted in several cities into a single entry.

    Employers routinely open one requisition per office: IBM's "Software
    Developer Spring Co-op 2027" was live in four cities on 2026-08-13, and
    Optiver's summer SWE internship in two. These are genuinely separate
    applications, so dedupe must NOT merge them -- each has its own URL and each
    is stored separately. But four near-identical rows read as a bug, so they
    are merged for display only, with one Apply button per city.

    Grouping is by (company, title, season, source): a same-titled role at the
    same employer for the same term. Different titles stay separate rows.
    """
    grouped: dict[tuple, RoleEntry] = {}
    order: list[tuple] = []
    for job in jobs:
        key = (job.company.strip().lower(), job.title.strip().lower(),
               (job.season or "").strip().lower(), job.source)
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = RoleEntry(job=job, postings=[job])
            order.append(key)
        else:
            entry.postings.append(job)
    return [grouped[k] for k in order]


def _apply_button(url: str, label: str = "Apply&nbsp;&rarr;") -> str:
    return f"""<a href="{_esc(url)}"
             style="display:inline-block;background:{_ACCENT};color:#ffffff;text-decoration:none;
                    font:600 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                    padding:9px 18px;border-radius:6px;margin:0 6px 6px 0;">{label}</a>"""


def _job_html(entry) -> str:
    """One row. `entry` is a group of postings that are the same role."""
    job = entry.job
    locations = entry.locations
    # Some sources write "-" or "N/A" for an unknown field; rendering those
    # leaves a stray "· -" hanging off the end of the metadata line.
    meta_bits = [b for b in (", ".join(locations) if locations else job.location,
                             job.season, job.salary, job.posted) if _meaningful(b)]
    meta = " &nbsp;·&nbsp; ".join(_esc(b) for b in meta_bits)

    if len(entry.postings) > 1:
        # One role open in several cities: one row, one button per city, rather
        # than N near-identical rows that read as duplicates.
        buttons = "".join(
            _apply_button(p.apply_url, _esc(_short_location(p.location)) or "Apply")
            for p in entry.postings
        )
    else:
        buttons = _apply_button(job.apply_url)

    note = ""
    if job.relevance_rule:
        note = (f'<div style="font:400 11px/1.5 -apple-system,sans-serif;color:{_MUTED};'
                f'margin-top:3px;">filtered: {_esc(job.relevance_rule)}</div>')

    return f"""
    <tr>
      <td style="padding:14px 18px;border-bottom:1px solid {_BORDER};">
        <div style="font:600 15px/1.35 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{_TEXT};">
          {_esc(job.title)}
        </div>
        <div style="font:500 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{_ACCENT};margin-top:2px;">
          {_esc(job.company)}
        </div>
        <div style="font:400 12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{_MUTED};margin-top:3px;">
          {meta}
        </div>{note}
        <div style="margin-top:9px;">
          {buttons}
          <span style="font:400 11px/1 -apple-system,sans-serif;color:{_MUTED};margin-left:4px;">
            {_esc(job.source)}
          </span>
        </div>
      </td>
    </tr>"""


def _section_html(title: str, subtitle: str, jobs: list) -> str:
    if not jobs:
        return ""

    entries = group_same_role(jobs)
    rows = []
    # Group by company so multiple roles at one employer read together.
    for company, group in groupby(
        sorted(entries, key=lambda e: (e.job.company.lower(), e.job.title.lower())),
        key=lambda e: e.job.company,
    ):
        rows.extend(_job_html(e) for e in group)

    sub = (
        f'<div style="font:400 12px/1.5 -apple-system,sans-serif;color:{_MUTED};margin-top:4px;">{_esc(subtitle)}</div>'
        if subtitle
        else ""
    )

    return f"""
  <div style="margin:26px 0 8px;">
    <div style="font:700 16px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{_TEXT};">
      {_esc(title)} <span style="color:{_MUTED};font-weight:500;">({len(jobs)})</span>
    </div>{sub}
  </div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:{_CARD};border:1px solid {_BORDER};border-radius:10px;overflow:hidden;">
    {"".join(rows)}
  </table>"""


def render_html(direct_jobs: list, linkedin_jobs: list, failures: list[str],
                run_label: str, maybe_jobs: list = ()) -> str:
    maybe_jobs = list(maybe_jobs)
    total = len(direct_jobs) + len(linkedin_jobs)

    if total == 0 and not maybe_jobs:
        body = f"""
  <div style="background:{_CARD};border:1px solid {_BORDER};border-radius:10px;padding:26px;text-align:center;">
    <div style="font:600 15px/1.4 -apple-system,sans-serif;color:{_TEXT};">No new postings this run.</div>
    <div style="font:400 13px/1.6 -apple-system,sans-serif;color:{_MUTED};margin-top:6px;">
      All sources were checked and everything found had already been sent.
    </div>
  </div>"""
    else:
        body = _section_html(
            "Direct Apply", "Links go straight to the employer's application page.", direct_jobs
        ) + _section_html(
            "LinkedIn",
            "LinkedIn hides employer apply links from logged-out visitors, so these open on LinkedIn.",
            linkedin_jobs,
        )

    # Demoted rather than deleted, so a filter rule that is too aggressive is
    # visible here instead of silently costing you a job.
    body += _section_html(
        "Maybe",
        "Filtered as off-domain or restricted. Tune the relevance rules in "
        "config/sources.yaml if anything here belongs above.",
        maybe_jobs,
    )

    warn = ""
    if failures:
        items = "".join(f"<li>{_esc(f)}</li>" for f in failures)
        warn = f"""
  <div style="background:#fff8e6;border:1px solid #f0d999;border-radius:8px;padding:12px 16px;margin-top:22px;">
    <div style="font:600 13px/1.4 -apple-system,sans-serif;color:#7a5c00;">Some sources were unavailable</div>
    <ul style="font:400 12px/1.6 -apple-system,sans-serif;color:#7a5c00;margin:6px 0 0;padding-left:18px;">{items}</ul>
    <div style="font:400 12px/1.6 -apple-system,sans-serif;color:#7a5c00;margin-top:6px;">
      Their postings were not marked as seen and will appear in the next successful run.
    </div>
  </div>"""

    return f"""<div style="background:{_BG};padding:22px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="max-width:660px;margin:0 auto;">
  <tr><td>
    <div style="font:700 21px/1.25 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:{_TEXT};">
      {total} new {"posting" if total == 1 else "postings"}
    </div>
    <div style="font:400 13px/1.5 -apple-system,sans-serif;color:{_MUTED};margin-top:3px;">{_esc(run_label)}</div>
    {body}
    {warn}
    <div style="font:400 11px/1.6 -apple-system,sans-serif;color:{_MUTED};margin-top:26px;text-align:center;">
      Job Digest &middot; 7 sources &middot; deduplicated against every previous email
    </div>
  </td></tr>
</table>
</div>"""


def render_text(direct_jobs: list, linkedin_jobs: list, failures: list[str],
                run_label: str, maybe_jobs: list = ()) -> str:
    maybe_jobs = list(maybe_jobs)
    lines = [f"{len(direct_jobs) + len(linkedin_jobs)} new postings", run_label, ""]

    def block(title: str, jobs: list):
        if not jobs:
            return
        entries = group_same_role(jobs)
        lines.append(f"== {title} ({len(jobs)}) ==")
        lines.append("")
        for entry in sorted(entries, key=lambda e: (e.job.company.lower(), e.job.title.lower())):
            job = entry.job
            where = ", ".join(entry.locations) or job.location
            meta = " | ".join(b for b in (where, job.season, job.salary) if _meaningful(b))
            lines.append(f"{job.company} - {job.title}")
            if meta:
                lines.append(f"  {meta}")
            if job.relevance_rule:
                lines.append(f"  filtered: {job.relevance_rule}")
            for posting in entry.postings:
                prefix = f"  [{_short_location(posting.location)}] " if len(entry.postings) > 1 else "  "
                lines.append(f"{prefix}{posting.apply_url}")
            lines.append("")

    block("Direct Apply", direct_jobs)
    block("LinkedIn (opens on LinkedIn)", linkedin_jobs)
    block("Maybe (off-domain or restricted -- review)", maybe_jobs)

    if not direct_jobs and not linkedin_jobs and not maybe_jobs:
        lines.append("No new postings this run.")
        lines.append("")

    if failures:
        lines.append("Unavailable sources (will retry next run):")
        lines.extend(f"  - {f}" for f in failures)

    return "\n".join(lines)


def render_first_run(tracked: int, run_label: str) -> tuple[str, str]:
    """First-run summary. Avoids dumping the entire backlog as one email."""
    text = (
        f"Job Digest initialised.\n\n"
        f"Baselined {tracked} existing postings across all sources so they are not "
        f"re-sent as 'new'.\n\nFrom the next run onward you will only receive "
        f"genuinely new postings, at 7:00 AM and 7:00 PM.\n\n{run_label}\n"
    )
    html_body = f"""<div style="background:{_BG};padding:22px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:660px;margin:0 auto;">
  <tr><td style="background:{_CARD};border:1px solid {_BORDER};border-radius:10px;padding:26px;">
    <div style="font:700 19px/1.3 -apple-system,sans-serif;color:{_TEXT};">Job Digest initialised</div>
    <div style="font:400 14px/1.6 -apple-system,sans-serif;color:{_MUTED};margin-top:10px;">
      Baselined <strong style="color:{_TEXT};">{tracked}</strong> existing postings so they are not re-sent as new.
    </div>
    <div style="font:400 14px/1.6 -apple-system,sans-serif;color:{_MUTED};margin-top:10px;">
      From the next run onward you will only receive genuinely new postings, at 7:00&nbsp;AM and 7:00&nbsp;PM.
    </div>
    <div style="font:400 11px/1.6 -apple-system,sans-serif;color:{_MUTED};margin-top:20px;">{_esc(run_label)}</div>
  </td></tr>
</table>
</div>"""
    return html_body, text


def send_email(
    *, subject: str, html_body: str, text_body: str,
    smtp_user: str, smtp_password: str, recipient: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    log.info("Email sent to %s: %s", recipient, subject)
