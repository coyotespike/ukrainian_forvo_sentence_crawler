#!/usr/bin/env python3
"""
Forvo assumption validator (v2) — run locally BEFORE the full crawl.

v1 assumed pronounced-words-search was a whole-entry PREFIX match. The probe
disproved that: it is a TOKEN-level match — an entry is returned when any
word/token inside it starts with the seed (tokens split on spaces, apostrophes,
hyphens). That is why seeding common *content words* surfaces phrases that
contain them mid-string, and why the seed list is now a frequency word list,
not the alphabet.

This version validates the assumptions THAT strategy depends on:

  0. Preflight: auth, envelope, item fields (1 request, reused below).
  1. Token-match rule: every result has a token starting with the seed, and
     some match on a NON-initial token (proves mid-phrase discovery).
  2. Inflection capture: a lemma seed (друг) also pulls друга / другом / ...
  3. Phrase yield: multi-word entries with >=2 recordings for the seed.
  4. Case-insensitivity: a lowercase seed matches capitalised entries.
  5. Per-seed yield across several real content words (realistic expectations).
  6. Pagination sanity for a content word, where exercisable.
  7. Seed-file sanity (if data/seeds_uk.txt exists): single-token, lowercase,
     Cyrillic, deduped, NO Russian contaminants, NO function words.

Cost: ~6-7 requests. Re-run freely (Non-Profit tier = 500/day).

Usage:
    export FORVO_API_KEY=your_key
    python test_forvo_assumptions.py
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

LANGUAGE = "uk"
BASE = "https://apifree.forvo.com"
PAGESIZE = 100
TIMEOUT_SEC = 30
REQUEST_DELAY_SEC = 1.5

# Probe seeds — real Ukrainian content words (test probes, NOT the seed list).
MAIN_SEED = "друг"
PROBE_WORDS = ["вода", "любити", "місто"]

SEEDS_FILE = Path("data/seeds_uk.txt")
OUT_DIR = Path("test_out")

# Tokeniser mirrors the assumed match rule: split on whitespace + apostrophes
# (straight/curly) + hyphen/dash.
TOKEN_RE = re.compile(r"[\s'’ʼ`\-—]+")

# Tiny audit sets for the seed-file sanity check.
RUSSIAN_CONTAMINANTS = {"что", "это", "ты", "был", "если", "очень", "сейчас"}
FUNCTION_WORDS = {"я", "не", "в", "на", "що", "як", "для", "але", "це", "і",
                  "з", "до", "від", "цей", "він", "вона", "ми", "ви"}

# --- output helpers ----------------------------------------------------------

def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s

def ok(s):   return _c("32", s)
def warn(s): return _c("33", s)
def bad(s):  return _c("31", s)
def dim(s):  return _c("2", s)

RESULTS = []

def record(status, label, detail=""):
    RESULTS.append((label, status))
    tag = {"PASS": ok("PASS"), "WARN": warn("WARN"), "FAIL": bad("FAIL")}[status]
    print(f"  [{tag}] {label}")
    if detail:
        for line in str(detail).splitlines():
            print(dim(f"        {line}"))

# --- api ---------------------------------------------------------------------

class Client:
    def __init__(self, key):
        self.key = key
        self.requests_used = 0

    def search(self, seed, page=1):
        self.requests_used += 1
        path = ("action/pronounced-words-search/"
                f"search/{urllib.parse.quote(seed)}/"
                f"language/{LANGUAGE}/pagesize/{PAGESIZE}/page/{page}")
        url = f"{BASE}/key/{self.key}/format/json/{path}"
        resp = requests.get(url, timeout=TIMEOUT_SEC)
        time.sleep(REQUEST_DELAY_SEC)
        return resp

def tokens(s):
    return [t for t in TOKEN_RE.split(s.lower()) if t]

def coerce_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def word_count(original):
    return len(original.split())

def save(name, obj):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / name, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)

def items_of(resp):
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text.strip()[:160]}"
    try:
        data = resp.json()
    except ValueError:
        return None, f"non-JSON: {resp.text.strip()[:160]}"
    if not isinstance(data, dict) or "items" not in data:
        return None, f"unexpected payload: {str(data)[:160]}"
    return data, None

# --- checks ------------------------------------------------------------------

def preflight(client):
    print(f"\nCHECK 0 — auth, envelope, fields (seed '{MAIN_SEED}')")
    resp = client.search(MAIN_SEED)
    if resp.status_code in (401, 403):
        record("FAIL", "Authentication", f"HTTP {resp.status_code}: check FORVO_API_KEY.")
        return None
    data, err = items_of(resp)
    if err:
        record("FAIL", "pronounced-words-search responds", err)
        return None
    save("sample_envelope.json", data)
    record("PASS", "Auth + envelope", f"keys: {list(data.keys())}")
    items = data.get("items") or []
    if not items:
        record("WARN", "Seed returns rows", f"'{MAIN_SEED}' returned nothing.")
        return data
    save("sample_item.json", items[0])
    for field in ("id", "original", "num_pronunciations"):
        record("PASS" if field in items[0] else "FAIL",
               f"Item has '{field}'", f"value: {items[0].get(field)!r}")
    return data

def check_token_rule(data):
    print(f"\nCHECK 1 — match rule is TOKEN-level (mid-phrase discovery works)")
    items = (data or {}).get("items") or []
    if not items:
        record("WARN", "Token-rule check skipped", "no data")
        return
    seed = MAIN_SEED.lower()
    matched, non_initial, neither = [], [], []
    for it in items:
        o = str(it.get("original", ""))
        toks = tokens(o)
        if any(t.startswith(seed) for t in toks):
            matched.append(o)
            if not o.lower().startswith(seed):
                non_initial.append(o)
        else:
            neither.append(o)
    record("PASS" if not neither else "WARN",
           "Every result has a token starting with the seed",
           f"{len(matched)}/{len(items)} matched; "
           f"{len(neither)} unexplained {neither[:3] if neither else ''}".strip())
    record("PASS" if non_initial else "WARN",
           "Mid-phrase (non-initial token) matches exist",
           f"{len(non_initial)} entries match on a later token, e.g. "
           f"{non_initial[:4]} — confirms seeding a WORD pulls phrases that "
           f"contain it anywhere, not just at the start.")

def check_inflections(data):
    print(f"\nCHECK 2 — inflection capture (lemma seed pulls inflected forms)")
    items = (data or {}).get("items") or []
    seed = MAIN_SEED.lower()
    single_forms = sorted({str(it.get("original", "")).lower()
                           for it in items
                           if word_count(str(it.get("original", ""))) == 1
                           and str(it.get("original", "")).lower().startswith(seed)})
    record("PASS" if len(single_forms) > 1 else "WARN",
           "Multiple surface forms of the lemma returned",
           f"{len(single_forms)} forms: {single_forms[:8]} — this is why the "
           f"builder lemmatises seeds (max prefix coverage).")

def check_phrase_yield(data):
    print(f"\nCHECK 3 — phrase yield for the seed")
    items = (data or {}).get("items") or []
    phrases = [(str(it.get("original")), coerce_int(it.get("num_pronunciations")))
               for it in items if word_count(str(it.get("original", ""))) >= 2]
    multi = [p for p in phrases if p[1] >= 2]
    top = "\n".join(f"{n}x  {o}" for o, n in sorted(phrases, key=lambda x: -x[1])[:8])
    record("PASS" if phrases else "WARN", "Multi-word phrases present",
           f"{len(phrases)} phrases, {len(multi)} with >=2 recordings.\n{top}")

def check_case(data):
    print(f"\nCHECK 4 — case-insensitivity")
    items = (data or {}).get("items") or []
    caps = [str(it.get("original")) for it in items
            if str(it.get("original")) != str(it.get("original")).lower()]
    record("PASS" if caps else "WARN", "Lowercase seed matches capitalised entries",
           f"e.g. {caps[:3]}" if caps else "no capitalised entries on this page")

def check_multi_seed_yield(client):
    print(f"\nCHECK 5 — realistic per-seed yield across content words")
    best_pages = (MAIN_SEED, 1)
    for word in PROBE_WORDS:
        resp = client.search(word)
        data, err = items_of(resp)
        if err:
            record("WARN", f"seed '{word}'", err)
            continue
        items = data.get("items") or []
        attrs = data.get("attributes") or {}
        tp = coerce_int(attrs.get("total_pages"), 1)
        if tp > best_pages[1]:
            best_pages = (word, tp)
        phrases = [it for it in items if word_count(str(it.get("original", ""))) >= 2]
        multi = [it for it in phrases if coerce_int(it.get("num_pronunciations")) >= 2]
        record("PASS", f"seed '{word}'",
               f"total={coerce_int(attrs.get('total'))}, "
               f"phrases on p1={len(phrases)}, with >=2 recs={len(multi)}")
    return best_pages

def check_pagination(client, best_pages):
    print(f"\nCHECK 6 — pagination sanity")
    word, total_pages = best_pages
    if total_pages <= 1:
        record("WARN", "Deep pagination not exercised",
               "All probe seeds fit on one page (typical for content words). "
               "The crawler still loops to total_pages, which was proven earlier.")
        return
    resp = client.search(word, page=total_pages)
    data, err = items_of(resp)
    last = (data.get("items") if data else None) or []
    record("PASS" if last else "FAIL", f"Last page of '{word}' ({total_pages}) returns data",
           f"{len(last)} items — no silent depth cap." if last else f"empty: {err}")

def check_seed_file():
    print(f"\nCHECK 7 — generated seed file sanity ({SEEDS_FILE})")
    if not SEEDS_FILE.exists():
        record("WARN", "Seed file present",
               f"{SEEDS_FILE} not found — run build_seeds.py first, then re-run.")
        return
    raw = [l.strip() for l in SEEDS_FILE.read_text(encoding="utf-8").splitlines()]
    seeds = [l for l in raw if l and not l.startswith("#")]
    record("PASS" if seeds else "FAIL", "Non-empty", f"{len(seeds)} seeds")

    multitoken = [s for s in seeds if len(s.split()) > 1]
    record("PASS" if not multitoken else "FAIL", "All single-token",
           f"{len(multitoken)} multi-word, e.g. {multitoken[:3]}" if multitoken else "ok")

    not_lower = [s for s in seeds if s != s.lower()]
    record("PASS" if not not_lower else "WARN", "All lowercase",
           f"{len(not_lower)} not lowercase, e.g. {not_lower[:3]}" if not_lower else "ok")

    non_cyr = [s for s in seeds if not re.fullmatch(r"[а-яґєіїّ'’ʼ\-]+", s)]
    record("PASS" if not non_cyr else "WARN", "All Cyrillic",
           f"{len(non_cyr)} odd, e.g. {non_cyr[:5]}" if non_cyr else "ok")

    dupes = len(seeds) - len(set(seeds))
    record("PASS" if dupes == 0 else "WARN", "Deduplicated", f"{dupes} duplicates")

    sset = set(seeds)
    ru = sorted(sset & RUSSIAN_CONTAMINANTS)
    record("PASS" if not ru else "FAIL", "No Russian contaminants",
           f"found: {ru}" if ru else "clean (Ukrainian-ness filter worked)")

    fn = sorted(sset & FUNCTION_WORDS)
    record("PASS" if not fn else "FAIL", "No function words",
           f"found: {fn}" if fn else "clean (POS filter worked)")

# --- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("FORVO_API_KEY"))
    args = ap.parse_args()
    if not args.key:
        print(bad("No API key. Set FORVO_API_KEY or pass --key."), file=sys.stderr)
        return 1

    client = Client(args.key)
    print("Forvo assumption validator v2 — language:", LANGUAGE)

    data = preflight(client)
    if data is None:
        print(bad("\nStopping: preflight failed."))
        return 1
    check_token_rule(data)
    check_inflections(data)
    check_phrase_yield(data)
    check_case(data)
    best = check_multi_seed_yield(client)
    check_pagination(client, best)
    check_seed_file()

    fails = [l for l, s in RESULTS if s == "FAIL"]
    warns = [l for l, s in RESULTS if s == "WARN"]
    print("\n" + "=" * 60)
    print(f"Requests used: {client.requests_used}   "
          f"PASS {sum(s=='PASS' for _, s in RESULTS)}  "
          f"WARN {len(warns)}  FAIL {len(fails)}")
    print(f"Raw samples in: {OUT_DIR.resolve()}")
    if fails:
        print(bad("VERDICT: fix the FAILs before crawling."))
        for l in fails:
            print(bad(f"  - {l}"))
        return 1
    print(warn("VERDICT: safe to crawl; review any WARNs.") if warns
          else ok("VERDICT: all new assumptions hold."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
