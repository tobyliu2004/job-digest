"""Semantic relevance: ask a model whether a title is a software job.

WHY THIS EXISTS

relevance.py answers the same question with regexes, and regexes enumerate.
The 2026-08-14 digests showed both failure directions in one day: the morning
led with nine ASIC/FPGA/silicon roles, and the evening carried BP's
"Geoscience Intern - Geoscientist" -- which slipped past a rule listing
"geology" and "geophysics" because it shares no whole word with either. Every
fix is one more word, and the list of words that are not software is infinite.

A pure allowlist doesn't solve it either. Measured on the 1,033 unique titles
in tests/fixtures/live_jobs.json, keeping only titles that match the software
vocabulary would demote 157 -- including Etched "Inference Architecture
Intern", "Forward Deployed Engineer Intern" and D. E. Shaw "Systems
Engineering Intern". That trades hardware noise for lost software jobs.

THIS IS A DRY-RUN MODULE. Nothing in the digest path imports it. Compare its
verdicts against the shipped rules with:

    python -m src.main --audit-classifier --from tests/fixtures/live_jobs.json

WHAT IT IS NOT

Not a replacement for relevance.py's block_* rules. Those answer "is this a
job posting at all?" (Summer of Code, talent pools, aggregator reposts) -- a
question about provenance, not subject matter, and one that regexes answer
exactly and for free. If this is ever adopted, it replaces only the
maybe_titles/allow_titles guess, and relevance.py stays as the fallback for
when the API is unreachable.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Haiku 4.5, chosen for this job in the 2026-08-14 planning conversation:
# the inputs are one-line titles and the output is one word each, so the
# cheapest tier is the right tier. Change this one line to re-run the
# comparison on a larger model.
MODEL = "claude-haiku-4-5"

# Titles per request. The cost is dominated by the system prompt, so batching
# amortises it: 50 titles share one copy of the instructions instead of 50.
BATCH_SIZE = 50

# Deliberately no cache_control. Haiku 4.5 needs a 4,096-token prefix before
# anything caches, and the prompt below is nowhere near that -- a breakpoint
# would silently do nothing rather than fail, which is worse than omitting it.

SYSTEM = """\
You classify internship job titles for a software-engineering student's job \
digest. You will be given a numbered list of titles. Return a verdict for every \
title, using its index.

Verdicts:

  software  -- the person writes code as the core of the job. Includes
               backend, frontend, full-stack, mobile, web, infrastructure,
               platform, DevOps/SRE, security engineering, embedded and
               firmware, compilers, graphics, gameplay, robotics and autonomy
               software, ML/AI engineering and research, data engineering,
               data science, and quantitative development, research, or
               trading.

  other     -- everything else. Includes hardware and chip design (ASIC, FPGA,
               silicon, VLSI, analog, PCB, RF, electrical, electronics), other
               engineering disciplines (mechanical, civil, thermal, aerospace,
               materials, industrial, manufacturing), earth and life sciences,
               product management, business, finance, operations, consulting,
               marketing, sales, HR, legal, and accounting.

Rules:

- Judge the actual work, not the vocabulary. "Applied AI Engineer Intern - AI
  Hardware" builds AI systems, so it is software. "Hardware Engineer Intern -
  FPGA" designs circuits, so it is other.
- A title that pairs a non-software domain with software work is software:
  "Software Engineer Intern, Chip Design Tools" and "Geospatial Software
  Engineer" both write code.
- Embedded and firmware are software. They write code that happens to run on
  hardware.
- Quantitative finance roles are software when they build models or systems
  ("Quantitative Developer", "Quantitative Researcher"). A pure markets role
  ("Sales and Trading Summer Analyst") is other.
- When a title is genuinely ambiguous, answer software. A wrong "software"
  costs one extra row in an email; a wrong "other" hides a job.

Keep each reason under 8 words."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["software", "other"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


class ClassifierUnavailable(RuntimeError):
    """No SDK, no credentials, or the API could not be reached.

    Callers treat this as "fall back to the regex rules", never as a reason to
    drop a posting: an unreachable API must not silently shrink the digest.
    """


def _client():
    try:
        import anthropic
    except ModuleNotFoundError as exc:
        raise ClassifierUnavailable(
            "the anthropic SDK is not installed - pip install anthropic"
        ) from exc

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        # A bare client can still authenticate from an `ant auth login` profile,
        # so this is a warning rather than a hard failure.
        log.warning("ANTHROPIC_API_KEY is not set; relying on a stored auth profile")
    return anthropic.Anthropic()


def _batch(client, titles: list[str], model: str) -> dict[int, tuple[str, str]]:
    """Classify one batch. Returns {index: (verdict, reason)}."""
    import anthropic

    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    try:
        response = client.messages.create(
            model=model,
            # Each verdict is an index, a word and a short reason -- roughly 30
            # tokens. Sized with headroom so a batch never truncates mid-array.
            max_tokens=200 * len(titles) + 1000,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": listing}],
        )
    except anthropic.RateLimitError as exc:
        raise ClassifierUnavailable(f"rate limited: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise ClassifierUnavailable(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClassifierUnavailable(f"could not reach the API: {exc}") from exc
    except TypeError as exc:
        # The SDK resolves credentials lazily, so "no API key" surfaces here as
        # a TypeError from header validation rather than at construction. Left
        # unhandled it prints an SDK stack trace, which reads like a bug in the
        # digest instead of a missing environment variable.
        if "authentication" in str(exc).lower():
            raise ClassifierUnavailable(
                "no credentials found - set ANTHROPIC_API_KEY, or run "
                "`ant auth login` to store a profile"
            ) from exc
        raise

    if response.stop_reason == "refusal":
        raise ClassifierUnavailable("the model declined to classify this batch")

    text = next((b.text for b in response.content if b.type == "text"), "")
    # output_config guarantees valid JSON matching the schema, so a parse error
    # here means something changed upstream -- surface it rather than guess.
    payload = json.loads(text)

    out: dict[int, tuple[str, str]] = {}
    for row in payload["verdicts"]:
        index = row["index"]
        if 0 <= index < len(titles):
            out[index] = (row["verdict"], row["reason"])
    return out


def classify(titles: list[str], model: str = MODEL,
             batch_size: int = BATCH_SIZE, progress=None) -> dict[str, tuple[str, str]]:
    """Classify titles. Returns {title: (verdict, reason)}.

    Titles the model omits are simply absent from the result. Callers decide
    what an absent title means; this module never invents a verdict.
    """
    client = _client()
    unique = list(dict.fromkeys(titles))
    results: dict[str, tuple[str, str]] = {}

    for start in range(0, len(unique), batch_size):
        chunk = unique[start:start + batch_size]
        for index, verdict in _batch(client, chunk, model).items():
            results[chunk[index]] = verdict
        if progress:
            progress(min(start + batch_size, len(unique)), len(unique))

    missing = len(unique) - len(results)
    if missing:
        log.warning("classifier returned no verdict for %d of %d titles",
                    missing, len(unique))
    return results
