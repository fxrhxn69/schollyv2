"""Mastodon collector.

Reads public posts through the Mastodon API (no login needed). A source can be
  - an account, e.g. "EUErasmusPlus@respublicae.eu", or
  - a hashtag, e.g. "ErasmusMundus".
Posts are read through one home server (mastodon.social by default),
which can also show accounts from other servers.
"""

import datetime as dt
import re

from bs4 import BeautifulSoup

from .common import events_from_post, parse_iso

DEFAULT_INSTANCE = "mastodon.social"


def _text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.insert_after("\n")
    return soup.get_text()


def _links(html):
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        cls = " ".join(a.get("class") or [])
        if "mention" in cls or "hashtag" in cls:
            continue
        out.append(a["href"])
    return out


def statuses(source, http):
    instance = (source.get("instance") or DEFAULT_INSTANCE).replace("https://", "").strip("/")
    base = f"https://{instance}/api/v1/"
    if source.get("tag"):
        tag = source["tag"].lstrip("#")
        return http.get_json(base + f"timelines/tag/{tag}", params={"limit": 40}), f"#{tag}"
    acct = (source.get("account") or source.get("handle") or "").lstrip("@")
    if not acct and source.get("url"):
        m = re.match(r"https://([^/]+)/@([^/?#]+)", source["url"])
        if m:
            acct = f"{m.group(2)}@{m.group(1)}"
    if not acct:
        raise RuntimeError("Mastodon source needs an 'account' or a 'tag'")
    who = http.get_json(base + "accounts/lookup", params={"acct": acct})
    data = http.get_json(base + f"accounts/{who['id']}/statuses",
                         params={"limit": 40, "exclude_replies": "true"})
    return data, who.get("display_name") or acct


def collect(source, http, ctx):
    data, label = statuses(source, http)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ctx["social_days"])
    keywords = [k.lower() for k in source.get("keywords", [])]
    out = []
    for st in data:
        st = st.get("reblog") or st
        posted = parse_iso(st.get("created_at"))
        if not posted:
            continue
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=dt.timezone.utc)
        if posted < cutoff:
            continue
        text = _text(st.get("content"))
        if keywords and not any(k in text.lower() for k in keywords):
            continue
        card = st.get("card") or {}
        links = [u for u in _links(st.get("content")) + [card.get("url") or ""] if u.startswith("https://")]
        acct = st.get("account") or {}
        organizer = source.get("organizer") if not source.get("tag") else ""
        organizer = organizer or acct.get("display_name") or acct.get("acct") or label
        post_url = st.get("url") or st.get("uri") or ""
        extra = "\n".join(x for x in (card.get("title"), card.get("description")) if x)
        for ev in events_from_post(text, posted, links[0] if links else post_url, organizer, "Mastodon",
                                   ctx["tz"], extra_text=extra):
            ev["social_post"] = post_url
            out.append(ev)
    return out
