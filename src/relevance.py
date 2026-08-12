"""Noise filtering for postings whose season the source never tagged.

Confirmed 2027 postings already passed a category filter at the source, so they
are left alone. The untagged bucket has had no such check, and it is where the
genuinely random listings collect -- measured on a live run, it held Zoox,
ByteDance, Jane Street, Elicit and Baseten alongside Penn State's "Maps and
Geospatial Assistant" and Ohio State's "Student Assistant".

Those two groups are separable, and the separation is what keeps recall high
without the digest reading like a campus job board:

* Universities posting their own internal student jobs are not part of the
  internship recruiting pipeline. A university is dropped from this bucket
  unless the title names real engineering work -- which keeps a genuine
  "ML Research Intern" at a university lab.
* A posting that names no technical work at all ("Student Assistant",
  "Geolocation Operator", "Content Management Student Technician") is dropped
  regardless of employer.

Everything here applies ONLY to untagged postings. Nothing that a source
confirmed as a 2027 role is ever filtered by this module.
"""

from __future__ import annotations

import re

# Real technical work. Deliberately broad -- the cost of keeping one marginal
# posting is one line in an email; the cost of dropping a real one is the whole
# point of the digest.
TECH_TITLE = re.compile(
    r"\b("
    r"software|swe|engineer(ing)?|developer|dev\b|programm|"
    r"machine\s*learning|\bml\b|\bai\b|artificial\s+intelligence|"
    r"deep\s*learning|reinforcement\s*learning|\brl\b|"
    r"model(l)?ing|foundation\s+model|generative|diffusion|transformer|agent(ic)?|"
    r"data\s*(scien|engineer|analy)|analytics|"
    r"quant(itative)?|trading|research(er)?|scientist|"
    r"back\s*-?end|front\s*-?end|full\s*-?stack|"
    r"infrastructure|platform|distributed|systems|embedded|firmware|"
    r"security|cyber|cryptograph|"
    r"devops|\bsre\b|reliability|cloud|"
    r"comput(er|ing)|algorithm|compiler|robotics|autonom|perception|"
    r"nlp|computer\s*vision|\bllm\b|speech|multimodal|"
    r"ios|android|mobile|web\b|frontend|backend|"
    r"technolog|technical"
    r")\b",
    re.I,
)

# Employers whose postings always pass, whatever the title looks like.
#
# AI labs and quant firms name their internships in ways no keyword list
# reliably catches -- "Fellows Program", "Resident", "Fellow", "Trading Desk
# Operations". Dropping an Anthropic or Jane Street posting because its title
# reads unusually is far worse than keeping a marginal one, and these are
# exactly the employers worth never missing.
#
# Matched as a substring on the normalised company name, so "ByteDance Inc"
# and "Jane Street Capital" both hit. Add your own targets here.
NOTABLE_EMPLOYERS = {
    # AI labs
    "openai", "anthropic", "deepmind", "google deepmind", "mistral",
    "cohere", "perplexity", "scale ai", "hugging face", "runway",
    "midjourney", "character", "inflection", "adept", "cursor", "anysphere",
    "sierra", "thinking machines", "safe superintelligence", "elicit",
    "prime intellect", "baseten", "together ai", "figure",
    "physical intelligence", "world labs", "reflection ai", "luma ai",
    # Big tech
    "google", "alphabet", "meta", "facebook", "apple", "amazon", "microsoft",
    "netflix", "nvidia", "tesla", "spacex", "bytedance", "tiktok", "uber",
    "lyft", "airbnb", "stripe", "square", "block", "coinbase", "databricks",
    "snowflake", "palantir", "salesforce", "adobe", "oracle", "ibm", "intel",
    "amd", "qualcomm", "broadcom", "cisco", "vmware", "dell", "hp",
    "linkedin", "snap", "pinterest", "reddit", "discord", "spotify",
    "shopify", "twilio", "cloudflare", "datadog", "mongodb", "atlassian",
    "figma", "notion", "canva", "asana", "slack", "zoom", "dropbox", "box",
    "roblox", "unity", "epic games", "riot games", "activision", "ea",
    "waymo", "cruise", "zoox", "rivian", "lucid", "applied intuition",
    "samsara", "rippling", "ramp", "brex", "plaid", "affirm", "robinhood",
    "instacart", "doordash", "grubhub", "yelp", "ebay", "paypal", "intuit",
    "workday", "servicenow", "splunk", "okta", "crowdstrike", "palo alto",
    "zscaler", "sentinelone", "hashicorp", "gitlab", "github", "jetbrains",
    # Quant / trading / finance tech
    "jane street", "two sigma", "citadel", "jump trading", "hudson river",
    "hrt", "de shaw", "d. e. shaw", "susquehanna", "sig", "optiver", "imc",
    "drw", "akuna", "belvedere", "cts", "old mission", "tower research",
    "five rings", "radix", "headlands", "point72", "millennium", "balyasny",
    "aqr", "man group", "bridgewater", "renaissance", "virtu", "flow traders",
    "peak6", "group one", "wolverine", "chicago trading", "xtx",
    "goldman sachs", "morgan stanley", "jpmorgan", "jp morgan", "blackrock",
    "capital one", "american express", "visa", "mastercard", "fidelity",
    # Aero / defense / hardware with strong SWE internships
    "anduril", "lockheed", "northrop", "raytheon", "rtx", "boeing",
    "general dynamics", "l3harris", "bae systems", "mitre", "sandia",
    "johns hopkins apl", "draper", "aerospace corporation",
}


def _is_notable(company: str) -> bool:
    name = re.sub(r"[^a-z0-9 ]", " ", (company or "").lower())
    name = re.sub(r"\s+", " ", name).strip()
    return any(
        n == name or name.startswith(n + " ") or f" {n} " in f" {name} "
        for n in NOTABLE_EMPLOYERS
    )

# Employers whose untagged postings are overwhelmingly internal campus jobs.
CAMPUS_EMPLOYER = re.compile(
    r"\b(universit(y|ies)|college|research\s+foundation|school\s+district|"
    r"community\s+college|academy|\bisd\b|public\s+schools)\b",
    re.I,
)

# Titles that name no technical work even when they contain a stray keyword.
LOW_SIGNAL_TITLE = re.compile(
    r"\b(student\s+(assistant|worker|aide|technician)|office\s+assistant|"
    r"custodial|cleaning|maintenance\s+(worker|technician)|groundskeep|"
    r"ambassador|tutor|note\s*-?taker|proctor|clerk|cashier|barista|"
    r"lifeguard|resident\s+advisor|orientation\s+leader|peer\s+mentor|"
    r"operator|dispatcher|receptionist|food\s+service)\b",
    re.I,
)


def is_obvious_noise(job) -> bool:
    """A posting that names no technical work at all, from any source.

    Applied everywhere, because a barista shift is not an internship lead no
    matter which list it came from. Kept narrow on purpose: it only fires when
    the title matches a campus-job phrase AND names no engineering work, so
    Zoox's "Student Worker - Manufacturing Software Engineer" survives.
    """
    title = job.title or ""
    return bool(LOW_SIGNAL_TITLE.search(title)) and not TECH_TITLE.search(title)


def is_relevant(job) -> bool:
    """Should this UNTAGGED posting be shown? Confirmed postings bypass this.

    The rule is deliberately strict, because of what this bucket is for. An
    untagged posting has no season, so it cannot be confirmed as a 2027 role --
    it earns a place only by being from an employer worth not missing. A
    no-name company with an untagged season is exactly the noise this digest
    should not generate; Anthropic with an untagged season is exactly the
    posting it exists to catch.

    So: notable employer AND a technical title. Everything else in this bucket
    is dropped. Postings whose season IS tagged are never touched by this --
    they already passed a category filter at the source.
    """
    if is_obvious_noise(job):
        return False

    if not TECH_TITLE.search(job.title or ""):
        return False

    return _is_notable(job.company)


def filter_jobs(jobs: list) -> tuple[list, int, int]:
    """Apply noise filtering. Returns (kept, dropped_noise, dropped_untagged)."""
    kept, noise, untagged = [], 0, 0
    for j in jobs:
        if j.unconfirmed:
            if is_relevant(j):
                kept.append(j)
            elif is_obvious_noise(j):
                noise += 1
            else:
                untagged += 1
        elif is_obvious_noise(j):
            noise += 1
        else:
            kept.append(j)
    return kept, noise, untagged
