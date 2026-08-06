"""
N9 2026 election, YouTube output benchmark
==========================================

Pulls every election video published by a set of Malaysian publishers inside a
date window, and writes a per-video CSV plus a per-publisher summary CSV.

Window default: 16 Jul 2026 to 2 Aug 2026 inclusive, Malaysia time (UTC+8).

WHY THIS EXISTS
Scraping YouTube listing pages alone gives relative dates ("5 days ago",
"2 weeks ago"). "2 weeks ago" is a seven-day bucket, so any video near a
window edge is unplaceable, and a single keyword search silently misses
videos. This script discovers candidates via channel search (yt-dlp, no API
key), then opens each watch page for an exact calendar publish date and
exact view counts.

SETUP
1.  pip install -r requirements.txt
2.  python3 v1_youtube_scraper_cursor.py

No Google Cloud / YouTube Data API key required.

REUSE
For GE16, change WINDOW_START, WINDOW_END and KEYWORDS. The channel list stays.
"""

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import yt_dlp
except ImportError:
    sys.exit("Run: pip install -r requirements.txt")


# ----------------------------------------------------------------------------
# CONFIG, edit this block
# ----------------------------------------------------------------------------

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
    "PRNNS2026",
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Relative listing dates clearly older than the window — skip watch-page fetch.
SKIP_RELATIVE = re.compile(
    r"\b(\d+\s+years?|1\s+year|a\s+year|\d+\s+months?|a\s+month)\s+ago\b",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------

def classify_language(title: str, description: str) -> str:
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
    if hits == 1 and len(bm_markers.findall(description)) >= 3:
        return "BM"
    return "EN"


def parse_views(text) -> int:
    if text is None:
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    if isinstance(text, dict):
        text = text.get("simpleText") or ""
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else 0


def parse_duration_text(text) -> int:
    """'1:24' / '1:02:03' -> seconds."""
    if not text:
        return 0
    if isinstance(text, dict):
        text = text.get("simpleText") or ""
    parts = str(text).strip().split(":")
    if not all(p.isdigit() for p in parts):
        return 0
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def parse_publish_date(text: str):
    """Parse 'Aug 1, 2026' / '1 Aug 2026' style dates from watch pages."""
    if not text:
        return None
    text = text.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(
                hour=12, minute=0, second=0, tzinfo=MYT
            )
        except ValueError:
            continue
    return None


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "replace")


def _walk_video_renderers(data):
    """Yield (videoId, title, description, views, duration_s, relative_date)."""
    found = []

    def simple(node):
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if "simpleText" in node:
                return node.get("simpleText") or ""
            runs = node.get("runs")
            if isinstance(runs, list):
                return "".join(r.get("text", "") for r in runs)
        return ""

    def walk(o):
        if isinstance(o, dict):
            vid = o.get("videoId")
            if vid and ("title" in o or "publishedTimeText" in o):
                title = simple(o.get("title"))
                desc = simple(o.get("descriptionSnippet") or o.get("description"))
                views = parse_views(o.get("viewCountText"))
                duration = parse_duration_text(o.get("lengthText"))
                rel = simple(o.get("publishedTimeText"))
                found.append((vid, title, desc, views, duration, rel))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def search_channel_html(channel_id: str, keyword: str) -> dict:
    """Channel search via page HTML. Includes relative dates for coarse skip."""
    q = urllib.parse.quote(keyword)
    url = f"https://www.youtube.com/channel/{channel_id}/search?query={q}"
    out = {}
    try:
        html = _http_get(url)
    except Exception as e:
        print(f"    ! {keyword} (html): {e}")
        return out
    m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html)
    if not m:
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});", html)
    if not m:
        return out
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return out
    for vid, title, desc, views, duration, rel in _walk_video_renderers(data):
        out[vid] = {
            "id": vid,
            "title": title,
            "description": desc,
            "view_count": views,
            "duration": duration,
            "relative_date": rel,
        }
    return out


def search_channel_ytdlp(channel_id: str, keyword: str) -> dict:
    """Channel search via yt-dlp (paginates further than the first HTML page)."""
    q = urllib.parse.quote(keyword)
    url = f"https://www.youtube.com/channel/{channel_id}/search?query={q}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "skip_download": True,
    }
    out = {}
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"    ! {keyword} (yt-dlp): {e}")
            return out
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out[e["id"]] = {
            "id": e["id"],
            "title": e.get("title") or "",
            "description": e.get("description") or "",
            "view_count": parse_views(e.get("view_count")),
            "duration": int(e.get("duration") or 0),
            "relative_date": "",
        }
    return out


def search_channel(channel_id: str, keyword: str) -> dict:
    """Prefer yt-dlp (full pagination); fall back / merge HTML for short queries."""
    ytdlp = search_channel_ytdlp(channel_id, keyword)
    html = search_channel_html(channel_id, keyword)
    # Merge: yt-dlp for coverage, HTML for relative_date when available.
    out = dict(ytdlp)
    for vid, entry in html.items():
        if vid in out:
            if entry.get("relative_date"):
                out[vid]["relative_date"] = entry["relative_date"]
            if not out[vid].get("title"):
                out[vid]["title"] = entry.get("title") or ""
            if not out[vid].get("description"):
                out[vid]["description"] = entry.get("description") or ""
            if not out[vid].get("view_count"):
                out[vid]["view_count"] = entry.get("view_count") or 0
            if not out[vid].get("duration"):
                out[vid]["duration"] = entry.get("duration") or 0
        else:
            out[vid] = entry
    return out


def fetch_watch_meta(video_id: str) -> dict:
    """Exact publish date + likes from the watch page (no API key)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        html = _http_get(url)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}

    published = None
    likes = 0
    comments = 0

    m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html)
    if not m:
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});", html)
    if m:
        try:
            data = json.loads(m.group(1))
            published, likes, comments = _meta_from_initial(data)
        except json.JSONDecodeError:
            pass

    if published is None:
        m = re.search(
            r'"publishDate"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', html
        )
        if m:
            published = parse_publish_date(m.group(1))

    return {
        "published": published,
        "likes": likes,
        "comments": comments,
        "error": None if published else "no publish date",
    }


def _meta_from_initial(data: dict):
    published = None
    likes = 0
    comments = 0

    def walk(o):
        nonlocal published, likes, comments
        if isinstance(o, dict):
            if published is None:
                for key in ("publishDate", "dateText"):
                    node = o.get(key)
                    if isinstance(node, dict) and node.get("simpleText"):
                        published = parse_publish_date(node["simpleText"])
                        if published:
                            break
            fr = o.get("factoidRenderer")
            if isinstance(fr, dict):
                label = ((fr.get("label") or {}).get("simpleText") or "").lower()
                value = (fr.get("value") or {}).get("simpleText") or ""
                if "like" in label:
                    likes = parse_views(value)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return published, likes, comments


def main():
    rows = []
    detail_cache = {}

    for publisher, channel_id in CHANNELS.items():
        if not channel_id:
            print(f"\n{publisher}: SKIPPED, no channel ID set")
            continue

        print(f"\n{publisher}")
        found = {}
        for kw in KEYWORDS:
            got = search_channel(channel_id, kw)
            before = len(found)
            # Prefer entries that already carry relative_date / richer fields.
            for vid, entry in got.items():
                if vid not in found:
                    found[vid] = entry
                else:
                    if entry.get("relative_date") and not found[vid].get("relative_date"):
                        found[vid]["relative_date"] = entry["relative_date"]
                    for field in ("title", "description", "view_count", "duration"):
                        if not found[vid].get(field) and entry.get(field):
                            found[vid][field] = entry[field]
            print(
                f"    {kw:20s} {len(got):4d} hits   "
                f"(+{len(found) - before} new, {len(found)} total)"
            )
            time.sleep(0.25)

        if not found:
            print("    nothing found")
            continue

        kept = 0
        checked = 0
        skipped_old = 0
        for vid, entry in sorted(found.items()):
            title = entry.get("title") or ""
            desc = entry.get("description") or ""
            if not RELEVANCE.search(f"{title} {desc}"):
                continue
            if EXCLUDE.search(title) and not RELEVANCE.search(title):
                continue

            rel = entry.get("relative_date") or ""
            if rel and SKIP_RELATIVE.search(rel):
                skipped_old += 1
                continue

            if vid not in detail_cache:
                detail_cache[vid] = fetch_watch_meta(vid)
                time.sleep(0.12)
            meta = detail_cache[vid]
            checked += 1
            published = meta.get("published")
            if not published:
                print(f"    ! skip {vid}: {meta.get('error')}")
                continue
            if not (WINDOW_START <= published <= WINDOW_END):
                continue

            secs = int(entry.get("duration") or 0)
            rows.append({
                "publisher": publisher,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published_myt": published.strftime("%Y-%m-%d %H:%M"),
                "views": parse_views(entry.get("view_count")),
                "likes": int(meta.get("likes") or 0),
                "comments": int(meta.get("comments") or 0),
                "language": classify_language(title, desc),
                "format": "SHORT" if 0 < secs <= 60 else ("VIDEO" if secs > 60 else "VIDEO"),
                "duration_s": secs,
                "in_window": True,
            })
            kept += 1

        print(
            f"    kept {kept} (checked {checked} watch pages, "
            f"skipped {skipped_old} clearly-old, {len(found)} discovered)"
        )

    with open(OUT_VIDEOS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=VIDEO_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    summary = {}
    for r in rows:
        p = r["publisher"]
        s = summary.setdefault(p, {"publisher": p, "videos": 0, "bm": 0, "en": 0,
                                   "cn": 0, "views": 0, "shorts": 0,
                                   "shorts_views": 0})
        s["videos"] += 1
        s["views"] += r["views"]
        lang = (r.get("language") or "").lower()
        if lang in ("bm", "en", "cn"):
            s[lang] += 1
        if r["format"] == "SHORT":
            s["shorts"] += 1
            s["shorts_views"] += r["views"]

    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        if summary:
            w.writerows(sorted(summary.values(), key=lambda x: -x["views"]))

    print(f"\n{'-' * 62}")
    print(f"{len(rows)} videos across {len(summary)} publishers")
    print(f"Written: {OUT_VIDEOS}, {OUT_SUMMARY}")
    print(f"{'-' * 62}")
    print(f"{'Publisher':28s} {'Vids':>5s} {'BM':>4s} {'Views':>10s} {'Shorts':>7s}")
    for s in sorted(summary.values(), key=lambda x: -x["views"]):
        print(f"{s['publisher']:28s} {s['videos']:5d} {s['bm']:4d} "
              f"{s['views']:10,d} {s['shorts']:7d}")


if __name__ == "__main__":
    main()
