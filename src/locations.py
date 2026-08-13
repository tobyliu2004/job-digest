"""US-location detection for the Pitt CSC feed.

That feed has no country field -- only free-text locations like "NYC",
"San Francisco, CA", "London, UK", "Toronto, ON, Canada", "Remote in USA".
So the US filter has to read those strings.

A posting is excluded only when EVERY location it lists is identifiably
foreign. That direction matters: a denylist of foreign markers is reliable,
while positively recognising every US city is not. A multi-site posting
("London, UK | New York, NY") stays on the strength of its US site.
"""

from __future__ import annotations

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

# "uk" needs whole-token matching -- it appears inside ordinary words.
_FOREIGN_TOKENS = {"uk"}


def _looks_foreign(location: str) -> bool:
    low = location.lower()
    if any(marker in low for marker in _FOREIGN):
        return True
    tokens = {t.strip(" ,.()") for t in low.replace("/", " ").split()}
    return bool(tokens & _FOREIGN_TOKENS)


def is_us(locations: list[str]) -> bool:
    """True if any listed location could be US-based.

    An untagged location returns True: the email shows the location, so a wrong
    guess costs one glance, while a wrong exclusion loses a job silently.
    """
    named = [loc for loc in locations if loc and loc.strip()]
    if not named:
        # No location at all, or only blanks. `[""]` used to fall through to an
        # empty generator and return False, so a posting with a blank location
        # was treated as foreign and silently dropped -- the opposite of the
        # intent stated above.
        return True
    return any(not _looks_foreign(loc) for loc in named)
