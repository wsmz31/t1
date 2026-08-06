"""
N9 2026 election, YouTube output benchmark
==========================================

Pulls every election video published by a set of Malaysian publishers inside a
date window, and writes a per-video CSV plus a per-publisher summary CSV.

Window default: 16 Jul 2026 to 2 Aug 2026 inclusive, Malaysia time (UTC+8).

WHY THIS EXISTS
Scraping YouTube listing pages alone gives relative dates ("5 days ago",
"2 weeks ago"). "2 weeks ago" is a seven-day bucket, so any video near a
window edge is unplaceable. This script browses each channel's Videos and
Shorts tabs newest-first (no API key), stops once listings are clearly older
than the window, then opens each relevant watch page for an exact calendar
publish date and exact view counts.

SETUP
1.  pip install -r requirements.txt
2.  python3 -u v1_youtube_scraper_cursor.py

No Google Cloud / YouTube Data API key required.

REUSE
For GE16, change WINDOW_START, WINDOW_END and KEYWORDS/RELEVANCE. The channel
list stays.
"""

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta


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

# A video is kept only if its title or description matches one of these.
# Widen if you find the filter dropping real coverage.
RELEVANCE = re.compile(
    r"negeri\s+sembilan|negri\s+sembilan|\bn9\b|\bn\.?\s*sembilan\b|"
    r"\bprn\s*n\.?\s*s|\bprnns\b|\bprnn9\b|\bprn\s*n9\b|state polls|"
    r"state election|pilihan\s+raya\s+negeri|森美兰|森州",
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

# Newest-first browse: stop after this many consecutive clearly-old items.
OLD_STREAK_STOP = 12


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
    s = str(text).strip().lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*([kmb])?", s)
    if not m:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    n = float(m.group(1))
    suf = m.group(2)
    if suf == "k":
        n *= 1_000
    elif suf == "m":
        n *= 1_000_000
    elif suf == "b":
        n *= 1_000_000_000
    return int(n)


def parse_duration_text(text) -> int:
    """'1:24' / '1:02:03' -> seconds."""
    if not text:
        return 0
    parts = str(text).strip().split(":")
    if not all(p.isdigit() for p in parts):
        return 0
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def parse_publish_date(text: str):
    """Parse 'Aug 1, 2026' / 'Premiered Jul 27, 2026' style watch-page dates."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(
        r"^(premiered|streamed|published|joined|started streaming)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(
                hour=12, minute=0, second=0, tzinfo=MYT
            )
        except ValueError:
            continue
    return None


def relative_age_bucket(text: str):
    """
    Classify listing relative dates.
    Returns: 'recent' | 'maybe' | 'old' | 'unknown'
    """
    if not text:
        return "unknown"
    t = text.lower().strip()
    t = re.sub(r"^(streamed|premiered)\s+", "", t)
    if re.search(r"\b(minute|hour|minutes|hours)\b", t):
        return "recent"
    m = re.search(r"(\d+)\s+day", t)
    if m:
        days = int(m.group(1))
        # Window is ~21 days deep from 6 Aug 2026; keep slack for bucket fuzz.
        if days <= 25:
            return "recent"
        if days <= 35:
            return "maybe"
        return "old"
    m = re.search(r"(\d+)\s+week", t)
    if m:
        weeks = int(m.group(1))
        if weeks <= 2:
            return "recent"
        if weeks == 3:
            return "maybe"
        return "old"
    if re.search(r"\b(a|1)\s+week\b", t):
        return "recent"
    if re.search(r"\b(month|year|months|years)\b", t):
        return "old"
    return "unknown"


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _extract_lockups(obj):
    """Pull video rows + grid continuation tokens from browse / ytInitialData JSON."""
    rows = []
    tokens = []
    seen_ids = set()

    def duration_from(lv):
        found = []

        def walk(o):
            if isinstance(o, dict):
                if (
                    o.get("badgeStyle") == "THUMBNAIL_OVERLAY_BADGE_STYLE_DEFAULT"
                    and o.get("text")
                    and ":" in str(o.get("text"))
                ):
                    found.append(str(o["text"]))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(lv.get("contentImage"))
        return found[0] if found else ""

    def walk(o):
        if isinstance(o, dict):
            # Only follow explicit list continuations (load-more), not every
            # continuationCommand embedded in menus / engagement panels.
            cir = o.get("continuationItemRenderer")
            if isinstance(cir, dict):
                tok = (
                    (cir.get("continuationEndpoint") or {})
                    .get("continuationCommand", {})
                    .get("token")
                )
                if tok:
                    tokens.append(tok)
            # Legacy grid/video renderer (still used on some tabs / clients)
            if o.get("videoId") and ("title" in o or "publishedTimeText" in o):
                vid = o["videoId"]
                if vid not in seen_ids:
                    title = o.get("title")
                    if isinstance(title, dict):
                        title = title.get("simpleText") or "".join(
                            r.get("text", "") for r in title.get("runs", [])
                        )
                    rel = (o.get("publishedTimeText") or {}).get("simpleText") or ""
                    views = (o.get("viewCountText") or {}).get("simpleText") or ""
                    length = (o.get("lengthText") or {}).get("simpleText") or ""
                    rows.append({
                        "id": vid,
                        "title": title or "",
                        "relative_date": rel,
                        "views": parse_views(views),
                        "duration": parse_duration_text(length),
                        "description": "",
                    })
                    seen_ids.add(vid)
            lv = o.get("lockupViewModel")
            if lv and lv.get("contentId"):
                vid = lv["contentId"]
                if vid not in seen_ids:
                    meta = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                    title = (meta.get("title") or {}).get("content") or ""
                    rel, views = "", ""
                    cm = ((meta.get("metadata") or {}).get("contentMetadataViewModel") or {})
                    for row in cm.get("metadataRows") or []:
                        for part in row.get("metadataParts") or []:
                            text = (part.get("text") or {}).get("content") or ""
                            low = text.lower()
                            if "ago" in low or low.startswith("streamed") or low.startswith("premiered"):
                                rel = text
                            elif "view" in low:
                                views = text
                    rows.append({
                        "id": vid,
                        "title": title,
                        "relative_date": rel,
                        "views": parse_views(views),
                        "duration": parse_duration_text(duration_from(lv)),
                        "description": "",
                    })
                    seen_ids.add(vid)
            # Shorts shelf uses a different view model (often no relative date).
            slv = o.get("shortsLockupViewModel")
            if slv:
                vid = ((slv.get("onTap") or {}).get("innertubeCommand") or {}).get(
                    "reelWatchEndpoint", {}
                ).get("videoId")
                if vid and vid not in seen_ids:
                    access = slv.get("accessibilityText") or ""
                    # "TITLE, 435 views - play Short" / "TITLE, 3.2 thousand views - play Short"
                    title = access
                    views = 0
                    m = re.match(
                        r"^(.*),\s*([\d.,]+\s*(?:[kmb]|thousand|million)?\s*views?)"
                        r"(?:\s*-\s*play short)?\s*$",
                        access,
                        re.I,
                    )
                    if m:
                        title = m.group(1).strip()
                        views = parse_views(m.group(2))
                    rows.append({
                        "id": vid,
                        "title": title,
                        "relative_date": "",
                        "views": views,
                        "duration": 60,  # Shorts tab; exact length from watch if needed
                        "description": "",
                    })
                    seen_ids.add(vid)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return rows, tokens


def browse_tab(channel_id: str, tab: str, detail_cache: dict | None = None) -> list:
    """
    Newest-first Videos or Shorts tab, stopping past the election window.
    tab: 'videos' | 'shorts'

    Shorts shelves often omit relative dates, so for that tab we stop using
    exact publish dates from title-relevant watch pages.
    """
    if detail_cache is None:
        detail_cache = {}

    url = f"https://www.youtube.com/channel/{channel_id}/{tab}"
    try:
        html = _http_get(url)
    except Exception as e:
        print(f"    ! {tab}: {e}", flush=True)
        return []

    api_key_m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    client_m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    data_m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html)
    if not data_m:
        data_m = re.search(r"ytInitialData\s*=\s*(\{.+?\});", html)
    if not data_m:
        print(f"    ! {tab}: no ytInitialData", flush=True)
        return []

    data = json.loads(data_m.group(1))
    api_key = api_key_m.group(1) if api_key_m else "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
    client_ver = client_m.group(1) if client_m else "2.20260806.01.00"

    collected = []
    seen = set()
    rows, tokens = _extract_lockups(data)
    old_streak = 0
    page = 1
    page_cap = 40 if tab == "shorts" else 80

    def shorts_past_window(batch) -> bool:
        """Fetch dates for title-relevant shorts; stop after an old streak."""
        nonlocal old_streak
        for row in batch:
            if not RELEVANCE.search(row.get("title") or ""):
                continue
            vid = row["id"]
            if vid not in detail_cache:
                detail_cache[vid] = fetch_watch_meta(vid)
                time.sleep(0.12)
            published = detail_cache[vid].get("published")
            if not published:
                continue
            if published < WINDOW_START:
                old_streak += 1
                if old_streak >= OLD_STREAK_STOP:
                    return True
            else:
                old_streak = 0
        return False

    def absorb(batch):
        nonlocal old_streak
        stop = False
        new_rows = []
        for row in batch:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            if tab == "shorts":
                # No reliable relative dates on shorts shelves — keep all for
                # title filter; stop decided via exact dates below.
                collected.append(row)
                new_rows.append(row)
                continue
            bucket = relative_age_bucket(row.get("relative_date") or "")
            if bucket == "old":
                old_streak += 1
                if old_streak >= OLD_STREAK_STOP:
                    stop = True
                    break
                continue
            old_streak = 0
            collected.append(row)
        if tab == "shorts" and shorts_past_window(new_rows):
            return True
        return stop

    if absorb(rows):
        print(
            f"    {tab:6s}  {len(collected):4d} candidates in-window-ish "
            f"({page} pages)",
            flush=True,
        )
        return collected

    used_tokens = set()
    while tokens:
        token = tokens.pop(0)
        if token in used_tokens:
            continue
        used_tokens.add(token)
        page += 1
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": client_ver,
                    "hl": "en",
                    "gl": "MY",
                }
            },
            "continuation": token,
        }
        try:
            resp = _http_post_json(
                f"https://www.youtube.com/youtubei/v1/browse?key={api_key}",
                body,
            )
        except Exception as e:
            print(f"    ! {tab} page {page}: {e}", flush=True)
            break
        batch, more = _extract_lockups(resp)
        if absorb(batch):
            break
        for t in more:
            if t not in used_tokens:
                tokens.append(t)
        time.sleep(0.15)
        if page >= page_cap or len(collected) >= 2500:
            print(f"    ! {tab}: page cap reached ({page} pages)", flush=True)
            break

    print(
        f"    {tab:6s}  {len(collected):4d} candidates in-window-ish "
        f"({page} pages)",
        flush=True,
    )
    return collected


def fetch_watch_meta(video_id: str) -> dict:
    """Exact publish date + likes + description from the watch page."""
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
    description = ""

    m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html)
    if not m:
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});", html)
    if m:
        try:
            data = json.loads(m.group(1))
            published, likes, comments, description = _meta_from_initial(data)
        except json.JSONDecodeError:
            pass

    if published is None:
        m = re.search(
            r'"publishDate"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', html
        )
        if m:
            published = parse_publish_date(m.group(1))

    if not description:
        m = re.search(
            r'"shortDescription":"(.*?)"(?:,|})', html
        )
        if m:
            description = (
                m.group(1)
                .encode("utf-8")
                .decode("unicode_escape", errors="ignore")
            )

    return {
        "published": published,
        "likes": likes,
        "comments": comments,
        "description": description,
        "error": None if published else "no publish date",
    }


def _meta_from_initial(data: dict):
    published = None
    likes = 0
    comments = 0
    description = ""

    def walk(o):
        nonlocal published, likes, comments, description
        if isinstance(o, dict):
            if published is None:
                for key in ("publishDate", "dateText"):
                    node = o.get(key)
                    if isinstance(node, dict) and node.get("simpleText"):
                        published = parse_publish_date(node["simpleText"])
                        if published:
                            break
            if not description and "attributedDescriptionBodyText" in o:
                description = (o["attributedDescriptionBodyText"].get("content") or "")
            if not description and o.get("description") and isinstance(o["description"], dict):
                if "simpleText" in o["description"]:
                    description = o["description"].get("simpleText") or description
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
    return published, likes, comments, description


def main():
    rows = []
    detail_cache = {}

    for publisher, channel_id in CHANNELS.items():
        print(f"\n{publisher}", flush=True)
        found = {}
        for tab in ("videos", "shorts"):
            for entry in browse_tab(channel_id, tab, detail_cache):
                found[entry["id"]] = entry

        if not found:
            print("    nothing found", flush=True)
            continue

        kept = 0
        checked = 0
        for vid, entry in sorted(found.items()):
            title = entry.get("title") or ""
            # News titles almost always carry the state/PRN marker; skip the
            # watch-page fetch when the title is clearly unrelated.
            if not RELEVANCE.search(title):
                continue
            if EXCLUDE.search(title) and not RELEVANCE.search(title):
                continue

            if vid not in detail_cache:
                detail_cache[vid] = fetch_watch_meta(vid)
                time.sleep(0.12)
            meta = detail_cache[vid]
            checked += 1
            published = meta.get("published")
            if not published:
                print(f"    ! skip {vid}: {meta.get('error')}", flush=True)
                continue

            desc = meta.get("description") or entry.get("description") or ""
            if not (WINDOW_START <= published <= WINDOW_END):
                continue

            secs = int(entry.get("duration") or 0)
            rows.append({
                "publisher": publisher,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published_myt": published.strftime("%Y-%m-%d %H:%M"),
                "views": int(entry.get("views") or 0),
                "likes": int(meta.get("likes") or 0),
                "comments": int(meta.get("comments") or 0),
                "language": classify_language(title, desc),
                "format": "SHORT" if 0 < secs <= 60 else "VIDEO",
                "duration_s": secs,
                "in_window": True,
            })
            kept += 1

        print(
            f"    kept {kept} (checked {checked} watch pages, "
            f"{len(found)} browse candidates)",
            flush=True,
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

    print(f"\n{'-' * 62}", flush=True)
    print(f"{len(rows)} videos across {len(summary)} publishers", flush=True)
    print(f"Written: {OUT_VIDEOS}, {OUT_SUMMARY}", flush=True)
    print(f"{'-' * 62}", flush=True)
    print(
        f"{'Publisher':28s} {'Vids':>5s} {'BM':>4s} {'Views':>10s} {'Shorts':>7s}",
        flush=True,
    )
    for s in sorted(summary.values(), key=lambda x: -x["views"]):
        print(
            f"{s['publisher']:28s} {s['videos']:5d} {s['bm']:4d} "
            f"{s['views']:10,d} {s['shorts']:7d}",
            flush=True,
        )


if __name__ == "__main__":
    main()
