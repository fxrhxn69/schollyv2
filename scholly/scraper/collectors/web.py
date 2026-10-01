"""Website collector.

It tries four methods, from most to least reliable:
  1. schema.org Event data in the page (JSON-LD)
  2. calendar files (.ics) linked from the page
  3. RSS or Atom feeds (when the source URL is a feed)
  4. text blocks that mention an event word and contain a date
"""

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import (EVENT_WORDS, TIME_RE, ZoneInfo, clean, events_from_post, find_date, find_time,
                     https_only, localize, parse_iso)


def _when_fields(start, tzname):
    """Turn a start value into date/time/timezone fields.
    Times with an offset are converted to the chosen time zone.
    Times without one are kept as the organiser wrote them."""
    if start is None:
        return None
    if start.tzinfo is not None:
        d, t, label = localize(start, tzname)
        return {"date": d.isoformat(), "time": t, "timezone": label}
    has_time = start.time() != dt.time(0, 0)
    return {"date": start.date().isoformat(), "time": start.strftime("%H:%M") if has_time else "", "timezone": ""}


# ---------------------------------------------------------------- 1. JSON-LD

def from_jsonld(soup, page_url, tzname):
    out = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            walk(node["@graph"])
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(t and "Event" in str(t) for t in types):
            when = _when_fields(parse_iso(node.get("startDate")), tzname)
            if when:
                loc = node.get("location") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                online = "Online" if "Online" in str(node.get("eventAttendanceMode", "")) or (
                    isinstance(loc, dict) and "Virtual" in str(loc.get("@type", ""))) else ""
                org = node.get("organizer")
                if isinstance(org, list):
                    org = org[0] if org else {}
                out.append({
                    "title": clean(node.get("name") or "Event", 140),
                    "organizer": clean(org.get("name", "")) if isinstance(org, dict) else clean(str(org or "")),
                    **when,
                    "mode": online,
                    "location": clean(loc.get("name", "") if isinstance(loc, dict) else str(loc)),
                    "url": https_only(urljoin(page_url, node.get("url") or page_url)),
                    "confidence": "high",
                })
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


# ---------------------------------------------------------------- 2. iCalendar

def _ics_start(line_params, value, tzname):
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?", value)
    if not m:
        return None
    y, mo, d, h, mi, _, z = m.groups()
    if h is None:
        return {"date": f"{y}-{mo}-{d}", "time": "", "timezone": ""}
    start = dt.datetime(int(y), int(mo), int(d), int(h), int(mi))
    if z:
        start = start.replace(tzinfo=dt.timezone.utc)
    else:
        tzid = re.search(r"TZID=([^;:]+)", line_params or "")
        if tzid and ZoneInfo is not None:
            try:
                start = start.replace(tzinfo=ZoneInfo(tzid.group(1).strip('"')))
            except Exception:  # noqa: BLE001
                pass
    return _when_fields(start, tzname)


def parse_ics(text, page_url, tzname):
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold long lines
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        summ = re.search(r"^SUMMARY[^:]*:(.*)$", block, re.M)
        start = re.search(r"^DTSTART([^:]*):(\S+)", block, re.M)
        if not (summ and start):
            continue
        when = _ics_start(start.group(1), start.group(2), tzname)
        if not when:
            continue
        link = re.search(r"^URL[^:]*:(\S+)", block, re.M)
        loc = re.search(r"^LOCATION[^:]*:(.*)$", block, re.M)
        loc_text = clean((loc.group(1) if loc else "").replace("\\,", ","))
        out.append({
            "title": clean(summ.group(1).replace("\\,", ","), 140),
            "organizer": "",
            **when,
            "mode": "Online" if re.search(r"online|zoom|teams|webinar|webbinar", f"{summ.group(1)} {loc_text}", re.I) else "",
            "location": loc_text,
            "url": https_only(link.group(1) if link else page_url),
            "confidence": "high",
        })
    return out


def from_ics_links(soup, page_url, http, tzname):
    out = []
    links = [a.get("href") for a in soup.find_all("a", href=True)
             if a["href"].lower().split("?")[0].endswith(".ics") or a["href"].startswith("webcal:")]
    for href in links[:10]:
        url = urljoin(page_url, href.replace("webcal:", "https:", 1))
        try:
            out += parse_ics(http.get(url).text, page_url, tzname)
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------- 3. RSS / Atom

def looks_like_feed(text):
    head = text.lstrip()[:400].lower()
    return head.startswith("<?xml") and ("<rss" in head or "<feed" in head) or head.startswith(("<rss", "<feed"))


def parse_feed(text, page_url, organizer, tzname):
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return []
    out = []
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for it in items[:60]:
        def find(*names):
            for n in names:
                el = it.find(n)
                if el is not None:
                    return el
            return None

        title_el = find("title", "{http://www.w3.org/2005/Atom}title")
        desc_el = find("description", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content")
        link_el = find("link", "{http://www.w3.org/2005/Atom}link")
        date_el = find("pubDate", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated")
        title = (title_el.text or "") if title_el is not None else ""
        desc = BeautifulSoup((desc_el.text or "") if desc_el is not None else "", "html.parser").get_text(" ")
        link = ""
        if link_el is not None:
            link = link_el.get("href") or (link_el.text or "")
        posted = None
        if date_el is not None and date_el.text:
            try:
                posted = parsedate_to_datetime(date_el.text)
            except (TypeError, ValueError):
                posted = parse_iso(date_el.text)
        ref = (posted.date() if posted else dt.date.today()) - dt.timedelta(days=1)
        evs = events_from_post(title, ref, urljoin(page_url, link.strip()), organizer, "web feed", tzname, extra_text=desc)
        for ev in evs:
            ev["title"] = clean(title, 140) or ev["title"]
        out += evs
    return out


# ---------------------------------------------------------------- 4. page text

def from_text(soup, page_url, ref):
    """Best-effort: look at headings, list items and short blocks."""
    out = []
    for bad in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        bad.decompose()
    seen_text = set()
    for el in soup.find_all(["h2", "h3", "h4", "li", "p", "tr", "article", "div"]):
        if el.name == "div" and el.find(["div", "article", "li", "p"]):
            continue  # only leaf-ish divs
        text = clean(el.get_text(" ", strip=True), 400)
        if len(text) < 8 or len(text) >= 400 or text in seen_text:
            continue
        seen_text.add(text)
        heading = el.find_previous(["h2", "h3", "h4"]) if el.name not in ("h2", "h3", "h4") else None
        heading_text = clean(heading.get_text(" ", strip=True)) if heading else ""
        parent_text = ""
        anc = el.parent
        for _ in range(2):
            if anc is None or anc.name in ("body", "html", "main"):
                break
            parent_text += " " + clean(anc.get_text(" ", strip=True), 800)
            anc = anc.parent
        context = f"{heading_text} {text}"
        if not EVENT_WORDS.search(context) and not EVENT_WORDS.search(parent_text):
            continue
        date, m = find_date(text, ref)
        if not date:
            continue
        title = clean(text[:m.start()] + " " + text[m.end():]) if m else text
        title = re.sub(r"^[\s,:\-–—|]+", "", title)
        title = TIME_RE.sub("", title)
        title = re.sub(r"(\s*,)+", ",", title)
        title = re.sub(r"[,\s]*\b(online|virtual|on campus|in person)\b[,\s]*$", "", title, flags=re.I)
        title = clean(re.split(r"(?<=[a-z0-9)])\.\s", title)[0], 140)
        if len(title) < 6 and heading_text:
            title = clean(heading_text, 140)
        if len(title) < 6:
            continue
        t, tz = find_time(text)
        link = el.find("a", href=True)
        url = https_only(urljoin(page_url, link["href"])) if link else https_only(page_url)
        out.append({
            "title": title,
            "organizer": "",
            "date": date.isoformat(),
            "time": t,
            "timezone": tz,
            "mode": "Online" if re.search(r"\b(online|virtual|zoom|teams|webinars?|webbinari\w*|youtube)\b", context, re.I) else "",
            "location": "",
            "url": url,
            "confidence": "check",
        })
    return out


# ---------------------------------------------------------------- entry point

def collect(source, http, ctx):
    url = source["url"]
    resp = http.get(url)
    text, final_url = resp.text, getattr(resp, "url", url)
    if looks_like_feed(text):
        return parse_feed(text, final_url, source.get("organizer", ""), ctx["tz"])
    if text.lstrip().startswith("BEGIN:VCALENDAR"):
        return parse_ics(text, final_url, ctx["tz"])
    soup = BeautifulSoup(text, "html.parser")
    events = from_jsonld(soup, final_url, ctx["tz"]) + from_ics_links(soup, final_url, http, ctx["tz"])
    events += from_text(BeautifulSoup(text, "html.parser"), final_url, ctx["today"])
    for ev in events:
        ev["platform"] = "website"
    return events
