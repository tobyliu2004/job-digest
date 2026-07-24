"""Shared HTTP layer.

Every outbound request in this project goes through here. Two reasons:

1. Simplify's Typesense host sits behind Cloudflare, which bans default
   Python client signatures with `HTTP 403 / error code: 1010`. A browser
   User-Agent plus an Origin header is what makes it succeed. Bare
   requests/urllib calls WILL fail against that host.
2. Centralised retry/backoff so a transient 429 or 5xx doesn't lose a source.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BACKOFF_BASE = 1.5


class FetchError(RuntimeError):
    """Raised when a URL could not be fetched after all retries."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    allow_redirects: bool = True,
    timeout: int = 30,
    **kwargs,
) -> requests.Response:
    """Perform a request with retry/backoff on transient failures.

    Non-retryable statuses (e.g. 404) are returned to the caller as-is so the
    caller can decide; only network errors and RETRY_STATUS trigger a retry.
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.request(
                method, url, timeout=timeout, allow_redirects=allow_redirects, **kwargs
            )
        except requests.RequestException as exc:
            last_exc = exc
            log.warning("%s %s failed (attempt %d/%d): %s", method, url, attempt, MAX_ATTEMPTS, exc)
        else:
            if resp.status_code not in RETRY_STATUS:
                return resp
            last_exc = FetchError(f"HTTP {resp.status_code} from {url}")
            log.warning(
                "%s %s -> HTTP %d (attempt %d/%d)",
                method, url, resp.status_code, attempt, MAX_ATTEMPTS,
            )

        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_BASE ** attempt)

    raise FetchError(f"{method} {url} failed after {MAX_ATTEMPTS} attempts") from last_exc


def get_json(session: requests.Session, url: str, **kwargs) -> dict:
    resp = request(session, "GET", url, **kwargs)
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
    return resp.json()


def get_text(session: requests.Session, url: str, **kwargs) -> str:
    resp = request(session, "GET", url, **kwargs)
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code} from {url}")
    return resp.text


def resolve_redirect(session: requests.Session, url: str) -> str | None:
    """Return the Location header of a single-hop redirect, without following it.

    Used for Simplify's /jobs/click/{id} endpoint, which 307s to the real ATS
    URL. We deliberately do not follow into the ATS: it is slower and far more
    likely to rate-limit or bot-block us.
    """
    try:
        resp = request(session, "GET", url, allow_redirects=False, timeout=20)
    except FetchError as exc:
        log.warning("Could not resolve redirect for %s: %s", url, exc)
        return None

    if resp.status_code in (301, 302, 303, 307, 308):
        return resp.headers.get("Location")

    log.warning("Expected redirect from %s, got HTTP %d", url, resp.status_code)
    return None
