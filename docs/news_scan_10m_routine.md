# News Scan Routine (10-minute window)

## Objective
Provide a recurring automation that checks for fresh RSS news every 10 minutes, with basic reliability controls and a low-noise handoff output for editorial consumption.

## Delivered Architecture
- Scheduler: GitHub Actions workflow `.github/workflows/news-scan-10m.yml` runs every 10 minutes (`cron: */10 * * * *`) and supports manual trigger.
- Collector: `automation/news_scan.py` fetches the RSS source with retry/backoff and timeout.
- Window filter: the scanner only includes items with `pubDate` inside the last `NEWS_SCAN_WINDOW_MINUTES` (default: 10).
- Deduplication: scanner keeps a persistent `seen_ids` state file and skips previously emitted entries.
- Observability: each run emits:
  - `news-handoff/scan_report.json` (run metadata and counts)
  - `news-handoff/new_items.json` (machine-readable handoff payload)
  - `news-handoff/new_items.md` (human-readable handoff summary)
- Runtime persistence: workflow restores scanner state from cache (`.state/news-scan-state.json`) to prevent duplicate alerts across scheduled runs.

## Environment Variables
- `NEWS_FEED_URL`: RSS endpoint. If not set, defaults to `https://agenciabrasil.ebc.com.br/rss.xml`.
- `NEWS_SCAN_WINDOW_MINUTES`: lookback window in minutes. Default: `10`.
- `NEWS_SCAN_STATE_PATH`: path of dedup state file. Default: `.state/news-scan-state.json`.
- `NEWS_SCAN_OUTPUT_DIR`: output directory. Default: `news-handoff`.

## Handoff Contract (for CMO workflow)
The CMO side should read `news-handoff/new_items.json` as the canonical list of newly detected items in that run:

- `id`: unique item id (guid or link)
- `title`: source headline
- `link`: source URL
- `guid`: source GUID when available
- `published_at_utc`: normalized source publish timestamp
- `categories`: source categories array

The CMO side can use `scan_report.json.total_new_items` for run-level monitoring and skip processing when `0`.

## Risks and Follow-ups
- GitHub Actions cron has no strict real-time guarantees; execution may drift by a few minutes under load.
- Dedup state is cache-based in CI. If cache is purged, historical dedup memory resets.
- If feed publishers delay `pubDate` or publish stale timestamps, some fresh items can be missed by strict window filtering.

Recommended hardening follow-up:
- Move dedup state to persistent storage (DB/object store) if strict durability is required.
