"""GitHub markdown job-list scraper.

The three repos publish the same broad table shape but differ in column names
and link style, so the parser maps columns by *header name* rather than by
position:

    vanshb03    | Company | Role     | Location | Application/Link | Date Posted |
    sndsh404    | Company | Role     | Location | Apply            | Added       |
    speedyapply | Company | Position | Location | Salary | Posting | Age        |

Link style also differs: vanshb03 and speedyapply wrap an <img> in an <a href>,
sndsh404 uses plain `[apply](url)` markdown. Both are handled.

Two details found in the live data that a naive parser gets wrong:

* vanshb03 uses `↳` as the company cell to mean "same company as the row
  above". Without carry-forward those rows lose their employer entirely --
  and Palantir alone had 7 consecutive `↳` rows.
* speedyapply's company cell is `<a href="https://www.google.com">Google</a>`,
  where the href is the *corporate site*, not the job. Taking the first href
  in the row would produce a useless link, so company is read as text and the
  apply URL is read only from the apply column.
"""

from __future__ import annotations

import html
import logging
import re

from ..http import get_text
from ..models import Job

log = logging.getLogger(__name__)

RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

# Column header -> logical field. Matched lowercase, substring-wise.
_HEADER_MAP = {
    "company": "company",
    "org": "company",
    "role": "title",
    "position": "title",
    "opportunity": "title",
    "location": "location",
    "application/link": "apply",
    "application": "apply",
    "apply": "apply",
    "posting": "apply",
    "link": "apply",
    "salary": "salary",
    "date posted": "posted",
    "added": "posted",
    "age": "posted",
    "date": "posted",
}

# Links that are decoration, not job postings.
_JUNK_LINK = re.compile(
    r"(imgur\.com|shields\.io|discord\.(gg|com)|github\.com|githubusercontent\.com"
    r"|speedyapply\.com|linkedin\.com/company|twitter\.com|\.(png|jpg|jpeg|svg|gif)$)",
    re.I,
)

_HREF = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
# Same shape, but capturing the label so a link can render as its text.
_MD_LINK_LABEL = re.compile(r"\[([^\]]*)\]\((?:https?://[^)\s]+)\)")
_TAG = re.compile(r"<[^>]+>")
_CARRY = "↳"
_CLOSED_MARKERS = ("🔒",)


def _cell_text(cell: str) -> str:
    """Visible text of a table cell, with HTML tags and entities removed.

    Markdown links are reduced to their label: a cell holding both the role and
    its link -- `[Software Undergrad Engineering Internship](https://...)` --
    must read as the role, not as raw markup, in the email.
    """
    text = _MD_LINK_LABEL.sub(r"\1", cell)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _cell_link(cell: str) -> str:
    """First real job URL in a cell, from either an <a href> or markdown link."""
    for match in _HREF.finditer(cell):
        url = html.unescape(match.group(1))
        if not _JUNK_LINK.search(url):
            return url
    for match in _MD_LINK.finditer(cell):
        url = html.unescape(match.group(1))
        if not _JUNK_LINK.search(url):
            return url
    return ""


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def parse_markdown(text: str, source_name: str) -> list[Job]:
    jobs: list[Job] = []
    columns: dict[str, int] | None = None
    last_company = ""
    skipped_closed = 0

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            # A non-table line ends the current table; the next table brings
            # its own header. Repos have several tables (FAANG/Quant/Other).
            columns = None
            continue

        cells = _split_row(stripped)

        if _is_separator(cells):
            continue

        # Header row? A table names its employer column "Company" or "Org".
        # sndsh404 keeps a second table headed `| org | opportunity | type |
        # deadline |`; recognising only "company" skipped it entirely, losing
        # 31 real postings including an Apple SWE internship.
        lowered = [_cell_text(c).lower() for c in cells]
        if "company" in lowered or "org" in lowered:
            columns = {}
            for idx, name in enumerate(lowered):
                for key, field in _HEADER_MAP.items():
                    if key in name and field not in columns:
                        columns[field] = idx
                        break
            # Some tables put the role and its link in one cell -- "| Apple |
            # [Software Undergrad Engineering Internship](https://...) |" --
            # rather than in a separate apply column. Read the link from the
            # title cell when there is no dedicated one.
            if "apply" not in columns and "title" in columns:
                columns["apply"] = columns["title"]
            last_company = ""
            continue

        if not columns or "apply" not in columns or "company" not in columns:
            continue

        def get(field: str) -> str:
            idx = columns.get(field)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        if any(marker in stripped for marker in _CLOSED_MARKERS):
            skipped_closed += 1
            continue

        company_raw = _cell_text(get("company"))
        # `↳` means "same company as the row above".
        if company_raw.startswith(_CARRY) or not company_raw:
            company = last_company
        else:
            company = company_raw
            last_company = company

        title = _cell_text(get("title"))
        apply_url = _cell_link(get("apply"))

        if not company or not title or not apply_url:
            continue

        jobs.append(
            Job(
                company=company,
                title=title,
                apply_url=apply_url,
                source=source_name,
                location=_cell_text(get("location")),
                salary=_cell_text(get("salary")),
                posted=_cell_text(get("posted")),
            )
        )

    if skipped_closed:
        log.info("%s: skipped %d closed postings", source_name, skipped_closed)
    return jobs


def fetch_one(session, source: dict) -> list[Job]:
    url = RAW_URL.format(repo=source["repo"], branch=source["branch"], path=source["path"])
    text = get_text(session, url)
    jobs = parse_markdown(text, source["name"])
    log.info("%s: %d postings", source["name"], len(jobs))
    return jobs
