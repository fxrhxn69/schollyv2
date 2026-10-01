"""Shared helpers: polite HTTP, date and time parsing, and event building."""

import datetime as dt
import hashlib
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover  (Python < 3.9)
    ZoneInfo = None

USER_AGENT = "SchollySeminarScraper/2.0 (personal scholarship tracker; checks public event pages)"
TIMEOUT = 20


# ---------------------------------------------------------------- HTTP

class Http:
    """A small HTTP client that waits between requests to the same site
    and, for normal web pages, follows robots.txt."""

    def __init__(self, delay=2.0, api_delay=1.0):
        self.delay = delay
        self.api_delay = api_delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self._last = {}
        self._robots = {}

    def _wait(self, host, gap):
        last = self._last.get(host)
        if last is not None:
            pause = gap - (time.monotonic() - last)
            if pause > 0:
                time.sleep(pause)
        self._last[host] = time.monotonic()

    def allowed(self, url):
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=TIMEOUT)
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(USER_AGENT, url)

    def get(self, url, params=None, api=False, headers=None):
        """Fetch a URL. Web pages check robots.txt first. Public APIs
        (YouTube Data API, Bluesky, Mastodon) are made for programs, so
        they skip that check but still wait between calls."""
        if url.startswith("file://") or (not url.startswith("http") and Path(url).exists()):
            path = url[7:] if url.startswith("file://") else url
            return FakeResponse(Path(path).read_text(encoding="utf-8", errors="replace"), url)
        if not api and not self.allowed(url):
            raise RuntimeError("robots.txt asks automated visitors not to read this page")
        self._wait(urlparse(url).netloc, self.api_delay if api else self.delay)
        try:
            r = self.session.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.Timeout:
            raise RuntimeError("the site took too long to answer") from None
        except requests.RequestException as e:
            raise RuntimeError(f"could not reach the site ({type(e).__name__})") from None
        if r.status_code in (401, 403, 429):
            raise RuntimeError(f"the site refused the request (HTTP {r.status_code})")
        if r.status_code == 404:
            raise RuntimeError("page or account not found (HTTP 404)")
        if r.status_code >= 400:
            # Do not repeat the URL here: API URLs can contain a key.
            raise RuntimeError(f"the site answered with an error (HTTP {r.status_code})")
        return r

    def get_json(self, url, params=None):
        return self.get(url, params=params, api=True).json()


class FakeResponse:
    """Used for local test files."""

    def __init__(self, text, url):
        self.text = text
        self.url = url
        self.headers = {}

    def json(self):
        import json
        return json.loads(self.text)


# ---------------------------------------------------------------- words and dates

EVENT_WORDS = re.compile(
    r"\b(webinars?|web-?seminars?|seminars?|info(?:rmation)?\s+sessions?|info\s+days?|open\s+(?:days?|house)|"
    r"virtual\s+fairs?|online\s+fairs?|education\s+fairs?|study\s+fairs?|fairs?|q\s*&\s*a|ask\s+(?:us|me)\s+anything|"
    r"live\s+(?:session|stream|chat|event)s?|livestreams?|youtube\s+live|instagram\s+live|going\s+live|"
    r"meet\s+us|workshops?|masterclass(?:es)?|information\s+events?|"
    # Swedish, Norwegian and Danish
    r"webbinari(?:um|er|et)|webinarier|informationsmöten?|informasjonsmøter?|infomøter?|informationsmøder?|"
    r"öppet\s+hus|åpen\s+dag|åbent\s+hus|livesändning|direktsändning|studiemässa|utdanningsmesse)\b",
    re.I,
)

# Social media posts use a stricter list. Words like "seminar" or "workshop"
# there usually mean research events, not admissions webinars.
SOCIAL_EVENT_WORDS = re.compile(
    r"\b(webinars?|web-?seminars?|info(?:rmation)?\s+(?:sessions?|meetings?|events?)|info\s+days?|"
    r"open\s+(?:days?|house)|(?:virtual|online|education|study|university)\s+fairs?|q\s*&\s*a|"
    r"ask\s+(?:us|me)\s+anything|live\s+(?:session|stream|chat|event)s?|livestreams?|going\s+live|meet\s+us|"
    r"webbinari(?:um|er|et)|webinarier|informationsmöten?|informasjonsmøter?|infomøter?|informationsmøder?|"
    r"öppet\s+hus|åpen\s+dag|åbent\s+hus|studiemässa|utdanningsmesse)\b",
    re.I,
)

MONTH_NAMES = {
    1: ["january", "jan", "januari", "januar"],
    2: ["february", "feb", "februari", "februar"],
    3: ["march", "mar", "mars", "marts"],
    4: ["april", "apr"],
    5: ["may", "maj", "mai"],
    6: ["june", "jun", "juni"],
    7: ["july", "jul", "juli"],
    8: ["august", "aug", "augusti"],
    9: ["september", "sep", "sept"],
    10: ["october", "oct", "oktober", "okt"],
    11: ["november", "nov"],
    12: ["december", "dec", "desember", "des"],
}
MONTHS = {name: num for num, names in MONTH_NAMES.items() for name in names}
_mon_alts = sorted(MONTHS, key=len, reverse=True)
MON_RE = r"(" + "|".join(_mon_alts) + r")"
WEEKDAY_RE = (r"(?:(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?|"
              r"måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag|"
              r"mandag|tirsdag|onsdag|torsdag|fredag|lørdag|søndag)\.?,?\s+")

DATE_PATTERNS = [
    # 2026-10-07
    (re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # 7 October 2026 / Wednesday 7 October / 7th Oct / 7. oktober
    (re.compile(r"\b(?:" + WEEKDAY_RE + r")?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th|e|a)?\.?\s+(?:of\s+)?" + MON_RE +
                r"\b\.?,?(?:\s+(20\d\d))?", re.I), "dmy"),
    # October 7, 2026 / Oct 7
    (re.compile(r"\b(?:" + WEEKDAY_RE + r")?" + MON_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b,?(?:\s+(20\d\d))?", re.I), "mdy"),
]
RELATIVE_RE = re.compile(r"\b(today|tonight|tomorrow|idag|i\s+dag|ikväll|imorgon|i\s+morgon|i\s+morgen)\b", re.I)

TZ_WORDS = r"(CEST|CET|GMT|UTC|BST|EEST|EET|IST|BDT|ET|EST|EDT|PT|PST|PDT|Swedish\s+time|Norwegian\s+time|Danish\s+time)"
TIME_RE = re.compile(
    r"\b(?:at\s+|kl\.?\s*|klockan\s+)?"
    r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?\s*(?:[-–—]|to|till)\s*\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?"
    r"|\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))"
    r"(?:\s*\(?" + TZ_WORDS + r"\)?)?",
    re.I,
)


def today():
    return dt.date.today()


def make_date(y, m, d, ref):
    """Build a date. If the year is missing, use the next match on or after ref (with 30 days' slack)."""
    try:
        if y:
            return dt.date(int(y), int(m), int(d))
        cand = dt.date(ref.year, int(m), int(d))
        if cand < ref - dt.timedelta(days=30):
            cand = dt.date(ref.year + 1, int(m), int(d))
        return cand
    except (ValueError, TypeError):
        return None


def find_date(text, ref, allow_relative=False):
    """Return (date, match) for the first date in the text."""
    best = None
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        if kind == "ymd":
            d = make_date(m.group(1), m.group(2), m.group(3), ref)
        elif kind == "dmy":
            d = make_date(m.group(3), MONTHS.get(m.group(2).lower().rstrip(".")), m.group(1), ref)
        else:
            d = make_date(m.group(3), MONTHS.get(m.group(1).lower().rstrip(".")), m.group(2), ref)
        if d and (best is None or m.start() < best[1].start()):
            best = (d, m)
    if best:
        return best
    if allow_relative:
        m = RELATIVE_RE.search(text)
        if m:
            word = m.group(1).lower().replace(" ", "")
            add = 1 if word in ("tomorrow", "imorgon", "imorgen") else 0
            return ref + dt.timedelta(days=add), m
    return None, None


def _to_24h(part):
    part = part.strip().lower().replace(".", ":")
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", part)
    if not m:
        return part
    h, mi, ap = int(m.group(1)), m.group(2) or "00", m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23:
        return ""
    return f"{h:02d}:{mi}"


def find_time(text):
    """Return (time, timezone label) such as ("15:00–18:00", "CEST")."""
    for m in TIME_RE.finditer(text):
        raw = m.group(1)
        # A bare number like "2026" or "15" without : or am/pm is not a time.
        if not re.search(r"[:.]\d{2}|am|pm", raw, re.I):
            continue
        parts = re.split(r"\s*(?:[-–—]|to|till)\s*", raw, flags=re.I)
        if len(parts) == 2:
            # "3-5pm": the first part borrows am/pm from the second.
            ap = re.search(r"(am|pm)\s*$", parts[1], re.I)
            if ap and not re.search(r"am|pm", parts[0], re.I):
                parts[0] += ap.group(1)
        out = [p for p in (_to_24h(p) for p in parts) if p]
        if not out:
            continue
        tz = (m.group(2) or "").strip()
        tz = {"swedish time": "CET/CEST", "norwegian time": "CET/CEST", "danish time": "CET/CEST"}.get(tz.lower(), tz.upper())
        return "–".join(out), tz
    return "", ""


def clean(text, limit=200):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text.rstrip(" ,;:-")


def https_only(url):
    return url if isinstance(url, str) and url.startswith("https://") else None


# ---------------------------------------------------------------- time zones

def tz_label(tzname, when):
    """A short label like "CEST" or "UTC+6"."""
    if ZoneInfo is None:
        return "UTC"
    try:
        zone = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        return "UTC"
    name = when.astimezone(zone).tzname() or ""
    if re.fullmatch(r"[A-Z]{2,5}", name):
        return name
    off = when.astimezone(zone).utcoffset() or dt.timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def localize(when, tzname):
    """Turn an aware datetime into (date, "HH:MM", label) in the chosen time zone."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    zone = dt.timezone.utc
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(tzname)
        except Exception:  # noqa: BLE001
            tzname = "UTC"
    local = when.astimezone(zone)
    return local.date(), local.strftime("%H:%M"), tz_label(tzname, when) if zone is not dt.timezone.utc else "UTC"


def parse_iso(value):
    """Parse an ISO 8601 date-time. Returns an aware datetime, a naive one, or None."""
    if not value:
        return None
    value = str(value).strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?", value)
        if not m:
            return None
        return dt.datetime.fromisoformat(m.group(1) + ("T" + m.group(2) if m.group(2) else "T00:00"))


# ---------------------------------------------------------------- events

def event_id(title, date):
    h = hashlib.sha1(f"{date}|{title.lower()}".encode()).hexdigest()[:10]
    return f"scr-{date}-{h}"


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


URL_RE = re.compile(r"https?://\S+")
HASHTAG_TAIL_RE = re.compile(r"(?:\s*#\w+)+\s*$")


def title_from_post(text):
    """Pick a short title from a social media post."""
    text = URL_RE.sub("", text or "")
    lines = [l.strip(" -–—•*|") for l in re.split(r"[\n\r]+", text) if l.strip()]
    pick = next((l for l in lines if SOCIAL_EVENT_WORDS.search(l)), lines[0] if lines else "")
    # Drop leading emoji and symbols.
    pick = re.sub(r"^[^\w#@\"'(]+", "", pick)
    pick = HASHTAG_TAIL_RE.sub("", pick)
    # Take dates and times out of the title; they have their own fields.
    for rx, _ in DATE_PATTERNS:
        pick = rx.sub(" § ", pick)
    pick = TIME_RE.sub(" § ", pick)
    # Drop the little words that pointed at a date or time ("on", "at", "kl.").
    pick = re.sub(r"\s*\b(?:on|at|kl|klockan|den|from)\.?\s*(?=§)", " ", pick, flags=re.I)
    pick = re.sub(r"\s*§[\s§]*", " ", pick)
    pick = re.sub(r"\s*\b(?:on|at|kl|klockan|den|from)\.?\s*(?=[,.;:!?–—-]|$)", "", pick, flags=re.I)
    pick = re.sub(r"\s*([,;:–—-])(?:\s*[,;:–—-])+", r"\1", pick)
    pick = re.sub(r"\s+([,.;:!?])", r"\1", pick)
    pick = re.sub(r"[\s,;:–—-]+$", "", pick)
    first = re.split(r"(?<=[a-z0-9)])[.!?]\s", pick)[0]
    return clean(first if len(first) >= 12 else pick, 140)


def events_from_post(text, posted, url, organizer, platform, tzname, extra_text=""):
    """Find an upcoming event announced in a social media post.

    A post counts if it mentions an event word and a date (or "today" /
    "tomorrow") that is on or after the day it was posted."""
    full = f"{text}\n{extra_text}".strip()
    if not SOCIAL_EVENT_WORDS.search(full):
        return []
    ref = posted.date() if isinstance(posted, dt.datetime) else posted
    date, _ = find_date(full, ref, allow_relative=True)
    if not date or date < ref:
        return []
    t, tz = find_time(full)
    return [{
        "title": title_from_post(text) or title_from_post(extra_text) or "Event",
        "organizer": organizer,
        "date": date.isoformat(),
        "time": t,
        "timezone": tz,
        "mode": "Online" if re.search(r"\b(online|virtual|zoom|teams|webinar|webbinari\w*|livestream|live\s?stream|youtube)\b",
                                       full, re.I) else "",
        "location": "",
        "url": https_only(url),
        "confidence": "check",
        "platform": platform,
    }]
