#!/usr/bin/env python3
"""
Forvo Ukrainian phrase crawler — metadata only, no audio.

Strategy
--------
`pronounced-words-search` matches an entry when any WORD/TOKEN inside it starts
with the seed (confirmed empirically — it is NOT whole-entry prefix). So seeding
common content-word lemmas (built by build_seeds.py from a real frequency list)
pulls the phrases those words appear in, anywhere in the phrase, plus inflected
forms. Each row already carries `num_pronunciations`; we keep multi-word entries
with enough recordings — the "multiple natives bothered to record it" signal.

Heavy overlap between seeds is expected (a phrase matches every content word it
contains), so results are deduped by `id`.

Budget
------
$2/month Non-Profit tier: 500 requests/day, reset 22:00 UTC. One page = one
request. State is checkpointed to ./data so a run that hits the cap resumes next
day. Seeds are in frequency order, so the highest-value words are crawled first.

Output
------
data/uk_phrases.csv — one row per surviving phrase, with `tier`
(tier2 / tier3 / tier4plus), `word_count`, and a stable `media_stem` (uk_<id>)
so the later audio pass and your images line up by id. CSV is UTF-8 with BOM.
"""

import csv
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import requests

API_KEY = os.environ.get("FORVO_API_KEY")
LANGUAGE = "uk"
BASE = "https://apifree.forvo.com"

SEEDS_FILE = Path(os.environ.get("SEEDS_FILE", "data/seeds_uk.txt"))

PAGESIZE = 100
MIN_WORDS = 2
MIN_PRONUNCIATIONS = 2
MAX_REQUESTS_PER_RUN = int(os.environ.get("MAX_REQUESTS_PER_RUN", "480"))

REQUEST_DELAY_SEC = 1.5
TIMEOUT_SEC = 30
MAX_RETRIES = 4
RECRAWL = os.environ.get("RECRAWL") == "1"

DATA_DIR = Path("data")
PROGRESS_PATH = DATA_DIR / "progress.json"
RAW_PATH = DATA_DIR / "raw_entries.json"
CSV_PATH = DATA_DIR / "uk_phrases.csv"
SAMPLE_PATH = DATA_DIR / "_sample_item.json"

CSV_FIELDS = ["id", "original", "num_pronunciations", "word_count",
              "tier", "media_stem", "forvo_url"]


def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_seeds() -> list:
    if not SEEDS_FILE.exists():
        print(f"Seed file {SEEDS_FILE} not found. Run build_seeds.py first.",
              file=sys.stderr)
        return []
    seeds, seen = [], set()
    for line in SEEDS_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s in seen:
            continue
        seen.add(s)
        seeds.append(s)
    return seeds


class Budget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def available(self):
        return self.used < self.limit

    def spend(self):
        self.used += 1


def search_path(seed, page):
    return ("action/pronounced-words-search/"
            f"search/{urllib.parse.quote(seed)}/"
            f"language/{LANGUAGE}/pagesize/{PAGESIZE}/page/{page}")


def api_get(path, budget):
    url = f"{BASE}/key/{API_KEY}/format/json/{path}"
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        budget.spend()
        try:
            resp = requests.get(url, timeout=TIMEOUT_SEC)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise RuntimeError(f"Non-JSON: {resp.text.strip()[:200]}")
                if isinstance(data, dict) and "items" in data:
                    return data
                raise RuntimeError(f"Unexpected payload: {str(data)[:200]}")
            if resp.status_code in (401, 403):
                raise RuntimeError(f"Auth error {resp.status_code}: check "
                                   f"FORVO_API_KEY. {resp.text.strip()[:200]}")
            last_err = f"HTTP {resp.status_code}: {resp.text.strip()[:200]}"
        except requests.RequestException as exc:
            last_err = str(exc)
        time.sleep(REQUEST_DELAY_SEC * attempt)
    raise RuntimeError(f"Max retries exceeded. Last error: {last_err}")


def coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def crawl(seeds) -> dict:
    progress = load_json(PROGRESS_PATH, {"complete": False, "seeds": {}})
    raw = load_json(RAW_PATH, {})

    if RECRAWL:
        progress = {"complete": False, "seeds": {}}
        raw = {}

    if progress.get("complete"):
        print("Crawl already complete. Regenerating CSV only (0 requests).")
        return raw

    budget = Budget(MAX_REQUESTS_PER_RUN)
    sample_saved = SAMPLE_PATH.exists()

    for seed in seeds:
        state = progress["seeds"].setdefault(
            seed, {"done": False, "next_page": 1, "total_pages": None})
        if state["done"]:
            continue

        while budget.available():
            page = state["next_page"]
            data = api_get(search_path(seed, page), budget)
            attrs = data.get("attributes") or {}
            total_pages = coerce_int(attrs.get("total_pages"), default=1)
            state["total_pages"] = total_pages
            items = data.get("items") or []

            if items and not sample_saved:
                save_json(SAMPLE_PATH, items[0])
                sample_saved = True

            for item in items:
                eid = str(item.get("id"))
                original = (item.get("original") or item.get("word") or "").strip()
                if not eid or not original:
                    continue
                raw[eid] = {
                    "id": eid,
                    "original": original,
                    "num_pronunciations": coerce_int(item.get("num_pronunciations")),
                }

            print(f"  [{seed}] page {page}/{total_pages} "
                  f"(+{len(items)}, {len(raw)} total, req {budget.used}/{budget.limit})")

            if not items or page >= total_pages:
                state["done"] = True
                break
            state["next_page"] = page + 1
            save_json(PROGRESS_PATH, progress)
            save_json(RAW_PATH, raw)
            time.sleep(REQUEST_DELAY_SEC)

        save_json(PROGRESS_PATH, progress)
        save_json(RAW_PATH, raw)

        if not budget.available():
            print(f"Daily request budget reached at seed '{seed}'. Resuming next run.")
            break

    done = sum(1 for s in progress["seeds"].values() if s.get("done"))
    if done == len(seeds) and len(progress["seeds"]) == len(seeds):
        progress["complete"] = True
        print("All seeds enumerated — crawl complete.")
    save_json(PROGRESS_PATH, progress)
    return raw


def tier_for(num):
    return "tier4plus" if num >= 4 else f"tier{num}"


def write_csv(raw):
    rows = []
    for entry in raw.values():
        original = entry["original"]
        wc = len(original.split())
        num = entry["num_pronunciations"]
        if wc < MIN_WORDS or num < MIN_PRONUNCIATIONS:
            continue
        rows.append({
            "id": entry["id"],
            "original": original,
            "num_pronunciations": num,
            "word_count": wc,
            "tier": tier_for(num),
            "media_stem": f"uk_{entry['id']}",
            "forvo_url": f"https://forvo.com/word/{urllib.parse.quote(original)}/#uk",
        })
    order = {"tier4plus": 0, "tier3": 1, "tier2": 2}
    rows.sort(key=lambda r: (order.get(r["tier"], 9),
                             -r["num_pronunciations"], r["original"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    counts = {"tier2": 0, "tier3": 0, "tier4plus": 0}
    for r in rows:
        counts[r["tier"]] += 1
    return {"total": len(rows), **counts}


def main():
    if not API_KEY:
        print("FORVO_API_KEY is not set.", file=sys.stderr)
        return 1
    seeds = load_seeds()
    if not seeds:
        return 1
    print(f"Loaded {len(seeds)} seeds from {SEEDS_FILE}.")
    raw = crawl(seeds)
    summary = write_csv(raw)
    print("\n--- Summary ---")
    print(f"raw entries crawled : {len(raw)}")
    print(f"phrases kept        : {summary['total']}")
    print(f"  tier2 (2 recs)    : {summary['tier2']}")
    print(f"  tier3 (3 recs)    : {summary['tier3']}")
    print(f"  tier4plus (4+)    : {summary['tier4plus']}")
    print(f"CSV written to      : {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
