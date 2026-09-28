# CLAUDE.md — Smart Playlist Creator by Deckard

> *This file orients contributors (and Claude Code) working **on** the app's code. You don't need it to **use** the app — see [README](README.md) / [QUICKSTART](QUICKSTART.md) for that.*

Guidance for Claude Code (and any human picking this up) when working in this repo.
This is the keystone doc — read it first. It links out to the deeper references.

## What this is

A **local-first DJ set-building app**. It turns your existing DJ
library into smart, mix-ready playlists: pick a track and it suggests what to play
next the way you actually think — **genre → energy → BPM → key** — then exports the
set to rekordbox (or Traktor). Everything runs on the local machine; nothing goes to
the cloud.

- **Public name:** Smart Playlist Creator.
- **Repo:** `kah1970/Smart-Playlist-Creator-by-Deckard` — MIT-licensed, free and open source.
- **Stack:** Python 3 + Flask backend (`app.py`), single-page UI (`Templates/index.html`),
  library parsing in `parser.py`. No build step, no framework on the front end.
- **License:** MIT.

> Lineage: this project began as the "Deckard DJ Agent" — an AI classifier/organizer
> vision. It has since **narrowed** into a focused set-builder. The AI-classification /
> Beatport / discovery modules from the original vision are **not built** and are not the
> current direction. Treat `DOMAIN.md` + `ROADMAP.md` as current truth.

## How to run

```bash
cd smart-playlist-creator
./launch.sh          # installs Flask if needed, serves http://localhost:5001
```

- **Port:** defaults to **5001** (5000 is reserved by macOS AirPlay Receiver). Override with
  `SPC_PORT=<n> ./launch.sh`. If it's stuck: `lsof -ti tcp:5001 | xargs kill` then `./launch.sh`.
- **`app.py` edits** need a server restart. **`Templates/index.html` edits** need only a
  browser hard-refresh (⌘⇧R) — the template is served fresh, no restart.
- **Config:** `config.json` (git-ignored; copy from `config.example.json`) holds
  `nml_path`, `mik_path`, `rb_path`, `source`. First run opens an in-app config screen
  with Browse buttons — paths are not hardcoded.
- **Optional per-user keys** (blank by default, so the features no-op for others):
  `dropbox_root` + `nas_root` drive the **path re-linker** (`_relink_roots` in `app.py` —
  index a local library tree, resolve online-only stubs to a NAS twin); `foundation_volume`
  names the drive shown as **★ Foundation** in the UI. These are your setup; keep them
  config-driven, never hardcode paths.

## Library sources & data

- Source is **either** Traktor `collection.nml` (+ Mixed In Key "All Playlists" JSON)
  **or** a rekordbox `master.db`. Selected via `config.json:source`.
- **`deckard_ratings.json`** — Deckard-side rating overlay written by Rating Mode.
  Universal (works for offline / non-MP3 tracks); overlaid on load. This is app-owned
  state, not the user's library.
- Big binaries (`*.nml`, `*.db`, `* copy*`) are **git-ignored** — real source files
  live outside the repo; `config.json` points at them.

## Hard rules — do not violate

These are load-bearing. Getting one wrong can corrupt the user's live DJ library or
wedge the app.

- **Never hand-edit the live Traktor `collection.nml`.** Use Lexicon or a verified
  script. The app reads it; it must not casually rewrite it.
- **Never `stat()` per-file on a sleeping network share.** A hung SMB/NFS mount (a
  sleeping music NAS) blocks for the full network timeout and wedges the
  endpoint. Resolve mount state by name against a single `os.listdir('/Volumes')`
  snapshot — see `_mounted_volume_names()` / `_nas_mounted()`.
- **Do not "fix" the `N - Genre` energy prefix** in Traktor/rekordbox genre tags (e.g.
  `2 - House`). It looks redundant but is the user's **deliberate combined energy+genre
  sort** — those apps can't multi-sort, so they pack both into one field. Deckard splits
  it cleanly in its own view (`parse_genre_field`). See `DOMAIN.md`.
- **Publishing is the user's call — do not flip the repo public, force-push, or push to
  `origin` without explicit say-so.** (The MIT license is already in place.)
- **Keep it reversible.** Direct rekordbox DB writes back up `master.db` first; ratings,
  dedup and moves are logged. The **XML export is the safe default**; the direct DB write
  (via `pyrekordbox`) is unofficial and can break on a rekordbox update.
- **Verify before claiming done.** This app is validated live against a large (~20k-track) real
  library. When you change parsing/export/resolution, run it and confirm counts, don't
  assume.

## Where things live / deeper docs

- **`DOMAIN.md`** — the Deckard Energy System, rating scale, canonical genre taxonomy,
  Camelot key handling. The *why* behind the classifications.
- **`ARCHITECTURE.md`** — `app.py` / `parser.py` structure, the re-link/resolution layer,
  NAS-primary logic, data stores, and the full REST API surface.
- **`ROADMAP.md`** — dev status, deferred fixes (NML export edge cases), future features.
  Known-broken edges live here, honestly.
- **`CHANGELOG.md`** — dated history of what shipped.
- **`README.md`** — user-facing intro + install. **`MANUAL.html`** — DJ field manual.

## Contact

team@djdeckard.com
