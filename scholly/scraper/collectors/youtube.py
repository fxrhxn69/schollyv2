"""YouTube collector.

Uses the official YouTube Data API v3. It needs a free API key in the
YOUTUBE_API_KEY environment variable. YouTube's rules and robots.txt do not
allow reading its pages or feeds with a script, so there is no key-free mode.

For each channel it finds:
  - scheduled live streams and premieres (exact start time), and
  - recent uploads whose title or description announce a webinar with a date.

Quota use per channel is about 103 units per run. The free daily quota is
10,000 units, so 20 channels a week is far below the limit.
"""

import datetime as dt
import os
import re

from .common import clean, events_from_post, localize, parse_iso

API = "https://www.googleapis.com/youtube/v3/"


class MissingKey(RuntimeError):
    pass


def api_key():
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def _call(http, endpoint, **params):
    params["key"] = api_key()
    try:
        return http.get_json(API + endpoint, params=params)
    except RuntimeError as e:
        if re.search(r"HTTP (400|403)", str(e)):
            raise RuntimeError(f"{e}. Check the API key, that 'YouTube Data API v3' is turned on "
                               "for it, and that today's quota is not used up") from None
        raise


def resolve_channel(source, http):
    """Return (channel_id, uploads_playlist_id, title) for a handle, URL or channel ID."""
    raw = (source.get("channel_id") or source.get("handle") or source.get("url") or "").strip()
    m = re.search(r"(UC[\w-]{22})", raw)
    if m:
        data = _call(http, "channels", part="snippet,contentDetails", id=m.group(1))
    else:
        h = re.search(r"@([\w.\-]+)", raw)
        handle = "@" + (h.group(1) if h else raw.lstrip("@"))
        data = _call(http, "channels", part="snippet,contentDetails", forHandle=handle)
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"YouTube channel not found: {raw}")
    ch = items[0]
    uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    return ch["id"], uploads, ch.get("snippet", {}).get("title", "")


def collect(source, http, ctx):
    if not api_key():
        raise MissingKey("skipped: add a free YouTube Data API key as YOUTUBE_API_KEY (see README)")
    channel_id, uploads, channel_title = resolve_channel(source, http)
    organizer = source.get("organizer") or channel_title

    ids = []
    # Scheduled live streams and premieres. This search costs 100 quota units.
    up = _call(http, "search", part="id", channelId=channel_id, eventType="upcoming", type="video", maxResults=15)
    ids += [i["id"]["videoId"] for i in up.get("items", []) if i.get("id", {}).get("videoId")]
    # Recent uploads. Costs 1 unit.
    if uploads:
        pl = _call(http, "playlistItems", part="contentDetails", playlistId=uploads, maxResults=15)
        ids += [i["contentDetails"]["videoId"] for i in pl.get("items", []) if i.get("contentDetails", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    vids = _call(http, "videos", part="snippet,liveStreamingDetails", id=",".join(ids[:50]))

    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for v in vids.get("items", []):
        sn = v.get("snippet", {})
        live = v.get("liveStreamingDetails") or {}
        url = f"https://www.youtube.com/watch?v={v['id']}"
        title = clean(sn.get("title", ""), 140)
        start = parse_iso(live.get("scheduledStartTime"))
        if start and not live.get("actualEndTime") and start >= now - dt.timedelta(hours=3):
            d, t, label = localize(start, ctx["tz"])
            out.append({
                "title": title or "YouTube live stream",
                "organizer": organizer,
                "date": d.isoformat(),
                "time": t,
                "timezone": label,
                "mode": "Online",
                "location": "YouTube Live" if sn.get("liveBroadcastContent") != "none" else "YouTube Premiere",
                "url": url,
                "confidence": "high",
                "platform": "YouTube",
            })
            continue
        # A normal video that announces an event, e.g. "Join our webinar on 12 November".
        posted = parse_iso(sn.get("publishedAt")) or now
        if posted < now - dt.timedelta(days=ctx["social_days"]):
            continue
        out += events_from_post(sn.get("title", ""), posted, url, organizer, "YouTube", ctx["tz"],
                                extra_text=(sn.get("description") or "")[:1500])
    return out
