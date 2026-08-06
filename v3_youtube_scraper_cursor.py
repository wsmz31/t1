"""
Election YouTube benchmark, no API key required        v3
=========================================================

WHAT CHANGED FROM THE RUN YOU JUST DID, AND WHY

Your v1 output had 916 rows but three defects. All three are fixed here.

1. LIVESTREAMS WERE MISSING ENTIRELY, and they are the highest-reach items
   in the whole dataset. The script walked /videos and /shorts but not
   /streams. That silently dropped KiniTV's results livestream (404K views),
   Sinar Harian's rolling results stream (277K) and every other LANGSUNG /
   SINAR LIVE / LIVE bulletin. Sinar Harian came out at 3,000 views against a
   true figure near 840,000. FIX: walk /streams as well.

2. THE RELEVANCE FILTER DROPPED "Negri polls:", which is The Star's house
   spelling and the prefix on most of its election clips. The pattern matched
   "negri sembilan" but not bare "negri". That is why The Star returned 5 rows
   instead of 25. FIX: match \\bnegri\\b on its own.

3. DATES WERE DATE-ONLY AND VIEWS WENT MISSING. Every one of the 916 rows
   carried the clock time 12:00, which means upload_date was used instead of
   the exact timestamp, so day assignment is off by one at window edges.
   Separately 36% of rows came back with views = 0, which is missing data
   rather than zero views (93% of FMT rows, 93% of Sinar Harian).
   FIX: require the exact epoch timestamp, and take the view count from the
   channel listing and the watch page, using whichever actually returned.

Also added: mStar, which carries all of The Star's Bahasa Malaysia election
video and was absent from the channel list.

The script now prints a DATA QUALITY report at the end. If it shows missing
views or missing timestamps, the run is not clean and the totals understate.
Do not use the CSV until that report is clean.

SETUP
  pip install yt-dlp
  python3 -u v3_youtube_scraper_cursor.py

If YouTube bot-checks this host, put a Netscape cookies file at
  ./youtube_cookies.txt
or set COOKIES_FROM_BROWSER = "chrome" (local browser profile).

RUNTIME
Walking three tabs per channel is slower than two. Budget 5 to 10 minutes a
channel, so roughly an hour for the full list. The -u flag keeps progress
printing live.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("Run: pip install yt-dlp")


# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

MYT = timezone(timedelta(hours=8))
WINDOW_START = datetime(2026, 7, 16, 0, 0, 0, tzinfo=MYT)
WINDOW_END = datetime(2026, 8, 2, 23, 59, 59, tzinfo=MYT)

CHANNELS = {
    "The Star":                  "UCpWvshQVx1d7BqCsPnVuNIw",
    "mStar":                     "UCvGK5qm1S-cjzV2YSmMmgFg",
    "Free Malaysia Today":       "UC2CzLwbhTiI8pTKNVyrOnJQ",
    "Malaysiakini / KiniTV":     "UC9ULnkwvyofZ_sWrzRpyvrg",
    "New Straits Times":         "UCsWp7U58TZM8uOYtRrAqWhg",
    "Berita Harian":             "UCoV8oT-GyqYSJkxzuAKG3Jw",
    "Astro Awani":               "UC5dYmq91e5_g54krpO06NJw",
    "Sinar Harian":              "UCY4WxCgJjIJghSQifLSs3yg",
    "Sin Chew (Pocketimes)":     "UCKC1q1zc_CG2MtOCEHNUt1Q",
    "Sin Chew Daily":            "UCE9WjVaCnXfjCLbp_WvH0NQ",
    "Nanyang / eNanyang":        "UCNUK8WgzrwdSfdR-wqSc1uw",
    # Verified via @BuletinTV3 (UCJ7o… in an earlier draft does not exist).
    "Buletin TV3 (Media Prima)": "UC2p8wkJVSjsMsRv0MjTikgA",
}
# MyUndi has no YouTube channel, it is a web-only results portal.
# Verify a channel ID with:
#   yt-dlp --print "%(channel_id)s" --playlist-end 1 "https://youtube.com/@HANDLE"

# FIX 1: /streams carries the livestream VODs, which are the biggest single
# items in election coverage. Omitting it is what broke the v1 view totals.
TABS = ("videos", "shorts", "streams")

MAX_VIDEOS_PER_TAB = 1200

# Stop reading a newest-first tab after this many consecutive videos older
# than WINDOW_START. Keeps runtime sane on busy news channels.
OLD_STREAK_STOP = 25

# FIX 2: \bnegri\b on its own. The Star writes "Negri polls:", never
# "Negeri Sembilan", on most clips. "Negri" is only ever used for Negeri
# Sembilan in the Malaysian press, so it does not pull in Johor or Melaka.
RELEVANCE = re.compile(
    # no-space variants matter on TikTok, where hashtags run words together
    r"\bnegri\b|negeri ?sembilan|negrisembilan|\bn9\b|n\.\s*sembilan|"
    r"\bn sembilan\b|prn\s*n|prnns|prnn9|state polls|state election|"
    r"pilihan ?raya ?negeri|"
    # N9 seat and district names. Bare "nilai"/"labu" are also common Malay
    # words, so only match them in seat/election context (DUN Labu, N.10 Nilai).
    r"seremban|chennah|rantau|linggi|paroi|sikamat|ampangan|jelebu|rembau|"
    r"juasseh|repah|temiang|lukut|chuah|pertang|klawang|gemas|"
    r"(?:dun|n\.\s*\d+|kerusi|calon|pengundi)\s+(?:labu|nilai)|"
    r"(?:labu|nilai)\s+(?:dun|kerusi|calon)|"
    r"森美兰|森州",
    re.IGNORECASE,
)

EXCLUDE = re.compile(r"\bjohor\b|\bmelaka\b|\bsabah\b|柔佛|马六甲|沙巴", re.IGNORECASE)

OUT_VIDEOS = "n9_youtube_videos.csv"
OUT_SUMMARY = "n9_youtube_summary.csv"

# If YouTube starts challenging yt-dlp, set to "chrome" / "firefox" / "edge".
# Close the browser first, it locks its own cookie database.
COOKIES_FROM_BROWSER = None

# Optional Netscape cookies file (preferred on bot-checked hosts).
COOKIES_FILE = os.environ.get("YOUTUBE_COOKIES", "youtube_cookies.txt")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ----------------------------------------------------------------------------

def _opts(**extra):
    o = {"quiet": True, "no_warnings": True, "ignoreerrors": True}
    if COOKIES_FROM_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    elif COOKIES_FILE and os.path.isfile(COOKIES_FILE):
        o["cookiefile"] = COOKIES_FILE
    if os.path.exists("/exec-daemon/node"):
        o["js_runtimes"] = {"node": {"path": "/exec-daemon/node"}}
    o.update(extra)
    return o


def classify_language(title: str, description: str) -> str:
    if re.search(r"[一-鿿]", f"{title} {description}"):
        return "CN"
    markers = re.compile(
        r"\b(yang|dan|untuk|kepada|dengan|akan|tidak|calon|pengundi|mengundi|"
        r"kerusi|parti|keputusan|pilihan raya|hari|negeri|jawatan|rakyat|"
        r"kempen|sokongan|kerajaan|menteri besar|pusat|undi|peratus|dalam|"
        r"selepas|semua|beliau|katanya|langsung|terkini|penamaan|tiba|sudah|"
        r"belum|bukan|antara|lagi|juga|mahu|boleh|seru|kekal|bawa)\b",
        re.IGNORECASE,
    )
    t = len(markers.findall(title))
    if t >= 2 or (t == 1 and len(markers.findall(description)) >= 3):
        return "BM"
    return "EN"


def list_channel(channel_id: str) -> dict:
    """Walk every tab, newest first.

    Returns {video_id: {views, title, duration, tab}}. Flat listings already
    carry view_count for most entries; keeping it lets us fill in where the
    watch page comes back empty (the 36% zero-view defect in v1).
    """
    found = {}
    for tab in TABS:
        url = f"https://www.youtube.com/channel/{channel_id}/{tab}"
        try:
            with YoutubeDL(_opts(extract_flat="in_playlist",
                                playlistend=MAX_VIDEOS_PER_TAB)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"    ! {tab:8s} failed: {str(e)[:70]}", flush=True)
            continue
        entries = [e for e in ((info or {}).get("entries") or []) if e and e.get("id")]
        for e in entries:
            vid = e["id"]
            prev = found.get(vid)
            vc = e.get("view_count")
            title = e.get("title") or ""
            if prev is None:
                found[vid] = {
                    "views": vc,
                    "title": title,
                    "duration": e.get("duration") or 0,
                    "tab": tab,
                    "description": e.get("description") or "",
                }
            else:
                if prev.get("views") is None and vc is not None:
                    prev["views"] = vc
                if not prev.get("title") and title:
                    prev["title"] = title
                if tab == "streams":
                    prev["tab"] = "streams"
        print(f"    {tab:8s} {len(entries):5d} listed", flush=True)
    return found


def fetch(video_id: str) -> dict | None:
    """yt-dlp watch extract. Returns None when bot-blocked / failed."""
    # Swallow yt-dlp's noisy "Sign in to confirm you're not a bot" lines;
    # the HTML fallback handles those hosts.
    opts = _opts(skip_download=True)
    opts["quiet"] = True
    opts["no_warnings"] = True
    opts["logger"] = type("L", (), {
        "debug": staticmethod(lambda *a, **k: None),
        "info": staticmethod(lambda *a, **k: None),
        "warning": staticmethod(lambda *a, **k: None),
        "error": staticmethod(lambda *a, **k: None),
    })()
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
        except Exception:
            return None
    return info


def parse_publish_date(text: str):
    if not text:
        return None
    text = text.strip()
    text = re.sub(
        r"^(premiered|streamed(?:\s+live)?(?:\s+on)?|published|joined)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            # Date-only fallback: noon MYT. Quality report flags these.
            return datetime.strptime(text, fmt).replace(
                hour=12, minute=0, second=0, tzinfo=MYT
            )
        except ValueError:
            continue
    return None


def fetch_watch_page(video_id: str) -> dict | None:
    """Fallback when yt-dlp is bot-blocked: calendar date + likes from HTML."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:
        return None

    published = None
    likes = 0
    comments = 0
    title = ""
    desc = ""
    views = None
    secs = 0
    was_live = False

    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        title = re.sub(r"\s*-\s*YouTube\s*$", "", m.group(1)).strip()

    m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html)
    if not m:
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});", html)
    if m:
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            data = None
        if data:
            published, likes, comments, title2, desc = _meta_from_initial(data)
            if title2:
                title = title2

    if published is None:
        m = re.search(
            r'"publishDate"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', html
        ) or re.search(
            r'"dateText"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', html
        )
        if m:
            published = parse_publish_date(m.group(1))
            if m.group(1).lower().startswith("streamed"):
                was_live = True

    m = re.search(r'"shortDescription":"(.*?)"(?:,|})', html)
    if m and not desc:
        desc = m.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")

    # viewCount sometimes present even when player microformat is empty
    m = re.search(r'"viewCount"\s*:\s*"(\d+)"', html)
    if m:
        views = int(m.group(1))
    else:
        m = re.search(r'"views"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', html)
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1))
            views = int(digits) if digits else None

    m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    if m:
        secs = int(m.group(1))

    if not published:
        return None

    return {
        "title": title,
        "description": desc,
        "timestamp": None,  # date-only fallback — no epoch available
        "release_timestamp": None,
        "published": published,
        "view_count": views,
        "like_count": likes,
        "comment_count": comments,
        "duration": secs,
        "webpage_url": url,
        "was_live": was_live,
        "is_live": False,
        "_date_only": True,
    }


def _meta_from_initial(data: dict):
    published = None
    likes = 0
    comments = 0
    title = ""
    desc = ""

    def walk(o):
        nonlocal published, likes, comments, title, desc
        if isinstance(o, dict):
            if not title and "videoPrimaryInfoRenderer" in o:
                t = (o["videoPrimaryInfoRenderer"].get("title") or {})
                title = t.get("simpleText") or "".join(
                    r.get("text", "") for r in t.get("runs", [])
                )
            if published is None:
                for key in ("publishDate", "dateText"):
                    node = o.get(key)
                    if isinstance(node, dict) and node.get("simpleText"):
                        published = parse_publish_date(node["simpleText"])
                        if published:
                            break
            if not desc and "attributedDescriptionBodyText" in o:
                desc = o["attributedDescriptionBodyText"].get("content") or desc
            fr = o.get("factoidRenderer")
            if isinstance(fr, dict):
                label = ((fr.get("label") or {}).get("simpleText") or "").lower()
                value = (fr.get("value") or {}).get("simpleText") or ""
                if "like" in label:
                    digits = re.sub(r"[^\d]", "", value)
                    likes = int(digits) if digits else likes
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return published, likes, comments, title, desc


def fetch_meta(video_id: str) -> dict | None:
    """Prefer yt-dlp (exact epoch). Fall back to watch-page HTML if blocked."""
    info = fetch(video_id)
    if info:
        ts = info.get("release_timestamp") or info.get("timestamp")
        if ts:
            info["_date_only"] = False
            info["published"] = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MYT)
            return info
        # yt-dlp returned something but no epoch — try HTML for the date.
    html_info = fetch_watch_page(video_id)
    if html_info:
        return html_info
    return info  # may be None


def main():
    rows, quality = [], {
        "no_timestamp": 0,
        "no_views": 0,
        "fetch_failed": 0,
        "date_only": 0,
    }

    cookie_note = (
        f"cookies: {COOKIES_FILE}"
        if COOKIES_FILE and os.path.isfile(COOKIES_FILE)
        else (
            f"cookies-from-browser: {COOKIES_FROM_BROWSER}"
            if COOKIES_FROM_BROWSER
            else "cookies: none (watch-page fallback if yt-dlp is blocked)"
        )
    )
    print(cookie_note, flush=True)

    for publisher, channel_id in CHANNELS.items():
        print(f"\n{publisher}", flush=True)
        listing = list_channel(channel_id)
        if not listing:
            print("    nothing listed, check the channel ID", flush=True)
            continue
        print(f"    {len(listing)} unique across tabs, reading metadata", flush=True)

        # Title/description prefilter from the flat listing so we only open
        # watch pages for plausible election clips (runtime).
        candidates = []
        for vid, meta in listing.items():
            title = meta.get("title") or ""
            desc = meta.get("description") or ""
            if not RELEVANCE.search(f"{title} {desc}"):
                continue
            if EXCLUDE.search(title) and not RELEVANCE.search(title):
                continue
            candidates.append((vid, meta))
        print(
            f"    {len(candidates)} title-relevant of {len(listing)} listed",
            flush=True,
        )

        kept = 0
        for vid, listed in candidates:
            try:
                info = fetch_meta(vid)
            except Exception:
                info = None
            if not info:
                quality["fetch_failed"] += 1
                continue

            published = info.get("published")
            if published is None:
                ts = info.get("release_timestamp") or info.get("timestamp")
                if not ts:
                    quality["no_timestamp"] += 1
                    continue
                published = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MYT)

            if not (WINDOW_START <= published <= WINDOW_END):
                continue

            title = info.get("title") or listed.get("title") or ""
            desc = info.get("description") or listed.get("description") or ""
            if not RELEVANCE.search(f"{title} {desc}"):
                continue
            if EXCLUDE.search(title) and not RELEVANCE.search(title):
                continue

            # FIX 3b: take whichever source actually returned a number.
            views = info.get("view_count")
            if views is None:
                views = listed.get("views")
            if views is None:
                quality["no_views"] += 1
                views = 0

            if info.get("_date_only"):
                quality["date_only"] += 1

            secs = info.get("duration") or listed.get("duration") or 0
            url = info.get("webpage_url") or ""
            is_short = (
                listed.get("tab") == "shorts"
                or "/shorts/" in url
                or (secs and secs <= 60)
            )
            is_live = bool(
                listed.get("tab") == "streams"
                or info.get("was_live")
                or info.get("is_live")
            )

            rows.append({
                "publisher": publisher,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published_myt": published.strftime("%Y-%m-%d %H:%M"),
                "views": int(views),
                "likes": info.get("like_count") or 0,
                "comments": info.get("comment_count") or 0,
                "language": classify_language(title, desc),
                "format": "SHORT" if is_short else ("LIVE" if is_live else "VIDEO"),
                "duration_s": secs,
                "channel_id": channel_id,
            })
            kept += 1
            time.sleep(0.05)
        print(f"    kept {kept}", flush=True)

    if not rows:
        sys.exit("Nothing collected. Check the window and the channel IDs.")

    rows.sort(key=lambda r: (r["publisher"], -r["views"]))
    with open(OUT_VIDEOS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = {}
    for r in rows:
        s = summary.setdefault(r["publisher"], {
            "publisher": r["publisher"], "videos": 0, "bm": 0, "en": 0, "cn": 0,
            "views": 0, "shorts": 0, "shorts_views": 0, "live": 0, "live_views": 0})
        s["videos"] += 1
        s["views"] += r["views"]
        s[r["language"].lower()] += 1
        if r["format"] == "SHORT":
            s["shorts"] += 1
            s["shorts_views"] += r["views"]
        if r["format"] == "LIVE":
            s["live"] += 1
            s["live_views"] += r["views"]

    ordered = sorted(summary.values(), key=lambda x: -x["views"])
    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(ordered[0].keys()))
        w.writeheader()
        w.writerows(ordered)

    print(f"\n{'=' * 74}", flush=True)
    print(f"{len(rows)} videos across {len(summary)} publishers", flush=True)
    print(
        f"{'Publisher':26s} {'Vids':>5s} {'BM':>4s} {'Views':>11s} {'Shrt':>5s} {'Live':>5s}",
        flush=True,
    )
    for s in ordered:
        print(
            f"{s['publisher']:26s} {s['videos']:5d} {s['bm']:4d} "
            f"{s['views']:11,d} {s['shorts']:5d} {s['live']:5d}",
            flush=True,
        )

    # Read this before using the CSV. A clean run reports zero on all three.
    print(f"\n{'-' * 74}\nDATA QUALITY", flush=True)
    zero = sum(1 for r in rows if r["views"] == 0)
    noon = sum(1 for r in rows if r["published_myt"].endswith(" 12:00"))
    print(
        f"  rows with no view count      {quality['no_views']:5d}"
        f"   ({zero} rows show 0 views in the output)",
        flush=True,
    )
    print(f"  videos skipped, no timestamp {quality['no_timestamp']:5d}", flush=True)
    print(f"  videos skipped, fetch failed {quality['fetch_failed']:5d}", flush=True)
    print(
        f"  rows timestamped exactly 12:00 {noon:3d}"
        f"   (should be near zero, a large number means dates are date-only)",
        flush=True,
    )
    print(f"  rows from date-only fallback {quality['date_only']:5d}", flush=True)
    if (
        quality["no_views"]
        or quality["no_timestamp"]
        or quality["fetch_failed"]
        or quality["date_only"]
        or noon > max(3, len(rows) // 50)
    ):
        print(
            "\n  NOT CLEAN. Totals may understate or dates may be date-only."
            "\n  Re-run with logged-in cookies:"
            "\n    export YOUTUBE_COOKIES=youtube_cookies.txt"
            "\n  or set COOKIES_FROM_BROWSER at the top of this file.",
            flush=True,
        )
    else:
        print("\n  Clean run.", flush=True)


if __name__ == "__main__":
    main()
