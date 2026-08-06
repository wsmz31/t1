"""
N9 2026 election, YouTube output benchmark
==========================================

Pulls every election video published by a set of Malaysian publishers inside a
date window, and writes a per-video CSV plus a per-publisher summary CSV.

Window default: 16 Jul 2026 to 2 Aug 2026 inclusive, Malaysia time (UTC+8).

WHY THIS EXISTS
Scraping YouTube pages gives you relative dates ("5 days ago", "2 weeks ago").
"2 weeks ago" is a seven-day bucket, so any video near a window edge is
unplaceable, and channel search silently misses videos (querying Buletin TV3
for "PRNNS2026" returned 25 videos that "negeri sembilan" did not). This script
uses the YouTube Data API v3 instead, which returns exact ISO-8601 publish
timestamps and exact view counts, and pages through every result.

SETUP
1.  pip install google-api-python-client pandas openpyxl
2.  Get a free API key:
    console.cloud.google.com -> new project -> APIs & Services -> Library ->
    "YouTube Data API v3" -> Enable -> Credentials -> Create credentials ->
    API key. No billing card needed.
3.  Put the key in an environment variable, do not paste it into this file:
       macOS / Linux:  export YOUTUBE_API_KEY="AIza..."
       Windows PowerShell:  $env:YOUTUBE_API_KEY="AIza..."
4.  python v1_youtube_scraper_cursor.py

QUOTA
Default allowance is 10,000 units a day. search.list costs 100 units a call,
videos.list costs 1. This script does roughly (channels x keywords) searches.
With 11 channels and 6 keywords that is about 66 searches, 6,600 units, so it
fits inside one day but will not run twice. Trim KEYWORDS if you hit the cap.
The script prints its running quota estimate so you can see it coming.

REUSE
For GE16, change WINDOW_START, WINDOW_END and KEYWORDS. The channel list stays.
"""

import os
import re
import csv
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    sys.exit("Run: pip install google-api-python-client pandas openpyxl")


# ----------------------------------------------------------------------------
# CONFIG, edit this block
# ----------------------------------------------------------------------------

API_KEY = os.environ.get("YOUTUBE_API_KEY")

MYT = timezone(timedelta(hours=8))          # Malaysia time
WINDOW_START = datetime(2026, 7, 16, 0, 0, 0, tzinfo=MYT)
WINDOW_END = datetime(2026, 8, 2, 23, 59, 59, tzinfo=MYT)

# Channel IDs are stable, handles are not. These were verified 6 Aug 2026.
CHANNELS = {
    "The Star":                 "UCpWvshQVx1d7BqCsPnVuNIw",
    "Free Malaysia Today":      "UC2CzLwbhTiI8pTKNVyrOnJQ",
    "Malaysiakini / KiniTV":    "UC9ULnkwvyofZ_sWrzRpyvrg",
    "New Straits Times":        "UCsWp7U58TZM8uOYtRrAqWhg",
    "Berita Harian":            "UCoV8oT-GyqYSJkxzuAKG3Jw",
    "Astro Awani":              "UC5dYmq91e5_g54krpO06NJw",
    "Sinar Harian":             "UCY4WxCgJjIJghSQifLSs3yg",
    "Sin Chew (Pocketimes)":    "UCKC1q1zc_CG2MtOCEHNUt1Q",
    "Sin Chew Daily":           "UCE9WjVaCnXfjCLbp_WvH0NQ",
    "Nanyang / eNanyang":       "UCNUK8WgzrwdSfdR-wqSc1uw",
    "Buletin TV3 (Media Prima)": "UC2p8wkJVSjsMsRv0MjTikgA",
}
# MyUndi has no YouTube channel. It is a web-only results portal.

# Cast wide. Recall matters more than precision here, the date filter and the
# relevance filter below do the tightening.
KEYWORDS = [
    "negeri sembilan",
    "negri",
    "PRN",
    "pilihan raya",
    "森美兰",        # Negeri Sembilan, Chinese
    "州选",          # state election, Chinese
]

# A video is kept only if its title or description matches one of these.
# Widen if you find the filter dropping real coverage.
RELEVANCE = re.compile(
    r"negeri sembilan|negri sembilan|\bn9\b|n\.sembilan|n sembilan|"
    r"prn\s*n|prnns|prnn9|state polls|state election|pilihan raya negeri|"
    r"森美兰|森州",
    re.IGNORECASE,
)

# Titles matching this are dropped as belonging to a different state's polls.
EXCLUDE = re.compile(r"\bjohor\b|\bmelaka\b|\bsabah\b|柔佛|马六甲|沙巴", re.IGNORECASE)

OUT_VIDEOS = "n9_youtube_videos.csv"
OUT_SUMMARY = "n9_youtube_summary.csv"

VIDEO_FIELDS = [
    "publisher", "title", "url", "published_myt", "views", "likes",
    "comments", "language", "format", "duration_s", "in_window",
]
SUMMARY_FIELDS = [
    "publisher", "videos", "bm", "en", "cn", "views", "shorts", "shorts_views",
]


# ----------------------------------------------------------------------------

_quota = 0


def iso8601_seconds(duration: str) -> int:
    """PT1M32S -> 92. YouTube returns durations in ISO-8601."""
    m = re.match(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def classify_language(title: str, description: str, snippet_lang: str) -> str:
    """BM / EN / CN. Script detection first, then keyword heuristics."""
    text = f"{title} {description}"
    if re.search(r"[一-鿿]", text):
        return "CN"
    bm_markers = re.compile(
        r"\b(yang|dan|untuk|kepada|dengan|akan|tidak|calon|pengundi|mengundi|"
        r"kerusi|parti|keputusan|pilihan raya|hari|negeri|jawatan|rakyat|"
        r"kempen|sokongan|kerajaan|menteri besar|pusat|undi|peratus|dalam|"
        r"selepas|semua|beliau|katanya|langsung|terkini|penamaan)\b",
        re.IGNORECASE,
    )
    hits = len(bm_markers.findall(title))
    if hits >= 2:
        return "BM"
    if (snippet_lang or "").lower().startswith("ms"):
        return "BM"
    if hits == 1 and len(bm_markers.findall(description)) >= 3:
        return "BM"
    return "EN"


def search_channel(youtube, channel_id: str, keyword: str) -> set:
    """Return video IDs for one channel and keyword inside the window."""
    global _quota
    ids, page = set(), None
    while True:
        try:
            resp = youtube.search().list(
                part="id",
                channelId=channel_id,
                q=keyword,
                type="video",
                maxResults=50,
                order="date",
                publishedAfter=WINDOW_START.astimezone(timezone.utc)
                                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
                publishedBefore=WINDOW_END.astimezone(timezone.utc)
                                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
                pageToken=page,
            ).execute()
        except HttpError as e:
            if "quotaExceeded" in str(e):
                sys.exit("\nQUOTA EXCEEDED. Trim KEYWORDS or wait for the "
                         "daily reset (midnight Pacific).")
            print(f"    ! {keyword}: {e}")
            return ids
        _quota += 100
        ids.update(i["id"]["videoId"] for i in resp.get("items", []))
        page = resp.get("nextPageToken")
        if not page:
            return ids
        time.sleep(0.1)


def fetch_details(youtube, video_ids: list) -> list:
    """videos.list in batches of 50. Costs 1 unit a call."""
    global _quota
    out = []
    for i in range(0, len(video_ids), 50):
        resp = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids[i:i + 50]),
        ).execute()
        _quota += 1
        out.extend(resp.get("items", []))
    return out


def main():
    if not API_KEY:
        sys.exit("Set YOUTUBE_API_KEY first. See the SETUP note at the top.")

    youtube = build("youtube", "v3", developerKey=API_KEY)
    rows = []

    for publisher, channel_id in CHANNELS.items():
        if not channel_id:
            print(f"\n{publisher}: SKIPPED, no channel ID set")
            rows.append({"publisher": publisher, "title": "NO CHANNEL ID SET",
                         "url": "", "published_myt": "", "views": 0,
                         "likes": 0, "comments": 0,
                         "language": "", "format": "", "duration_s": 0,
                         "in_window": ""})
            continue

        print(f"\n{publisher}")
        found = set()
        for kw in KEYWORDS:
            got = search_channel(youtube, channel_id, kw)
            found |= got
            print(f"    {kw:20s} {len(got):3d} hits   (quota ~{_quota})")

        if not found:
            print("    nothing found")
            continue

        for v in fetch_details(youtube, sorted(found)):
            sn, st, cd = v["snippet"], v.get("statistics", {}), v["contentDetails"]
            title, desc = sn["title"], sn.get("description", "")

            if not RELEVANCE.search(f"{title} {desc}"):
                continue
            if EXCLUDE.search(title) and not RELEVANCE.search(title):
                continue

            published = datetime.fromisoformat(
                sn["publishedAt"].replace("Z", "+00:00")).astimezone(MYT)
            secs = iso8601_seconds(cd.get("duration"))

            rows.append({
                "publisher": publisher,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "published_myt": published.strftime("%Y-%m-%d %H:%M"),
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "language": classify_language(
                    title, desc, sn.get("defaultAudioLanguage", "")),
                "format": "SHORT" if secs <= 60 else "VIDEO",
                "duration_s": secs,
                "in_window": WINDOW_START <= published <= WINDOW_END,
            })
        print(f"    kept {sum(1 for r in rows if r['publisher'] == publisher)}")

    rows = [r for r in rows if r.get("in_window") is not False]

    with open(OUT_VIDEOS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=VIDEO_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    summary = {}
    for r in rows:
        if r.get("title") == "NO CHANNEL ID SET":
            continue
        p = r["publisher"]
        s = summary.setdefault(p, {"publisher": p, "videos": 0, "bm": 0, "en": 0,
                                   "cn": 0, "views": 0, "shorts": 0,
                                   "shorts_views": 0})
        s["videos"] += 1
        s["views"] += r["views"]
        lang = (r.get("language") or "").lower()
        if lang in ("bm", "en", "cn"):
            s[lang] = s.get(lang, 0) + 1
        if r["format"] == "SHORT":
            s["shorts"] += 1
            s["shorts_views"] += r["views"]

    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        if summary:
            w.writerows(sorted(summary.values(), key=lambda x: -x["views"]))

    print(f"\n{'-' * 62}")
    print(f"{len([r for r in rows if r.get('title') != 'NO CHANNEL ID SET'])} "
          f"videos across {len(summary)} publishers")
    print(f"Quota used, approximately {_quota} of 10,000")
    print(f"Written: {OUT_VIDEOS}, {OUT_SUMMARY}")
    print(f"{'-' * 62}")
    print(f"{'Publisher':28s} {'Vids':>5s} {'BM':>4s} {'Views':>10s} {'Shorts':>7s}")
    for s in sorted(summary.values(), key=lambda x: -x["views"]):
        print(f"{s['publisher']:28s} {s['videos']:5d} {s['bm']:4d} "
              f"{s['views']:10,d} {s['shorts']:7d}")


if __name__ == "__main__":
    main()
