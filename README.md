# N9 2026 election — YouTube output benchmark

Pulls every Negeri Sembilan election video published by a set of Malaysian
news publishers inside a date window, and writes:

- `n9_youtube_videos.csv` — one row per video
- `n9_youtube_summary.csv` — per-publisher totals

Default window: **16 Jul 2026 – 2 Aug 2026** (Malaysia time, UTC+8).

## Setup

```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY="AIza..."   # YouTube Data API v3 key
python v1_youtube_scraper_cursor.py
```

Get a free key: [Google Cloud Console](https://console.cloud.google.com) →
new project → APIs & Services → Library → enable **YouTube Data API v3** →
Credentials → Create credentials → API key.

## Quota

Default allowance is 10,000 units/day. `search.list` costs 100 units per call.
With 11 channels × 6 keywords ≈ 6,600 units — fits once per day. Trim
`KEYWORDS` in the script if you hit the cap.

## Publishers

The Star, Free Malaysia Today, Malaysiakini / KiniTV, New Straits Times,
Berita Harian, Astro Awani, Sinar Harian, Sin Chew (Pocketimes),
Sin Chew Daily, Nanyang / eNanyang, Buletin TV3 (Media Prima).

## Reuse

For GE16, change `WINDOW_START`, `WINDOW_END`, and `KEYWORDS` in
`v1_youtube_scraper_cursor.py`. The channel list stays.
