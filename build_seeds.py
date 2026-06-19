#!/usr/bin/env python3
"""
Build the crawler's seed list: Ukrainian content-word seeds, frequency-ordered.

Sources (combine any, deduped across them in the order given)
-------------------------------------------------------------
  wordfreq      pip 'wordfreq' — blended, balanced, conversational. Surfaces
                everyday verbs (обіцяв ~rank 8000). Default, listed first.
  ukwiki        Ukrainian Wikipedia frequency (IlyaSemenov, 1.29M entries).
                Natively Ukrainian, deep on nouns, encyclopedic skew.
  file --file P local "word [count]" file — plug in the PUREST literary lists:
                lang-uk Brown-UK / UberText (https://lang.org.ua, github/brown-uk).
  opensubtitles hermitdave OpenSubtitles uk_50k — legacy, Russian-contaminated.

Default is `--sources wordfreq --per-source 3000`. wordfreq ALREADY blends
Wikipedia (with a weighted median that damps its encyclopedic skew), so adding
`ukwiki` double-counts Wikipedia and re-introduces exactly the encyclopedic tail
wordfreq down-ranked. Use `ukwiki` only to deliberately boost concrete nouns; to
reach deeper everyday vocabulary (verbs like обіцяв ~rank 8000) raise
`--per-source` on wordfreq instead.

Filtering (every source)
------------------------
  * Ukrainian letters only (drops latin/digits and Russian-only ы э ъ ё).
  * pymorphy3 (uk): keep best-parse open-class POS (noun/verb/adj/adverb) with
    NO function-word parse (catches його->noun, про->noun mislabels).
  * Drop uk stopwords and copula forms of бути (є, був, буде, ...).
  * Drop Russian function words (его, если, ...) via a Russian analyzer, while
    keeping content words shared by both languages (друг, вода).
  * Drop Russian CONTENT words (очень, сейчас, ...) that are valid-looking and
    morphology can't catch, using a frequency-skew test: drop if a word is far
    more frequent in Russian than Ukrainian (zipf_ru - zipf_uk >= 1.25).
Seeds are SURFACE forms (lemmatising corrupts його->йога; distinct stems give
broader Forvo token-prefix coverage). Output: data/seeds_uk.txt.

Install:  pip install -r requirements-seeds.txt
Usage:
    python build_seeds.py                                   # wordfreq + ukwiki
    python build_seeds.py --sources wordfreq --per-source 8000
    python build_seeds.py --sources file --file bruk_freq.txt
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

CONTENT_POS = {"NOUN", "ADJF", "ADJS", "COMP", "VERB", "INFN",
               "PRTF", "PRTS", "GRND", "ADVB"}
FUNCTION_POS = {"NPRO", "PREP", "CONJ", "PRCL", "INTJ", "PRED"}
MIN_FUNC_SCORE = 0.25
RU_FUNC_MASS = 0.3
RU_SKEW_DELTA = 1.25   # drop if zipf_ru - zipf_uk >= this (Russian content words)

UK_WORD = re.compile(r"^[абвгґдежзийіїклмнопрстуфхцчшщьюяє'’ʼ\-]+$")
MIN_LEN = 3

OPENSUBS_URL = ("https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
                "master/content/2016/uk/uk_50k.txt")
UKWIKI_URL = ("https://raw.githubusercontent.com/IlyaSemenov/"
              "wikipedia-word-frequency/master/results/ukwiki-2022-08-30.txt")

# Copula forms of "бути" (to be): pass the POS gate as verbs but aren't "hefty".
COPULA = {"бути", "є", "був", "була", "було", "були", "буде", "буду", "будеш",
          "будемо", "будете", "будуть", "будь", "будучи", "бувши"}

OUT_PATH = Path("data/seeds_uk.txt")


# --- candidate sources -------------------------------------------------------

def _download_word_count(url, limit, note=""):
    import requests
    print(f"Downloading{note}:\n  {url}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines():
        parts = line.split()
        if parts:
            out.append(parts[0])
        if len(out) >= limit:
            break
    return out


def candidates_wordfreq(limit):
    import wordfreq
    if "uk" not in wordfreq.available_languages():
        raise RuntimeError("wordfreq has no 'uk' data")
    return wordfreq.top_n_list("uk", limit)


def candidates_file(path, limit):
    words = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(line.split()[0])
        if len(words) >= limit:
            break
    return words


def get_candidates(source, file_path, limit):
    if source == "wordfreq":
        return candidates_wordfreq(limit)
    if source == "ukwiki":
        return _download_word_count(UKWIKI_URL, limit, " Ukrainian Wikipedia list")
    if source == "opensubtitles":
        return _download_word_count(OPENSUBS_URL, limit, " (legacy, contaminated)")
    if source == "file":
        if not file_path:
            raise SystemExit("--sources file requires --file PATH")
        return candidates_file(file_path, limit)
    raise SystemExit(f"unknown source: {source}")


# --- filter ------------------------------------------------------------------

def reject_reason(word, morph, morph_ru, uk_stop, wf):
    """Return a rejection reason, or None if the word is a keepable seed."""
    if len(word) < MIN_LEN:
        return "too_short"
    if not UK_WORD.fullmatch(word):
        return "not_ukrainian"
    if word in uk_stop:
        return "stopword"
    if word in COPULA:
        return "copula"
    parses = morph.parse(word)
    if parses[0].tag.POS not in CONTENT_POS:
        return "not_content_pos"
    if any(p.tag.POS in FUNCTION_POS and p.score >= MIN_FUNC_SCORE for p in parses):
        return "function_homonym"
    if morph_ru is not None:
        ru_mass = sum(p.score for p in morph_ru.parse(word)
                      if p.tag.POS in FUNCTION_POS)
        if ru_mass >= RU_FUNC_MASS:
            return "russian_function"
    if wf is not None:
        u = wf.zipf_frequency(word, "uk")
        if u > 0 and wf.zipf_frequency(word, "ru") - u >= RU_SKEW_DELTA:
            return "russian_skew"   # far more frequent in Russian than Ukrainian
    return None


# --- build -------------------------------------------------------------------

def build(sources, per_source, max_candidates, file_path):
    try:
        import pymorphy3
    except ImportError:
        print("Run: pip install -r requirements-seeds.txt", file=sys.stderr)
        raise
    morph = pymorphy3.MorphAnalyzer(lang="uk")
    try:
        morph_ru = pymorphy3.MorphAnalyzer(lang="ru")
    except Exception:
        morph_ru = None
        print("note: pymorphy3-dicts-ru unavailable; skipping Russian gate.")
    try:
        import stopwordsiso
        uk_stop = stopwordsiso.stopwords("uk")
    except Exception:
        uk_stop = set()
        print("note: stopwordsiso unavailable; relying on POS gate only.")

    try:
        import wordfreq as wf
    except Exception:
        wf = None
        print("note: wordfreq unavailable; skipping Russian-skew gate.")

    per_source_lists = {}
    for source in sources:
        cands = get_candidates(source, file_path, max_candidates)
        print(f"Source '{source}': {len(cands)} candidates.")
        kept = []
        for raw_word in cands:
            word = raw_word.strip().lower()
            if reject_reason(word, morph, morph_ru, uk_stop, wf) is not None:
                continue
            kept.append(word)
            if len(kept) >= per_source:
                break
        per_source_lists[source] = kept

    # Union across sources, preserving order (earlier source wins position).
    seen, seeds = set(), []
    for source in sources:
        for word in per_source_lists[source]:
            if word not in seen:
                seen.add(word)
                seeds.append(word)
    per_source_kept = {s: len(per_source_lists[s]) for s in sources}
    overlap = sum(per_source_kept.values()) - len(seeds)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "# Ukrainian content-word seeds for the Forvo phrase crawler.",
        f"# Generated: {date.today().isoformat()}",
        f"# Sources (per_source={per_source}, deduped): "
        + ", ".join(f"{s}={per_source_kept[s]}" for s in sources),
        "# Filter: uk letters + pymorphy3 open-class POS (no function parse) +",
        "#         uk stopwords + copula(бути) + Russian-function gate.",
        "# Surface forms, in the source order above (frequency within each).",
    ]
    OUT_PATH.write_text("\n".join(header + seeds) + "\n", encoding="utf-8")

    print("\n--- build_seeds summary ---")
    for s in sources:
        print(f"  kept from {s:12s}: {per_source_kept[s]}")
    print(f"  overlap removed     : {overlap}")
    print(f"  total (deduped)     : {len(seeds)} -> {OUT_PATH}")
    print("  preview             : " + ", ".join(seeds[:14]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+",
                    choices=["wordfreq", "ukwiki", "file", "opensubtitles"],
                    default=["wordfreq"])
    ap.add_argument("--per-source", type=int, default=3000,
                    help="kept content seeds to take from each source")
    ap.add_argument("--max-candidates", type=int, default=40000,
                    help="frequency lines to scan per source to reach per-source")
    ap.add_argument("--file", dest="file_path", default=None)
    args = ap.parse_args()
    build(args.sources, args.per_source, args.max_candidates, args.file_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
