"""Collectors for each kind of source."""

import re

from . import bluesky, mastodon, web, youtube

COLLECTORS = {
    "web": web.collect,
    "youtube": youtube.collect,
    "bluesky": bluesky.collect,
    "mastodon": mastodon.collect,
}


def source_type(src):
    """Use the source's "type", or guess it from the URL."""
    t = (src.get("type") or "").lower().strip()
    if t:
        return t
    url = src.get("url") or ""
    if re.search(r"(^|//)(www\.|m\.)?youtube\.com/", url):
        return "youtube"
    if "bsky.app/profile/" in url:
        return "bluesky"
    return "web"


def describe(src):
    t = source_type(src)
    if t == "youtube":
        return f"YouTube {src.get('handle') or src.get('channel_id') or src.get('url')}"
    if t == "bluesky":
        return f"Bluesky @{bluesky.handle_of(src)}"
    if t == "mastodon":
        return f"Mastodon #{src['tag'].lstrip('#')}" if src.get("tag") else f"Mastodon @{src.get('account') or src.get('url')}"
    return src.get("url", "")
