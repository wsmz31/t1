# N9 2026 election — YouTube output benchmark

Pulls Negeri Sembilan election videos from Malaysian news publishers
(16 Jul–2 Aug 2026, MYT) into:

- `n9_youtube_videos.csv`
- `n9_youtube_summary.csv`

**No YouTube API key required.** Use **v3** (current):

```bash
pip install -r requirements.txt
python3 -u v3_youtube_scraper_cursor.py
```

## What v3 fixes vs v1

1. Walks `/streams` as well as `/videos` and `/shorts` (captures high-reach livestream VODs).
2. Matches bare `\bnegri\b` (The Star’s house spelling) and adds mStar.
3. Prefers exact epoch timestamps from yt-dlp; fills views from listing **or** watch page.
4. Prints a **DATA QUALITY** report — do not use the CSV until it says clean.

## Cookies (for a clean run)

If YouTube bot-checks the host, yt-dlp cannot return exact timestamps. Then:

1. Export logged-in YouTube cookies (Netscape `cookies.txt`).
2. Save as `youtube_cookies.txt` in this folder, or:
   `export YOUTUBE_COOKIES=/path/to/cookies.txt`
3. Or set `COOKIES_FROM_BROWSER = "chrome"` in the script (local only).

Without cookies the script falls back to watch-page calendar dates (often `12:00`) and the quality report will say **NOT CLEAN**.

## Publishers

The Star, mStar, Free Malaysia Today, Malaysiakini / KiniTV, New Straits Times,
Berita Harian, Astro Awani, Sinar Harian, Sin Chew (Pocketimes), Sin Chew Daily,
Nanyang / eNanyang, Buletin TV3 (Media Prima).
