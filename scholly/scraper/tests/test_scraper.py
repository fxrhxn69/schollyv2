"""Offline tests for the seminar scraper. Run from the repository root:

    python -m unittest discover -s scraper/tests -v

They use made-up pages and API answers, so no internet is needed.
Dates are built relative to today, so the tests keep working over time.
"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seminar_scraper  # noqa: E402
from collectors import common, source_type  # noqa: E402
from collectors.common import find_date, find_time  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
TODAY = NOW.date()
SOON = TODAY + dt.timedelta(days=20)  # an upcoming event date
LATER = TODAY + dt.timedelta(days=40)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def long_date(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def iso(d):
    return d.isoformat().replace("+00:00", "Z")


class FakeResp:
    def __init__(self, text, url):
        self.text, self.url = text, url

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Answers requests from a table of URL pieces -> body (str or JSON-able)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _find(self, url, params):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        self.calls.append(full)
        for piece, body in self.routes:
            if piece in full:
                if isinstance(body, Exception):
                    raise body
                return body
        raise RuntimeError(f"page or account not found (HTTP 404): {url}")

    def get(self, url, params=None, api=False, headers=None):
        body = self._find(url, params)
        return FakeResp(body if isinstance(body, str) else json.dumps(body), url)

    def get_json(self, url, params=None):
        body = self._find(url, params)
        return json.loads(body) if isinstance(body, str) else body


def run(sources, routes, **kw):
    return seminar_scraper.scrape(sources, http=FakeHttp(routes), **kw)


# ---------------------------------------------------------------- parsing


class TestParsing(unittest.TestCase):
    ref = dt.date(2026, 10, 1)

    def test_dates(self):
        cases = {
            "Webinar on 7 October 2026": dt.date(2026, 10, 7),
            "Wednesday, 7th Oct": dt.date(2026, 10, 7),
            "November 12, 2026 at 3pm": dt.date(2026, 11, 12),
            "Join us 2026-11-28": dt.date(2026, 11, 28),
            "Webbinarium den 14 oktober kl. 15.00": dt.date(2026, 10, 14),
            "Infomøte 3. desember": dt.date(2026, 12, 3),
            "Info session 15 January": dt.date(2027, 1, 15),  # next January
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(find_date(text, self.ref)[0], want)

    def test_relative_dates_only_when_allowed(self):
        self.assertIsNone(find_date("webinar tomorrow", self.ref)[0])
        self.assertEqual(find_date("webinar tomorrow", self.ref, allow_relative=True)[0], dt.date(2026, 10, 2))

    def test_times(self):
        self.assertEqual(find_time("15:00–18:00 CEST"), ("15:00–18:00", "CEST"))
        self.assertEqual(find_time("at 3pm (CET)"), ("15:00", "CET"))
        self.assertEqual(find_time("from 3-5pm"), ("15:00–17:00", ""))
        self.assertEqual(find_time("kl. 15.00"), ("15:00", ""))
        self.assertEqual(find_time("on 2026-10-07"), ("", ""))  # a date is not a time

    def test_post_titles_drop_dates_and_times(self):
        cases = {
            "Webbinarium: Studera i Stockholm, 14 oktober kl. 15.00": "Webbinarium: Studera i Stockholm",
            "Live Q&A at 3pm on Oct 7 with our admissions team": "Live Q&A with our admissions team",
            "🎓 Online info session!\nJoin us on 7 October": "Online info session!",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(common.title_from_post(text), want)

    def test_social_posts_ignore_research_seminars(self):
        now = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(common.events_from_post("Research seminar on 12 October about glaciers", now,
                                                 "https://x", "o", "Bluesky", "Asia/Dhaka"), [])

    def test_localize_to_dhaka(self):
        d, t, label = common.localize(dt.datetime(2026, 11, 28, 10, 0, tzinfo=dt.timezone.utc), "Asia/Dhaka")
        self.assertEqual((d.isoformat(), t, label), ("2026-11-28", "16:00", "UTC+6"))

    def test_source_type_guessing(self):
        self.assertEqual(source_type({"url": "https://www.youtube.com/@lunduniversity"}), "youtube")
        self.assertEqual(source_type({"url": "https://bsky.app/profile/x.bsky.social"}), "bluesky")
        self.assertEqual(source_type({"url": "https://www.lu.se/events"}), "web")
        self.assertEqual(source_type({"type": "Mastodon", "tag": "x"}), "mastodon")


# ---------------------------------------------------------------- websites


class TestWeb(unittest.TestCase):
    def test_jsonld_converts_time_zone(self):
        start = dt.datetime.combine(SOON, dt.time(11, 0), tzinfo=dt.timezone(dt.timedelta(hours=1)))
        html = f"""<html><head><script type="application/ld+json">
        {{"@context":"https://schema.org","@type":"Event","name":"Study in Sweden Online Fair",
          "startDate":"{start.isoformat()}","eventAttendanceMode":"https://schema.org/OnlineEventAttendanceMode",
          "url":"/fair","organizer":{{"name":"Study in Sweden"}}}}
        </script></head><body></body></html>"""
        sems, errs, _ = run([{"type": "web", "url": "https://example.se/events"}],
                            [("example.se/events", html)])
        self.assertEqual(errs, [])
        self.assertEqual(len(sems), 1)
        s = sems[0]
        self.assertEqual((s["date"], s["time"], s["timezone"], s["mode"]), (SOON.isoformat(), "16:00", "UTC+6", "Online"))
        self.assertEqual(s["url"], "https://example.se/fair")
        self.assertEqual(s["notes"], "")

    def test_text_listing(self):
        html = f"""<html><body><main><h2>Upcoming webinars</h2><ul>
          <li><a href="/apply">Get Ready to Apply for Master's Studies</a> {long_date(SOON)}, 10:00–11:00 CEST</li>
          <li>Alumni panel {long_date(LATER)}</li>
        </ul><p>Our office is open 9 to 5.</p></main></body></html>"""
        sems, _, _ = run([{"url": "https://uni.example/events", "organizer": "Uni", "related_entry_ids": ["A"]}],
                         [("uni.example/events", html)])
        titles = {s["title"]: s for s in sems}
        self.assertIn("Get Ready to Apply for Master's Studies", titles)
        first = titles["Get Ready to Apply for Master's Studies"]
        self.assertEqual((first["date"], first["time"], first["timezone"]), (SOON.isoformat(), "10:00–11:00", "CEST"))
        self.assertEqual(first["related_entry_ids"], ["A"])
        self.assertEqual(first["organizer"], "Uni")
        self.assertIn("text matching", first["notes"])
        self.assertEqual(len(sems), 2)

    def test_ics_with_tzid(self):
        stamp = SOON.strftime("%Y%m%d")
        ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Info session\\, master's\r\n"
               f"DTSTART;TZID=Europe/Stockholm:{stamp}T150000\r\nLOCATION:Zoom\r\n"
               "URL:https://uni.example/info\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        sems, _, _ = run([{"url": "https://uni.example/cal.ics"}], [("cal.ics", ics)])
        self.assertEqual(len(sems), 1)
        self.assertEqual(sems[0]["title"], "Info session, master's")
        self.assertEqual(sems[0]["mode"], "Online")
        self.assertIn(sems[0]["time"], ("19:00", "20:00"))  # 15:00 Stockholm in Dhaka (summer/winter)

    def test_rss_feed(self):
        rss = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>
          <item><title>Webinar: Studying in Lund</title><link>https://uni.example/news/1</link>
            <description>Join our webinar on {long_date(SOON)} at 14:00 CET.</description>
            <pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
          <item><title>New research on bees</title><link>https://uni.example/news/2</link>
            <description>Published {long_date(SOON)}.</description></item>
        </channel></rss>"""
        sems, _, _ = run([{"url": "https://uni.example/feed.xml"}], [("feed.xml", rss)])
        self.assertEqual([s["title"] for s in sems], ["Webinar: Studying in Lund"])
        self.assertEqual(sems[0]["time"], "14:00")

    def test_site_error_is_reported_not_fatal(self):
        sems, errs, checked = run(
            [{"url": "https://blocked.example/"}, {"url": "https://ok.example/"}],
            [("blocked.example", RuntimeError("the site refused the request (HTTP 403)")),
             ("ok.example", "<html><body><p>nothing here</p></body></html>")])
        self.assertEqual(sems, [])
        self.assertEqual(len(errs), 1)
        self.assertIn("403", errs[0]["error"])
        self.assertEqual([c["status"] for c in checked], ["error", "ok"])


# ---------------------------------------------------------------- social media


class TestYouTube(unittest.TestCase):
    def routes(self):
        start = NOW + dt.timedelta(days=5)
        return [
            ("channels?forHandle=@lunduniversity", {"items": [{
                "id": "UC" + "a" * 22, "snippet": {"title": "Lund University"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "a" * 22}}}]}),
            ("search?", {"items": [{"id": {"videoId": "live1"}}]}),
            ("playlistItems?", {"items": [{"contentDetails": {"videoId": "vid2"}},
                                          {"contentDetails": {"videoId": "vid3"}}]}),
            ("videos?", {"items": [
                {"id": "live1", "snippet": {"title": "Live Q&A: Master's admissions", "liveBroadcastContent": "upcoming"},
                 "liveStreamingDetails": {"scheduledStartTime": iso(start)}},
                {"id": "vid2", "snippet": {"title": "Webinar recording: How to apply", "publishedAt": iso(NOW),
                                           "description": f"Our next webinar is on {long_date(LATER)} at 13:00 CET."}},
                {"id": "vid3", "snippet": {"title": "Campus tour", "publishedAt": iso(NOW), "description": "Enjoy!"}},
            ]}),
        ]

    def test_without_key_is_skipped(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}):
            sems, errs, checked = run([{"type": "youtube", "handle": "@lunduniversity"}], self.routes())
        self.assertEqual(sems, [])
        self.assertIn("YOUTUBE_API_KEY", errs[0]["error"])
        self.assertEqual(checked[0]["status"], "skipped (no YouTube key)")

    def test_with_key(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "secret123"}):
            sems, errs, _ = run([{"type": "youtube", "handle": "@lunduniversity", "related_entry_ids": ["X"]}],
                                self.routes())
        self.assertEqual(errs, [])
        by_title = {s["title"]: s for s in sems}
        live = by_title["Live Q&A: Master's admissions"]
        self.assertEqual(live["url"], "https://www.youtube.com/watch?v=live1")
        self.assertEqual(live["location"], "YouTube Live")
        self.assertEqual(live["timezone"], "UTC+6")
        self.assertEqual(live["notes"], "")
        self.assertEqual(live["organizer"], "Lund University")
        announced = by_title["Webinar recording: How to apply"]
        self.assertEqual(announced["date"], LATER.isoformat())
        self.assertIn("YouTube video description", announced["notes"])
        self.assertNotIn("Campus tour", by_title)

    def test_key_never_leaks_into_errors(self):
        routes = [("channels?", RuntimeError("boom for url https://x/channels?key=secret123&id=1"))]
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "secret123"}):
            _, errs, _ = run([{"type": "youtube", "handle": "@x"}], routes)
        self.assertNotIn("secret123", json.dumps(errs))


class TestBluesky(unittest.TestCase):
    def test_posts(self):
        feed = {"feed": [
            {"post": {"uri": "at://did:plc:1/app.bsky.feed.post/abc", "author": {"handle": "studyemmir.bsky.social",
                                                                                   "displayName": "EMMIR"},
                      "record": {"text": f"📣 Online info session for EMMIR applicants!\nJoin us on {long_date(SOON)}, "
                                         "14:00 CET. Register below #ErasmusMundus",
                                 "createdAt": iso(NOW),
                                 "facets": [{"features": [{"$type": "app.bsky.richtext.facet#link",
                                                           "uri": "https://emmir.org/register"}]}]}}},
            {"post": {"uri": "at://did:plc:1/app.bsky.feed.post/def", "author": {"handle": "studyemmir.bsky.social"},
                      "record": {"text": "Congratulations to our graduates!", "createdAt": iso(NOW)}}},
            {"post": {"uri": "at://did:plc:1/app.bsky.feed.post/old", "author": {"handle": "studyemmir.bsky.social"},
                      "record": {"text": "Webinar tomorrow at 10:00!", "createdAt": iso(NOW - dt.timedelta(days=200))}}},
            {"post": {"uri": "at://did:plc:1/app.bsky.feed.post/tom", "author": {"handle": "studyemmir.bsky.social"},
                      "record": {"text": "Reminder: our webinar is tomorrow at 10:00 CET", "createdAt": iso(NOW)}}},
        ]}
        sems, errs, _ = run([{"type": "bluesky", "handle": "studyemmir.bsky.social", "related_entry_ids": ["EM03"]}],
                            [("getAuthorFeed", feed)])
        self.assertEqual(errs, [])
        self.assertEqual(len(sems), 2, sems)
        info = next(s for s in sems if s["date"] == SOON.isoformat())
        self.assertEqual(info["title"], "Online info session for EMMIR applicants!")
        self.assertEqual(info["url"], "https://emmir.org/register")
        self.assertEqual((info["time"], info["timezone"], info["mode"]), ("14:00", "CET", "Online"))
        self.assertIn("bsky.app/profile/studyemmir.bsky.social/post/abc", info["notes"])
        tomorrow = next(s for s in sems if s is not info)
        self.assertEqual(tomorrow["date"], (NOW.date() + dt.timedelta(days=1)).isoformat())


class TestMastodon(unittest.TestCase):
    def status(self, html, days_ago=0, reblog=None):
        return {"created_at": iso(NOW - dt.timedelta(days=days_ago)), "content": html,
                "url": "https://respublicae.eu/@EUErasmusPlus/1", "account": {"display_name": "Erasmus+"},
                "card": None, "reblog": reblog}

    def test_account(self):
        statuses = [
            self.status(f"<p>Erasmus Mundus webinar for future students on {long_date(SOON)} at 11:00 CET "
                        '<a href="https://erasmus-plus.ec.europa.eu/webinar">register</a> '
                        '<a class="mention" href="https://x/@y">@y</a></p>'),
            self.status("<p>Apply now for 2027 scholarships!</p>"),
        ]
        sems, errs, _ = run([{"type": "mastodon", "account": "EUErasmusPlus@respublicae.eu"}],
                            [("accounts/lookup", {"id": "42", "display_name": "Erasmus+"}),
                             ("accounts/42/statuses", statuses)])
        self.assertEqual(errs, [])
        self.assertEqual(len(sems), 1)
        self.assertEqual(sems[0]["url"], "https://erasmus-plus.ec.europa.eu/webinar")
        self.assertEqual(sems[0]["date"], SOON.isoformat())

    def test_tag_with_reblog(self):
        inner = self.status(f"<p>Study in Sweden online fair: Asia-Pacific<br>{long_date(LATER)}, 11:00–14:00 CET</p>")
        inner["account"] = {"acct": "studyinsweden@example.social", "display_name": "Study in Sweden"}
        sems, _, checked = run([{"type": "mastodon", "tag": "#StudyInSweden"}],
                               [("timelines/tag/StudyInSweden", [self.status("", reblog=inner)])])
        self.assertEqual(len(sems), 1)
        self.assertEqual(sems[0]["title"], "Study in Sweden online fair: Asia-Pacific")
        self.assertEqual(sems[0]["organizer"], "Study in Sweden")
        self.assertEqual(checked[0]["source"], "Mastodon #StudyInSweden")


# ---------------------------------------------------------------- whole run


class TestEndToEnd(unittest.TestCase):
    def test_duplicates_merge_and_prefer_exact_data(self):
        start = dt.datetime.combine(SOON, dt.time(9, 0), tzinfo=dt.timezone.utc)
        jsonld = f"""<script type="application/ld+json">{{"@type":"Event","name":"Online Fair: Asia-Pacific",
            "startDate":"{start.isoformat()}"}}</script>"""
        feed = {"feed": [{"post": {"uri": "at://x/app.bsky.feed.post/1", "author": {"handle": "a.bsky.social"},
                                   "record": {"text": f"Online Fair: Asia-Pacific\n{long_date(SOON)}",
                                              "createdAt": iso(NOW)}}}]}
        sems, _, _ = run([{"type": "bluesky", "handle": "a.bsky.social", "related_entry_ids": ["B"]},
                          {"url": "https://fair.example/", "related_entry_ids": ["A"]}],
                         [("getAuthorFeed", feed), ("fair.example", jsonld)])
        self.assertEqual(len(sems), 1)
        self.assertEqual(sems[0]["related_entry_ids"], ["A", "B"])
        self.assertEqual(sems[0]["notes"], "")  # the exact (JSON-LD) copy won

    def test_output_matches_dashboard_import_format(self):
        """The dashboard keeps seminars that have a title and a YYYY-MM-DD date,
        and only https links."""
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text(f"<ul><li>Webinar: Apply to Lund {long_date(SOON)}</li></ul>", encoding="utf-8")
            srcs = Path(tmp) / "sources.json"
            srcs.write_text(json.dumps({"sources": [{"_comment": "x"}, {"url": str(page)},
                                                    {"url": "https://off.example", "enabled": False}]}))
            out = Path(tmp) / "out.json"
            with mock.patch("builtins.print"):
                seminar_scraper.main(["--sources", str(srcs), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"scraped_at", "timezone", "seminars", "errors", "checked"})
        self.assertEqual(len(data["seminars"]), 1)
        s = data["seminars"][0]
        for key in ("id", "title", "organizer", "date", "time", "timezone", "mode", "location", "url",
                    "related_entry_ids", "source", "notes", "registered"):
            self.assertIn(key, s)
        self.assertRegex(s["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(s["url"] is None or s["url"].startswith("https://"))

    def test_real_sources_file_is_valid(self):
        path = Path(__file__).resolve().parents[1] / "scraper_sources.json"
        sources = seminar_scraper.load_sources([path])
        self.assertGreater(len(sources), 10)
        kinds = {source_type(s) for s in sources}
        self.assertEqual(kinds, {"web", "youtube", "bluesky", "mastodon"})
        ids = json.loads((path.parents[1] / "data" / "scholarships.json").read_text(encoding="utf-8"))
        known = {e["scholarship_entry"]["tracker_meta"]["entry_id"] for e in ids}
        for s in sources:
            for rid in s.get("related_entry_ids", []):
                self.assertIn(rid, known, f"{rid} in {s}")


if __name__ == "__main__":
    unittest.main()
