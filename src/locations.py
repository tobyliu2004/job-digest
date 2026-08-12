"""US-location detection, shared by the scrapers.

Sources tag country inconsistently. Simplify's index has a `countries` field
that is sometimes empty; the GitHub feed has no country field at all, only
free-text locations like "NYC", "San Francisco, CA", "London, UK",
"Toronto, ON, Canada", "Remote in USA".

Filtering on a `countries:=[United States]` gate alone therefore drops
US postings that simply were not tagged. This module is the post-filter used
instead: it excludes a posting only when EVERY location it lists is
identifiably foreign. That direction matters -- a denylist of foreign markers
is reliable, whereas trying to positively recognise every US city is not.
"""

from __future__ import annotations

# Substrings that identify a non-US location. Matched against a lowercased
# location string. "uk" and "us" are handled as whole tokens (see _tokens)
# because they appear inside ordinary words.
_FOREIGN = {
    "united kingdom", "england", "scotland", "wales", "ireland", "canada",
    "india", "germany", "france", "spain", "netherlands", "singapore",
    "japan", "china", "australia", "israel", "switzerland", "sweden",
    "poland", "brazil", "mexico", "korea", "taiwan", "hong kong",
    "united arab emirates", "dubai", "italy", "norway", "denmark", "finland",
    "belgium", "austria", "portugal", "romania", "czech", "hungary", "greece",
    "turkey", "argentina", "chile", "colombia", "philippines", "vietnam",
    "thailand", "indonesia", "malaysia", "new zealand", "south africa",
    "egypt", "nigeria", "kenya", "ukraine", "bulgaria", "serbia", "croatia",
    "lithuania", "latvia", "estonia", "luxembourg", "iceland", "peru",
}

# Whole-token foreign markers (avoid matching inside other words).
_FOREIGN_TOKENS = {"uk", "ca-on", "on", "qc", "bc", "ab"}

# Canadian province codes above would also match nothing US, but "ON"/"CA"
# are ambiguous with Ontario vs California. Only trust them when the string
# also names a Canadian city or the literal word Canada, which _FOREIGN
# already covers -- so province codes are deliberately NOT used alone.
_FOREIGN_TOKENS = {"uk"}


def _looks_foreign(location: str) -> bool:
    low = location.lower()
    if any(marker in low for marker in _FOREIGN):
        return True
    tokens = {t.strip(" ,.()") for t in low.replace("/", " ").split()}
    return bool(tokens & _FOREIGN_TOKENS)


def is_us(locations: list[str]) -> bool:
    """True if any listed location could be US-based.

    An empty/untagged location returns True: an untagged posting is exactly
    the kind this digest must not silently drop, and the email shows the
    location so a wrong guess costs one glance.
    """
    if not locations:
        return True
    return any(not _looks_foreign(loc) for loc in locations if loc)
