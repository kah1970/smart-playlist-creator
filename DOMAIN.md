# DOMAIN.md — The Deckard Energy System & Taxonomy

The domain model behind the app: how Deckard thinks about energy, genre, rating and
key. Ported from the project's original design notes and reconciled
against what the code (`parser.py`) actually implements today. **Where the two differ,
the code is truth and this doc says so.**

Why this matters: the app's whole job is to rank "what to play next" the way the user
does. That ranking is **genre → energy → BPM → key**. The concepts below are what those
words mean to them.

---

## 1. The two-axis rating (1–5)

Ratings are **not** a simple quality scale. They're a hybrid: the low end categorizes,
the high end grades.

**Axis 1 — Track Type (1–2):** categorization, not quality.
- **1** — Interesting but not often played; collector's item.
- **2** — Specific use: DJ tool, acapella, white noise, novelty, date-based.

**Axis 2 — Quality (3–5):** for playable tracks. This is the user's own scale of **how
good a song is at doing what it does** — how well it accomplishes what it's *trying* to
be, in their ears. It correlates with energy at times, but it's primarily an **overall
quality** judgement, not an energy judgement.

- **3** — Good, but the user likes it less. The song most likely isn't doing its best at
  accomplishing what it *could* be. A 3-star **chill** or **downtempo** track doesn't mean
  "low energy" — it means it falls short of the best of what a chill/downtempo track can be.
- **4** — Really good. It does its job well.
- **5** — Absolute banger. The ones — a song firing on all cylinders at what it's for.

The key idea: a **5-star chill track** and a **5-star peak-time banger** are equally
5-star. The rating grades each song against *its own kind*, not against a floor-filling
ideal.

**This 1–5 star rating is separate from the 1–3 energy prefix** that goes in front of the
genre (see §2). Energy prefix = where a track sits in the peak-time arc; star rating = how
good it is at being what it is. A track has both, independently (e.g. a `1 - Downtempo`
track rated 5★ = a deep/atmospheric track that is *excellent* at being deep and
atmospheric).

In code: Traktor stores this as `RANKING` (0/51/102/153/204/255 → 0–5★),
`traktor_rating_to_stars()` in `parser.py`. Deckard's own Rating Mode writes to
`deckard_ratings.json` and overlays it, so ratings work even for offline / non-MP3
tracks.

---

## 2. The energy prefix (1 / 2 / 3) and the `N - Genre` field

Every organized track's **genre field** is packed as `[energy] - [genre]`, e.g.
`2 - House`, `3 - Breakbeat`, `1 - Downtempo`.

- **1 -** Low energy / deep / atmospheric.
- **2 -** Mid energy / building / main-floor warm-up.
- **3 -** High energy / peak time / full floor.

**This combined field is deliberate.** Traktor and rekordbox can't sort by two columns
at once, so the user encodes both energy *and* genre into the single genre string to get a
usable sort inside those apps. **Do not "clean this up."** Deckard splits it back apart
on load via `parse_genre_field()` → `(energy_prefix, genre_name)`, so its own UI can
filter energy and genre independently without touching the source tags.

Note: this energy prefix is **distinct** from the 1–5 rating above, and also distinct
from Mixed In Key's 1–10 energy score. Three different "energy" numbers — keep them
straight:
- **Folder/genre prefix 1–3** — the user's own peak-time bucketing (deliberate, manual).
- **Rating 1–5** — the two-axis hybrid above.
- **MIK energy 1–10** — algorithmic, from Mixed In Key export; sparse coverage today.

---

## 3. Genre taxonomy — code truth vs. original vision

### What the code actually does

`parser.py` collapses the **~441 raw Traktor genre strings** in the user's library into a
**canonical set of ~31** so the Genre filter stays clean and diggable. This happens in
`GENRE_RULES` + `normalize_genre()`, applied **after** the `N - ` energy prefix is
stripped. Rules are ordered, first substring match wins (specific → general); unknown
non-empty strings fall through to `Other`. The raw genre (`genre_raw`) and sub-genre
(`genre_sub`) are preserved untouched on each track.

**Canonical genres (current, from `GENRE_RULES`):**

DJ Tools · Xmas · Deep House · Tech House · Bass House · Funk House · Jackin House ·
House · Nu Funk · Bass Funk · Ghetto Funk · Nu Disco · Disco · Big Beat · Breakbeat ·
Drum & Bass · Bass · Techno · Trance · Electro · Hip-Hop · Reggae/Dub · 80s · Pop ·
Rock · Indie · Blues/Jazz · Funk/Soul · Downtempo/Chill · Mash-Up · (+ `Other`)

> The **ordering matters** — e.g. `Deep House`/`Tech House`/`Funk House` are matched
> before the catch-all `House`; `Nu Funk`/`Nu Disco` before `Funk/Soul`/`Disco`. If you
> add a rule, put the specific case above the general one. Edit `GENRE_RULES` in
> `parser.py` — that list is the single source of truth for the Genre filter.

### The original vision (historical)

The design docs described **10 aspirational "parent genres"** (House, Tech House, Bass
House, Dirty House, Breakbeat, Big Beat, Nu Funk, Nu Disco, Downtempo, Chill) with a
folder-organizer that would file tracks into `[Parent]/[Energy] - [Genre]/[Sub-Genre]/`.
That organizer/classifier was never built. The core *principles* still hold and are worth
knowing:

- **Genre is assigned before energy.** A track's parent genre is decided first, then its
  energy prefix within that genre.
- **Some genres are first-class parents, not sub-folders.** Nu Funk is never filed under
  Breakbeat; Nu Disco is never filed under House — even when BPM would suggest otherwise.
  "Funk DNA / disco DNA wins classification."
- **Ground truth from the library.** Existing folder structure and tags are the source of
  truth, not industry defaults.

---

## 4. Key handling (Camelot)

- Keys are handled on the **Camelot wheel** (1A–12A minor, 1B–12B major), with a
  Camelot ↔ Western toggle in the UI.
- Harmonic neighbours computed by `camelot_neighbours()` in `parser.py`; surfaced via the
  Harmonic Neighbours panel and folded into mix suggestions.
- Key coverage comes largely from **filename extraction** (the user's ` - 8A - 128 `
  MIK naming convention), `extract_key_from_filename()` — ~90% coverage on the real
  library. rekordbox keys map via `rb_key_to_camelot()`.

---

## 5. Terminology

- **Vignette** — a group of tracks that play well together, sequenced and tagged with
  hashtags in the Comments field (e.g. `#GothSwing`, `#RaveTrap`).
- **Energy prefix** — the `1/2/3` in the `N - Genre` field. Peak-time bucketing.
- **Canonical genre** — one of the ~31 normalized genres the app filters on.
- **Re-link / resolution** — the runtime step that re-matches stale library paths to real
  files on disk (see `ARCHITECTURE.md`).
