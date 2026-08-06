# N9 2026 election — YouTube output benchmark

Pulls every Negeri Sembilan election video published by a set of Malaysian
news publishers inside a date window, and writes:

- `n9_youtube_videos.csv` — one row per video
- `n9_youtube_summary.csv` — per-publisher totals

Default window: **16 Jul 2026 – 2 Aug 2026** (Malaysia time, UTC+8).

**No YouTube API key required.** Discovery uses yt-dlp channel search; exact
publish dates come from each video’s watch page (calendar dates, not
“5 days ago”).

## Setup

```bash
pip install -r requirements.txt
python3 v1_youtube_scraper_cursor.py
```

## Publishers

The Star, Free Malaysia Today, Malaysiakini / KiniTV, New Straits Times,
Berita Harian, Astro Awani, Sinar Harian, Sin Chew (Pocketimes),
Sin Chew Daily, Nanyang / eNanyang, Buletin TV3 (Media Prima).

## Reuse

For GE16, change `WINDOW_START`, `WINDOW_END`, and `KEYWORDS` in
`v1_youtube_scraper_cursor.py`. The channel list stays.
