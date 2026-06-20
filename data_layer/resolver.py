"""Turn whatever a user types into a numeric SteamID64.

Accepts all four common formats:
    76561198000000000                          (bare SteamID64)
    https://steamcommunity.com/profiles/7656...  (profile URL with the ID)
    https://steamcommunity.com/id/daniel         (custom URL)
    daniel                                       (bare custom name)

Steam has no "search by display name" endpoint — display names aren't unique
and change freely — so only the custom-URL name (vanity) can be resolved.
"""
import re

from . import steam_client


class SteamResolveError(ValueError):
    """Raised when the input can't be turned into a valid SteamID64."""


_PROFILE_ID_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_VANITY_URL_RE = re.compile(r"steamcommunity\.com/id/([^/?#]+)")
_NUMERIC_RE    = re.compile(r"^\d{17}$")


def resolve_steam_id(user_input: str) -> str:
    """Resolve any supported input format to a SteamID64 string.

    Raises SteamResolveError with a user-friendly message on failure."""
    raw = (user_input or "").strip()
    if not raw:
        raise SteamResolveError("Please enter a Steam ID, custom URL, or profile link.")

    # 1) Full .../profiles/<id> URL
    m = _PROFILE_ID_RE.search(raw)
    if m:
        return m.group(1)

    # 2) Bare 17-digit SteamID64
    if _NUMERIC_RE.match(raw):
        return raw

    # 3) .../id/<vanity> URL  ->  pull out the vanity name
    m = _VANITY_URL_RE.search(raw)
    vanity = m.group(1) if m else raw.strip("/")

    # 4) Resolve the vanity / custom name via the Steam API
    steam_id = steam_client.resolve_vanity_url(vanity)
    if not steam_id:
        raise SteamResolveError(
            f"Couldn't find a Steam profile for '{user_input}'. That might be your in-game "
            "display name, which can't be searched — use your custom URL alias (the name in "
            "steamcommunity.com/id/…) or paste your full profile URL."
        )
    return steam_id
