# Scholly — Scholarship Tracker

A dashboard for tracking master's scholarships and the seminars that go with them.
It focuses on full-ride scholarships for master's study in Scandinavia.

A scraper collects webinars, info sessions and study fairs.
It checks university websites, YouTube, Bluesky and Mastodon.

## What's in this repository

```
index.html                         The dashboard. Open it in any browser.
data/
  scholarships.json                All scholarship entries, in the schema format.
  seminars.json                    The hand-checked seminar list (1 October 2026).
  seminars_scraped.json            What the scraper found last time. Made by GitHub.
  scrape_log.txt                   The log of the last scraper run on GitHub.
schema/
  scholarship_schema.json          The original tracker specification.
scraper/
  seminar_scraper.py               Run this to collect seminars.
  scraper_sources.json             The websites and accounts the scraper checks.
  requirements.txt                 The Python packages the scraper needs.
  collectors/                      One file per kind of source.
    web.py                         Websites.
    youtube.py                     YouTube channels.
    bluesky.py                     Bluesky accounts.
    mastodon.py                    Mastodon accounts and hashtags.
    common.py                      Shared date, time and text helpers.
  tests/                           Offline tests for the scraper.
.github/workflows/
  scrape-seminars.yml              Runs the scraper on GitHub every Monday.
```

## Using the dashboard

Double-click `index.html`. It opens in your browser. No install is needed.

Your changes are saved in that browser. They stay when you close and reopen the file.
They do not move to another browser or computer by themselves.

To back up your data, click **Export JSON**. To load data, click **Import JSON**.
Import accepts scholarship files and seminar files.

The online version of the dashboard on claude.ai saves changes into the page itself.
The local file and the online page do not sync. Use Export and Import to move data between them.

## Getting new seminars

There are two ways. The easy way needs no setup on your computer.

### The easy way: let GitHub run it

GitHub runs the scraper every Monday at 09:00 Dhaka time.
It also runs when the scraper or its sources change.
The result is saved as `data/seminars_scraped.json`.

1. Open `data/seminars_scraped.json` on GitHub.
2. Click the download button (**Download raw file**).
3. In the dashboard, open the **Seminars** tab.
4. Click **Import scraped seminars** and choose the file.

Seminars already on your list are skipped.

To run it now, open the **Actions** tab. Choose **Scrape seminars**. Click **Run workflow**.

### On your own computer

You need Python 3.9 or newer.

1. Open a terminal in the `scraper` folder.
2. Install the packages once:
   `pip install -r requirements.txt`
3. Run the scraper:
   `python seminar_scraper.py --out seminars.json`
4. Import `seminars.json` in the dashboard, as above.

Useful options:

- `--verbose` shows how many events each source gave.
- `--only web,bluesky` checks only some kinds of source.
- `--days 90` keeps only events in the next 90 days.
- `--social-days 60` reads only posts from the last 60 days.
- `--timezone Europe/Stockholm` shows times in another time zone. The default is Dhaka.
- `--include-past` keeps events that already happened.
- `--sources my_file.json` uses another sources file. You can repeat it to use several.

## How the scraper finds events

### Websites

It tries four methods, from most to least reliable.

1. Event data that the website publishes in a standard format.
2. Calendar files (`.ics`) linked from the page.
3. News feeds (RSS or Atom), when the source link is a feed.
4. Text on the page that mentions an event word and a date.

It reads English, Swedish, Norwegian and Danish month names and event words.

### YouTube

It finds scheduled live streams and premieres, with their exact start time.
It also finds recent videos whose description announces a webinar with a date.

YouTube needs a free API key. YouTube's rules do not allow scripts to read its pages without one.
Without a key, YouTube channels are skipped. Everything else still runs.

How to get a key:

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in.
2. Create a project. Any name works.
3. Open **APIs & Services > Library**. Search for **YouTube Data API v3**. Click **Enable**.
4. Open **APIs & Services > Credentials**. Click **Create credentials > API key**.
5. Copy the key.

To use it on GitHub, open the repository's **Settings > Secrets and variables > Actions**.
Click **New repository secret**. Name it `YOUTUBE_API_KEY` and paste the key.

To use it on your computer, set it before you run the scraper.
On Windows: `set YOUTUBE_API_KEY=your-key`. On Mac or Linux: `export YOUTUBE_API_KEY=your-key`.

The free quota is 10,000 units a day. One run uses about 100 units per channel.

### Bluesky and Mastodon

The scraper reads recent public posts through each network's open API. No login is needed.
It keeps a post only when it names a study event (such as a webinar, info session or study fair)
and a date that is still to come. It also understands "today" and "tomorrow".

For Mastodon, a source can be an account or a hashtag.
Hashtags such as `#ErasmusMundus` find posts from many accounts.

Events found in posts or page text get a note that says to check them.
Always check the date and time on the event page before you register.

### Times and time zones

Times from YouTube, calendar files and event data are shown in Dhaka time (for example `UTC+6`).
Times written in a post or on a page are kept as written (for example `14:00 CET`).

### What it does not check

Facebook, Instagram, LinkedIn and X do not allow scripts to read them without a login.
Their rules forbid it, so the scraper does not try. Check those pages by hand.

Some websites also block automated visitors. si.se (the Swedish Institute) is one of them.
The scraper respects each site's `robots.txt` rules and waits between requests.
Pages it could not read appear in the `errors` list of the output file. Check them by hand.

## Adding a source

Open `scraper/scraper_sources.json`. Add an entry to the list. Examples:

```json
{"type": "web", "url": "https://www.example.se/events", "organizer": "Example University",
 "related_entry_ids": ["SCH-2026-SE01"]}

{"type": "youtube", "handle": "@lunduniversity", "organizer": "Lund University"}

{"type": "bluesky", "handle": "studyemmir.bsky.social"}

{"type": "mastodon", "account": "EUErasmusPlus@respublicae.eu"}

{"type": "mastodon", "tag": "StudyInSweden"}
```

`related_entry_ids` links the seminars to scholarships in the dashboard. It is optional.
To turn a source off without deleting it, add `"enabled": false`.

The dashboard's **Download scraper sources** button makes a list of scholarship pages.
You can pass that file with `--sources` too.

## Testing the scraper

From the repository's main folder, run:

`python -m unittest discover -s scraper/tests -v`

The tests use made-up pages and posts. They need no internet. GitHub runs them before each scrape.

## About the data

Each entry follows the schema in `schema/scholarship_schema.json`.
Unverified values are left empty and show as "Unspecified".
Each entry's notes say what is confirmed and what is projected.

Some 2027 dates are projected from the 2026 cycle. Confirm them on the official pages.

The Erasmus Mundus entries are full rides only if you win the Erasmus Mundus scholarship.
Self-funded places are not full rides.

The BI-Luiss entry covers full tuition only. It is not a full ride.
Use the **Full ride only** filter to hide it.

The dashboard adds two things the schema doesn't have:

- `_progress.docs_ready` records which documents you've marked as ready.
- A separate seminar list, with title, date, time, organizer, link and related scholarships.
