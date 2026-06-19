# Ukrainian Forvo Phrase Crawler

Discover high-signal Ukrainian **phrases** (not just words) for language
learning by mining [Forvo](https://forvo.com), then turn them into Anki cards.

The core idea: on Forvo, anyone can record a pronunciation, so a phrase that
**multiple native speakers independently chose to record** is the crowd telling
you it's worth knowing. Instead of guessing which phrases are useful and looking
them up, this project works backwards — it enumerates what natives recorded and
ranks phrases by how many recordings they have. More recordings = stronger
signal.

---

## How it works

### 1. Seeds — content words from frequency data (`build_seeds.py`)

Forvo's `pronounced-words-search` matches at the **token level**: an entry is
returned when any word *inside* it starts with your search term (tokens split on
spaces and apostrophes). So seeding the API with common **content words** pulls
back the phrases those words appear in — anywhere in the phrase, plus inflected
forms. That is why the seed list is a frequency word list, not the alphabet.

Frequency sources (choose with `--source`):

| source | what it is | character |
|---|---|---|
| `wordfreq` | blended multi-source list (Wikipedia, news, subtitles…) | balanced, conversational; e.g. surfaces verbs like `обіцяв` around rank ~8000 |
| `ukwiki` | Ukrainian Wikipedia frequency (IlyaSemenov, 1.29M entries) | natively Ukrainian, deep on nouns, **encyclopedic skew** (`населення`, `області`…) |
| `file` | a local `word [count]` file you provide | use to plug in the **purest** literary corpora: lang-uk **Brown-UK** / **UberText 2.0** |
| `opensubtitles` | hermitdave OpenSubtitles uk_50k | **legacy/contaminated** with Russian — not recommended |

By default the builder uses `wordfreq` alone (3000 kept content words). Note
that wordfreq **already blends Wikipedia** among its sources, using a weighted
median that damps Wikipedia's encyclopedic skew — so layering `ukwiki` on top
double-counts Wikipedia and re-introduces that skew. Reach for `ukwiki` only to
deliberately boost concrete nouns; for deeper everyday vocabulary (e.g. verbs
like `обіцяв`, ~rank 8000) raise `--per-source` on wordfreq instead.

Every source runs through the same filter to keep only "hefty" content words:

1. Ukrainian-letters only (drops Latin, digits, and Russian-only `ы э ъ ё`).
2. `pymorphy3` (lang=uk): keep words whose best parse is an open-class POS
   (noun / verb / adjective / adverb) **and** that have no function-word parse —
   this catches homonym mislabels like `його`→noun and `про`→noun.
3. Drop the (safe) Ukrainian stopword set, and the copula forms of `бути`
   (`є`, `був`, `буде`, …).
4. Drop words that are **function words in Russian** (`его`, `если`, …) using a
   second `pymorphy3` analyzer, and drop Russian **content** words (`очень`,
   `сейчас`, …) that morphology can't catch via a frequency-skew test — a word
   far more common in Russian than Ukrainian (`zipf_ru − zipf_uk ≥ 1.25`) is
   contamination. Both gates keep words genuinely shared by the languages
   (`друг`, `вода`).

Seeds are **surface forms** (not lemmas): pymorphy's lemmatiser corrupts common
words (`його`→`йога`), and distinct surface stems (`друг` / `друзі`) give broader
token-prefix coverage on Forvo anyway. Output: `data/seeds_uk.txt`, one seed per
line, in frequency order.

### 2. Crawl — rank phrases by recording count (`crawl_forvo.py`)

For each seed, query `pronounced-words-search` (`language=uk`), page through all
results, and dedup by Forvo `id` (heavy overlap is expected — a phrase matches
every content word it contains). Each row already carries `num_pronunciations`,
so the filter is local: keep **multi-word** entries with **≥2 recordings**, and
bucket them into tiers — `tier2`, `tier3`, `tier4plus`.

Output: `data/uk_phrases.csv` with `id, original, num_pronunciations,
word_count, tier, media_stem, forvo_url`. **Metadata only — no audio yet.**
`media_stem` is `uk_<id>`, the stable key that the later audio and image stages
attach to.

Budget & resumability: the Forvo Non-Profit tier allows **500 requests/day**
(reset 22:00 UTC); one results page is one request. The crawler checkpoints to
`data/` after every page, so a run that hits the cap resumes next time. Seeds are
in frequency order, so the most useful words are crawled first. Note that crawl
cost scales with seed count: ~3,000 seeds is a few thousand requests; an 8,000
seed list is proportionally more (days at 500/day, or one day on a paid tier).

### 3. Validate before spending budget (`test_forvo_assumptions.py`)

A cheap (~7 request) probe that confirms the live API still behaves as assumed —
token-level matching, field names, count accuracy, pagination, and a sanity pass
over the generated seed file (no Russian, no function words). Run it once before
the full crawl.

### 4. Audio pass — *separate, later*

For the surviving phrases only, call `word-pronunciations` and download the mp3s
as `uk_<id>.mp3`. Scoping this to the keepers keeps it cheap; note that every
audio play counts as a request and generated audio links expire after ~2 hours.

### 5. Anki cards — *later, will live in this repo*

Cards are built programmatically with [`genanki`](https://github.com/kerrickstaley/genanki).
The note type has three fields — **Image**, **Text**, **Audio** — with the image
on the front and text + audio on the back. Media travels *inside* the `.apkg`:
audio is referenced as `[sound:uk_<id>.mp3]`, images as `<img src="uk_<id>.jpg">`,
and the files are passed to genanki via `media_files`, matched by the same
`media_stem` from the CSV.

Images start **empty** (bespoke images are generated separately, e.g. with
ChatGPT, and dropped in later). The card template uses a conditional
(`{{#Image}}…{{/Image}}`) so cards stay reviewable with a text/audio fallback
until their image exists.

---

## Repo layout

```
.
├── .github/workflows/forvo-crawl.yml   # GitHub Actions runner (the crawl)
├── build_seeds.py                      # 1. build data/seeds_uk.txt
├── test_forvo_assumptions.py           # 2. validate the API (local)
├── crawl_forvo.py                      # 3. crawl -> data/uk_phrases.csv
├── requirements.txt                    # crawler/validator deps (requests)
├── requirements-seeds.txt              # seed-builder deps (NLP; local only)
├── data/
│   ├── seeds_uk.txt                    # generated seeds (commit this)
│   └── uk_phrases.csv                  # crawl output (written by the runner)
└── README.md
```

---

## How to run

Requires Python 3.10+.

### 1. Build the seed list (local, one-time)

The builder needs heavier NLP libraries, so it runs locally — not in the
GitHub runner.

```bash
pip install -r requirements-seeds.txt
python build_seeds.py                                       # wordfreq, 3000 seeds (balanced default)
# python build_seeds.py --per-source 8000                     # deeper everyday vocab (verbs)
# python build_seeds.py --sources ukwiki                       # Wikipedia only (noun-heavy, niche)
# python build_seeds.py --sources file --file bruk_freq.txt    # Brown-UK / UberText
```

Commit the resulting `data/seeds_uk.txt`.

### 2. Validate the API (local, ~7 requests)

```bash
pip install -r requirements.txt
export FORVO_API_KEY=your_key_here          # Windows: set FORVO_API_KEY=...
python test_forvo_assumptions.py
```

Aim for an all-PASS verdict before crawling.

### 3a. Crawl locally

```bash
python crawl_forvo.py        # resumes automatically; writes data/uk_phrases.csv
```

### 3b. Crawl on GitHub Actions (recommended for the 500/day cap)

1. Push the repo, **including `data/seeds_uk.txt`** and
   `.github/workflows/forvo-crawl.yml` (the workflow only crawls; it does not
   build seeds).
2. Add the API key as a repository secret named **`FORVO_API_KEY`**
   (Settings → Secrets and variables → Actions → New repository secret).
3. Actions tab → "Forvo Ukrainian phrase crawl" → **Run workflow**. The daily
   schedule then resumes the crawl automatically if a run hits the cap.
4. The runner commits `data/uk_phrases.csv` (and checkpoints) back to the repo.
   `git pull` to retrieve your phrase list.

### 4. Audio + Anki — later stages

Run the audio pass over `uk_phrases.csv`, then build the deck with genanki.

---

## Sources & attribution

- **Forvo API** — Non-Profit tier: non-commercial use with attribution to Forvo.
- **Frequency data** — `wordfreq` (Robyn Speer); IlyaSemenov *wikipedia-word-frequency*
  (Ukrainian Wikipedia, CC BY-SA); lang-uk **Brown-UK** / **UberText 2.0**;
  hermitdave **FrequencyWords** (OpenSubtitles, CC BY-SA).
- **NLP** — `pymorphy3` + `pymorphy3-dicts-uk` / `-ru` (MIT code, CC BY-SA dicts);
  `stopwordsiso`.
