# Changelog

Dated history of the Smart Playlist Creator (formerly "Deckard DJ Agent").
Reconstructed from session logs and git history; going
forward, add an entry per shipping session. Newest first.

Format loosely follows Keep-a-Changelog. Dates are the working session, not release.

---

## 2026-09-17 — public-release prep

- **Fixed** — tracks whose filename contains an apostrophe or quote (e.g.
  `What's Going On.mp3`) silently failed to add or preview from the Best Mixes /
  Harmonic Neighbours panels: the raw quote broke the onclick handler. Filenames
  are now escaped for JS + HTML (`jsq()`) everywhere they're passed to a handler.
- **Added** — vibe tags are now configurable: `vibe_tag_min_count` (default 8;
  lower it for a small library) and `vibe_tag_exclude` (terms to hide) in
  `config.json`. Vibe tags are mined live from track comments.
- **Docs** — README rewritten (problem-first, energy-tier + vibe-tag explainers);
  docs and code comments depersonalized for open-source release.

## 2026-08-20 — v1.0 released 🎉 (tag `v1.0`)

**Export sign-off passed** — the RC1 → v1.0 gate. Manual GUI round-trips against the live
library (QA-TEST-PLAN TC-101–103):
- **TC-101 rekordbox XML import** — ✅ tracks import in order; comments + key preserved.
- **TC-102 direct → RB write** — ✅ playlist written + populated; `master.db` backed up first.
- **TC-103 Traktor NML load** — ✅ after the fix below.

**Bug found + fixed during sign-off — NML export is now self-contained.** The NML export wrote a
playlist-only file (bare `PRIMARYKEY` pointers, no `COLLECTION`), so Traktor imported an *empty*
playlist — Traktor populates from the file's own `COLLECTION`, not by matching pointers against the
already-loaded library. Fix: embed a `<COLLECTION>` with the **verbatim source `<ENTRY>`** per
track, so the file is self-contained and imports at **full fidelity** — genre energy-prefix,
ratings, comments, keys, cue points, and beatgrids all preserved. Automated QA couldn't catch this
(it validates file structure, not Traktor's import behavior) — the manual gate earned its keep.
(`app.py`: new `_split_nml_key` / `_load_source_entries` / `_synth_nml_entry`, rewritten `api_export_nml`.)

**Deferred to v1.1:** rekordbox XML + direct-write metadata parity (both flatten the `N -` genre
energy prefix; the direct write also omits Comment/Key). The Traktor NML path already carries all of
it. See ROADMAP.md (#15).

---

## 2026-08-11/12 — v1.0 push + Gig Check + one-true-library finalized

**v1.0 core (export path):**
- **NML export edge cases fixed** (PR #2) — rekordbox-sourced key synthesis + cross-source
  warning, `ENTRIES` counts actual entries, stable-`id` lookup (basename collisions), skip
  empty PRIMARYKEY, clean JSON on bad body. Export paths machine-verified live.
- **Set Builder drag-to-reorder** (#3) — ⋮⋮ handle, HTML5 DnD, cyan drop indicator.
- **Harmonic flow warnings** (#4) — Camelot clash detection between adjacent set tracks
  (red accent + ⚠ tooltip + set-header clash count), live on reorder.

**Gap-fixing & library intelligence:**
- **BPM-from-filename** — parse the trailing bpm in MIK-named files; missing-BPM 1,464 → 653.
- **Gig Check** — `GET /api/playlists` + modal: every playlist's online/offline readiness
  (409 playlists, 403 fully online), worst-first, expandable missing tracks.
- **Rate Mode energy (1/2/3)** — new `deckard_energy.json` store + `/api/track/setenergy`;
  the card pre-fills existing rating AND energy so you can update either.
- **Apple Music playlist** — create directly in Music via AppleScript (imports the files;
  the `.m3u8` import left the playlist empty). `.m3u8` export retained for other tools.
- **➕ Add all → Set**, **path hover tooltip**, **multi-drive scope** (+ ★ Foundation +
  scope banner), **collapsible filter panel**, clearer **In Library / Shown** counts.

**Docs & ops:**
- Captured a standalone iOS "Gig Check" feasibility note (native, reads USB `export.pdb`).
- **One-true-library finalized (infra, off-repo):** copied 173 Dropbox-only files to the
  NAS; made all local Dropbox audio **online-only** (~130 GB reclaimed, 0 materialized
  left); Cloud Sync switched to **one-way NAS→Dropbox, append-only**; the NAS via
  AutoMounter. Twice-a-year Dropbox declutter scan scheduled (launchd, Jan 1 + Jul 1).

## 2026-08-10 — "one true library" session (branch `library-drives-panel`, 6 commits)

- **Genre cleanup** — 441 raw Traktor genres → 31 canonical (`normalize_genre` /
  `GENRE_RULES` in `parser.py`); `genre_raw` / `genre_sub` preserved.
- **Era decades** (80s–2020s from release year) + **Gaps/To-Do dashboard** (per-field
  missing counts, `missing=` filter).
- **Rating Mode** — fast 1–5 keyboard loop over unrated tracks; saved to
  `deckard_ratings.json` and overlaid on load. Works for offline / non-MP3 tracks.
- **Re-linker** — re-matches stale NML paths (old `/Users/olduser/` home, MIK-renamed
  filenames, reorg moves) to real files. 12,466 → 15,150 re-linked; offline 8,725 → 683.
- **NAS-primary resolution** — 0-byte online-only Dropbox stubs resolve to their NAS twin
  (the volume named by config `nas_root`); library plays again. `/api/audio` returns 409 for stubs.
- **Collapse-duplicates view** — one row + `×N` badge per song; a large (~20k-track)
  library collapses to roughly two-thirds unique. Non-destructive.

## 2026-08-03 — Library Drives panel

- **Library Drives panel** — collapsible view of every volume the library spans, each
  with status dot, track count, share bar, mounted/offline label. Auto-expands + amber on
  offline. New `GET /api/library/volumes` + `library_volumes()` (`offline_summary` becomes
  a thin filter over it). Verified: 8 volumes, 8,722 offline across 6 drives.
- **Drive filters** — click a drive row to isolate it; "Hide offline" toggle.
  `/api/library/search` gains `volume` + `online_only` (server-side).

## 2026-08-02 — usable on the real library

- **Offline-volumes banner** — when part of the library is on an unplugged drive, show
  "X tracks offline — mount DRIVE" instead of failing track-by-track. New
  `GET /api/library/offline` + `offline_summary()` / `_volume_root()`. Mount state
  resolved via a single `os.listdir('/Volumes')` snapshot (a per-file `stat()` on a
  sleeping NAS blocks and wedges the endpoint — caught live).
- **Track-list render freeze** — confirmed already fixed: server pages via `limit`/
  `offset`, front-end infinite-scrolls 300 rows/page. 40,990-track library paints 300 rows.

## Earlier (shipped, dates approximate — from git history)

- **`usb_preflight.py` + hardening** (`ea7f438`) — standalone gig-safety CLI: verifies a
  USB stick carries a real, fresh rekordbox export. Exit map 0 pass / 1 hard fail /
  2 not-mounted / 3 warnings; whole-stick audio scan; read-error surfacing.
- **Native Traktor playlist key for NML export** (`565daf0`, `nml_key`).
- **Right-panel readability** (`7d66ace`).
- **Acquisition-vs-release year filters + Load-Set import** (`e00b2d1`, `f063ac8`).
- **rekordbox as a library *source*** (`eafb938`) — read `master.db` directly.
- **Initial public release** (`0c240b8`).

---

## Origin — Deckard DJ Agent (design + first build)

> The project started life as an AI classifier/organizer vision, then narrowed into the
> set-builder it is today. These sessions predate the git repo.

## 2026-03-03 — Flask app v1.0 live

- DJ Library Intelligence Tool went concept → deployed local app in one session.
  19,374 tracks loading from Traktor NML; ~90.7% Camelot key coverage via filename
  extraction; 833 tracks matched with MIK energy data. Full REST API; Blade Runner UI.
- Patches: **Day/Night mode toggle** (localStorage); **Add to Set from Harmonic
  Neighbours** (fixed missing `filename` in neighbours API + `by-filename` fallback);
  **multi-column sort** with direction + Option-click secondary/tertiary; +20% font size.

## 2026-02-28 — Design & architecture sprint

- Established the vision, the **Deckard Energy System** (two-axis rating + 1/2/3 energy
  prefix), the genre taxonomy (10 aspirational parent genres), the classifier confidence
  tiers, and the data pipeline. Produced `DECKARD_AGENT_VISION.md` +
  `DECKARD_GENRE_TAXONOMY.md`. (The AI-classifier/organizer described here was never
  built — see `DOMAIN.md` for what carried forward vs. what didn't.)
