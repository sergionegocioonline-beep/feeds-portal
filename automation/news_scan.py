#!/usr/bin/env python3
"""Scan RSS feed for new items in a rolling time window."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


DEFAULT_FEED_URL = "https://agenciabrasil.ebc.com.br/rss.xml"
DEFAULT_WINDOW_MINUTES = 10
DEFAULT_STATE_PATH = Path(".state/news-scan-state.json")
DEFAULT_OUTPUT_DIR = Path("news-handoff")
MAX_SEEN_ITEMS = 5000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_pubdate(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        return None


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_ids": [], "last_run_utc": None}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("seen_ids", [])
    data.setdefault("last_run_utc", None)
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def fetch_feed(url: str, retries: int = 3, timeout_seconds: int = 30) -> bytes:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "feeds-portal-news-scan/1.0",
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except Exception as err:
            last_err = err
            if attempt < retries:
                time.sleep(attempt * 2)
    raise RuntimeError(f"failed to fetch feed after {retries} attempts: {last_err}")


def scan_feed(feed_xml: bytes, window_start_utc: datetime, seen_ids: set[str]) -> list[dict[str, Any]]:
    root = ET.fromstring(feed_xml)
    items = root.findall("./channel/item")

    new_items: list[dict[str, Any]] = []
    for item in items:
        guid = (item.findtext("guid") or "").strip()
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()
        pub_date_utc = parse_pubdate(pub_date_raw)

        item_id = guid or link
        if not item_id:
            continue
        if item_id in seen_ids:
            continue
        if pub_date_utc is None or pub_date_utc < window_start_utc:
            continue

        category_nodes = item.findall("category")
        categories = [(node.text or "").strip() for node in category_nodes if (node.text or "").strip()]

        new_items.append(
            {
                "id": item_id,
                "title": title,
                "link": link,
                "guid": guid,
                "published_at_utc": pub_date_utc.isoformat(),
                "categories": categories,
            }
        )

    # Keep oldest-to-newest for deterministic handoff.
    new_items.sort(key=lambda x: x["published_at_utc"])
    return new_items


def write_outputs(output_dir: Path, report: dict[str, Any], new_items: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "scan_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    items_path = output_dir / "new_items.json"
    with items_path.open("w", encoding="utf-8") as handle:
        json.dump(new_items, handle, ensure_ascii=False, indent=2)

    lines = [
        "# News Scan Handoff",
        "",
        f"- run_at_utc: {report['run_at_utc']}",
        f"- window_start_utc: {report['window_start_utc']}",
        f"- total_new_items: {report['total_new_items']}",
        "",
        "## Items",
    ]
    if not new_items:
        lines.append("")
        lines.append("- No new items found in the current window.")
    else:
        for item in new_items:
            lines.append("")
            lines.append(f"- {item['title']}")
            lines.append(f"  - published_at_utc: {item['published_at_utc']}")
            lines.append(f"  - link: {item['link']}")
            if item["categories"]:
                lines.append(f"  - categories: {', '.join(item['categories'])}")

    markdown_path = output_dir / "new_items.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    feed_url = os.getenv("NEWS_FEED_URL", DEFAULT_FEED_URL).strip() or DEFAULT_FEED_URL
    window_minutes = int(os.getenv("NEWS_SCAN_WINDOW_MINUTES", str(DEFAULT_WINDOW_MINUTES)))
    state_path = Path(os.getenv("NEWS_SCAN_STATE_PATH", str(DEFAULT_STATE_PATH)))
    output_dir = Path(os.getenv("NEWS_SCAN_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))

    run_at_utc = utc_now()
    window_start_utc = run_at_utc - timedelta(minutes=window_minutes)

    state = load_state(state_path)
    seen_ids = set(state.get("seen_ids", []))

    feed_xml = fetch_feed(feed_url)
    new_items = scan_feed(feed_xml, window_start_utc, seen_ids)

    all_seen = list(new_items_item["id"] for new_items_item in new_items) + state.get("seen_ids", [])
    state["seen_ids"] = all_seen[:MAX_SEEN_ITEMS]
    state["last_run_utc"] = run_at_utc.isoformat()
    save_state(state_path, state)

    report = {
        "run_at_utc": run_at_utc.isoformat(),
        "window_start_utc": window_start_utc.isoformat(),
        "window_minutes": window_minutes,
        "feed_url": feed_url,
        "total_new_items": len(new_items),
        "state_path": str(state_path),
        "output_dir": str(output_dir),
    }
    write_outputs(output_dir, report, new_items)

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
