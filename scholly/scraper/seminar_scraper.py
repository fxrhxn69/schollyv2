#!/usr/bin/env python3
"""
Seminar scraper for the Scholly scholarship tracker.

It checks university websites, YouTube channels, Bluesky accounts and
Mastodon accounts or hashtags for webinars, info sessions, fairs and open
days. It writes what it finds to a JSON file that the dashboard can import
(Seminars tab > Import scraped seminars).

Setup (once):
    pip install -r requirements.txt

Run:
    python seminar_scraper.py --out seminars.json

YouTube needs a free API key in the YOUTUBE_API_KEY environment variable.
Without it, YouTube sources are skipped and everything else still runs.
See the README for details.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import requests  # noqa: F401
    import bs4  # noqa: F401
except ImportError:  # pragma: no cover
    sys.exit("Missing packages. Run: pip install -r requirements.txt")

from collectors import COLLECTORS, describe, source_type  # noqa: E402
from collectors.common import Http, event_id, norm_title, today  # noqa: E402
from collectors.youtube import MissingKey  # noqa: E402

HERE = Path(__file__).resolve().parent
NOTES = {
    "high": "",
    "website": "Found by text matching on the web page. Check the date and title on the page.",
    "web feed": "Found in the site's news feed. Check the date and title on the page.",
    "YouTube": "Found in a YouTube video description. Check the details before you register.",
    "Bluesky": "Found in a Bluesky post. Check the date, time and link before you register.",
    "Mastodon": "Found in a Mastodon post. Check the date, time and link before you register.",
}


def load_sources(paths):
    sources = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        items = data["sources"] if isinstance(data, dict) else data
        for s in items:
            if not isinstance(s, dict) or s.get("enabled") is False:
                continue
            if not any(s.get(k) for k in ("url", "handle", "account", "tag", "channel_id")):
                continue  # comment-only entries
            sources.append(s)
    return sources


def redact(text):
    return re.sub(r"(key=)[^&\s]+", r"\1REDACTED", str(text))[:300]


def scrape(sources, tz="Asia/Dhaka", include_past=False, horizon_days=365, social_days=90,
           delay=2.0, only=None, verbose=False, http=None):
    ref = today()
    ctx = {"tz": tz, "today": ref, "social_days": social_days}
    http = http or Http(delay=delay)
    found, errors, checked = {}, [], []
    yt_skipped = 0

    for src in sources:
        kind = source_type(src)
        label = describe(src)
        if only and kind not in only:
            continue
        collect = COLLECTORS.get(kind)
        if not collect:
            errors.append({"source": label, "error": f"unknown source type '{kind}'"})
            continue
        try:
            events = collect(src, http, ctx)
        except MissingKey:
            yt_skipped += 1
            checked.append({"source": label, "platform": kind, "found": 0, "status": "skipped (no YouTube key)"})
            continue
        except Exception as e:  # noqa: BLE001
            msg = redact(e)
            errors.append({"source": label, "url": src.get("url"), "error": msg})
            checked.append({"source": label, "platform": kind, "found": 0, "status": "error"})
            if verbose:
                print(f"  ! {label}: {msg}")
            continue

        kept = 0
        for ev in events:
            try:
                d = dt.date.fromisoformat(ev["date"])
            except (KeyError, ValueError):
                continue
            if not include_past and d < ref:
                continue
            if (d - ref).days > horizon_days:
                continue
            conf = ev.pop("confidence", "check")
            platform = ev.pop("platform", kind)
            ev["organizer"] = ev.get("organizer") or src.get("organizer", "")
            ev["related_entry_ids"] = list(src.get("related_entry_ids", []))
            ev["source"] = f"Scraped {ref.isoformat()} from {label}"
            ev["notes"] = NOTES["high"] if conf == "high" else NOTES.get(platform, NOTES["website"])
            if ev.get("social_post") and ev["social_post"] != ev.get("url"):
                ev["notes"] += f" Post: {ev['social_post']}"
            ev.pop("social_post", None)
            ev["id"] = event_id(ev["title"], ev["date"])
            ev["registered"] = False
            ev["_conf"] = conf
            key = (ev["date"], norm_title(ev["title"]))
            old = found.get(key)
            if old:
                merged = sorted(set(old["related_entry_ids"]) | set(ev["related_entry_ids"]))
                if old["_conf"] != "high" and conf == "high":
                    found[key] = ev
                found[key]["related_entry_ids"] = merged
                continue
            found[key] = ev
            kept += 1
        checked.append({"source": label, "platform": kind, "found": kept, "status": "ok"})
        if verbose:
            print(f"  {label}: {kept} event(s)")

    if yt_skipped:
        errors.append({"source": "YouTube",
                       "error": f"{yt_skipped} YouTube channel(s) skipped. Add a free YouTube Data API key "
                                "as YOUTUBE_API_KEY to check them. See the README."})
    seminars = sorted(found.values(), key=lambda e: (e["date"], e.get("time", ""), e["title"]))
    for ev in seminars:
        ev.pop("_conf", None)
    return seminars, errors, checked


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collect webinars and info sessions for the Scholly tracker.")
    ap.add_argument("--sources", action="append",
                    help="sources file; repeat to use several (default: scraper_sources.json next to this script)")
    ap.add_argument("--out", default="seminars.json", help="file to import into the dashboard")
    ap.add_argument("--timezone", default="Asia/Dhaka",
                    help="show times from social media and calendars in this time zone (default: Asia/Dhaka)")
    ap.add_argument("--only", help="comma list of source types to check: web,youtube,bluesky,mastodon")
    ap.add_argument("--include-past", action="store_true", help="keep events that already happened")
    ap.add_argument("--days", type=int, default=365, help="only keep events within this many days")
    ap.add_argument("--social-days", type=int, default=90, help="only read posts from the last N days")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds to wait between pages on the same site")
    ap.add_argument("--verbose", action="store_true", help="print what each source gave")
    args = ap.parse_args(argv)

    paths = args.sources or [str(HERE / "scraper_sources.json")]
    sources = load_sources(paths)
    only = {x.strip().lower() for x in args.only.split(",")} if args.only else None
    print(f"Checking {len(sources)} source(s)...")
    seminars, errors, checked = scrape(sources, args.timezone, args.include_past, args.days,
                                       args.social_days, args.delay, only, args.verbose)
    Path(args.out).write_text(json.dumps({
        "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
        "timezone": args.timezone,
        "seminars": seminars,
        "errors": errors,
        "checked": checked,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    by_platform = {}
    for c in checked:
        by_platform[c["platform"]] = by_platform.get(c["platform"], 0) + c["found"]
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_platform.items()))
    print(f"Found {len(seminars)} seminar(s) ({summary}). Saved to {args.out}.")
    if errors:
        print(f"{len(errors)} problem(s). See the 'errors' list in {args.out}.")
        for e in errors:
            print(f"  - {e['source']}: {e['error']}")


if __name__ == "__main__":
    main()
