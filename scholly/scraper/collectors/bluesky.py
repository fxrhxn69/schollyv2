"""Bluesky collector.

Reads an account's recent posts through Bluesky's open public API
(no login needed) and keeps posts that announce an upcoming event.
"""

import datetime as dt
import re

from .common import events_from_post, parse_iso

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def handle_of(source):
    raw = (source.get("handle") or source.get("url") or "").strip()
    m = re.search(r"bsky\.app/profile/([^/?#]+)", raw)
    return (m.group(1) if m else raw).lstrip("@")


def _links(record, embed):
    links = []
    for facet in record.get("facets") or []:
        for feat in facet.get("features") or []:
            if feat.get("uri"):
                links.append(feat["uri"])
    for e in (embed or {}, (embed or {}).get("media") or {}):
        ext = e.get("external") or {}
        if ext.get("uri"):
            links.append(ext["uri"])
    return links


def _embed_text(embed):
    parts = []
    for e in (embed or {}, (embed or {}).get("media") or {}):
        ext = e.get("external") or {}
        parts += [ext.get("title", ""), ext.get("description", "")]
    rec = (embed or {}).get("record") or {}
    rec = rec.get("record") if isinstance(rec.get("record"), dict) else rec
    val = rec.get("value") or {}
    if isinstance(val, dict):
        parts.append(val.get("text", ""))  # quoted post
    return "\n".join(p for p in parts if p)


def collect(source, http, ctx):
    handle = handle_of(source)
    data = http.get_json(API, params={"actor": handle, "limit": 50, "filter": "posts_no_replies"})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ctx["social_days"])
    out = []
    for item in data.get("feed", []):
        post = item.get("post") or {}
        record = post.get("record") or {}
        posted = parse_iso(record.get("createdAt") or post.get("indexedAt"))
        if not posted:
            continue
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=dt.timezone.utc)
        if posted < cutoff:
            continue
        author = post.get("author") or {}
        rkey = (post.get("uri") or "").rsplit("/", 1)[-1]
        post_url = f"https://bsky.app/profile/{author.get('handle', handle)}/post/{rkey}"
        links = [u for u in _links(record, post.get("embed")) if u.startswith("https://")]
        organizer = source.get("organizer") or author.get("displayName") or handle
        if author.get("handle") and author.get("handle") != handle:  # a repost
            organizer = author.get("displayName") or author["handle"]
        for ev in events_from_post(record.get("text", ""), posted, links[0] if links else post_url,
                                   organizer, "Bluesky", ctx["tz"], extra_text=_embed_text(post.get("embed"))):
            ev["social_post"] = post_url
            out.append(ev)
    return out
