# ARCHITECTURE.md — How the app is built

A map of the codebase so a change lands in the right place. Two Python files do the
work; the front end is a single served template. No build step, no client framework.

```
launch.sh            one-command launcher (installs Flask if missing, runs app.py)
app.py               Flask server: config, library cache, resolution layer, REST API
parser.py            pure library parsing: Traktor NML / MIK JSON / rekordbox DB → tracks
Templates/index.html single-page UI (Blade Runner theme; day/night toggle)
config.json          local paths + source selector (git-ignored)
deckard_ratings.json app-owned rating overlay written by Rating Mode
usb_preflight.py     standalone gig-safety CLI (not part of the web app)
```

`app.py` is ~1300 lines, `parser.py` ~600. Keep parsing logic in `parser.py` (no Flask
imports there) and HTTP/state logic in `app.py`.

---

## Data pipeline

```
config.json (source = traktor | rekordbox)
        │
        ▼
parser.load_library(nml, mik)      OR    parser.load_rekordbox(master.db)
  · load_traktor_nml()                     · rb_key_to_camelot()
  · load_mik()                             · (reads via pyrekordbox)
  · merge_libraries()  ← MIK matched by normalized filename stem
  · parse_genre_field() → (energy, genre) · normalize_genre() → canonical
  · extract_key_from_filename() → Camelot
        │
        ▼
app.get_library()   ← cached in-process; bust_cache() on reload/config change
        │
        ▼
app._relink_library(tracks)   ← RESOLUTION LAYER (see below)
        │
        ▼
REST API  ─────────────────►  Templates/index.html (fetch + render)
```

`get_library()` builds the track list once and caches it in-process; `bust_cache()`
clears it on `/api/reload` or a config change. Every request works off that cache.

---

## The resolution / re-link layer (the subtle part)

The user's Traktor NML paths are stale on **three independent axes**, so a raw parse points
at files that don't exist. This layer re-matches tracks to real files at runtime —
it does **not** rewrite the NML.

- **Stale home dir** — old `/Users/olduser/` paths.
- **MIK-renamed filenames** — MIK appended ` - <key> - <bpm>` to filenames after the NML
  was written.
- **Reorg moves** — files physically moved between folders/drives.

Key functions in `app.py`:
- `_norm_stem(filename)` — normalizes a filename (drops the MIK ` - key - bpm ` suffix,
  case/space folding) so moved/renamed files still match.
- `_build_relink_index()` — indexes real files on disk by normalized stem.
- `_relink_library(tracks)` — re-points each track to its real file via that index.
  Result on the live library: ~12.5k → 15.1k tracks re-linked; offline dropped 8,725 → 683.

### NAS-primary resolution
Online-only Dropbox stubs (0 bytes) resolve to their **NAS twin** under
the volume named by config `nas_root` (the real files). Materialized local copies still win, so recent
tracks stay fast. `/api/audio` returns **409** for a stub that can't be resolved. This is
a **runtime resolution fix**, not an official re-point of the NML/config (that's a future
phase).

### NAS mount safety
`_mounted_volume_names()` / `_nas_mounted()` resolve mount state from a **single**
`os.listdir('/Volumes')` snapshot — never a per-file `stat()`, which blocks for the full
network timeout on a sleeping share and would wedge the endpoint. See the hard rules in
`CLAUDE.md`.

---

## View-layer transforms (non-destructive)

- **Collapse duplicates** — the same song imported from many folders shows as one row +
  `×N` badge. `_dedup_key()` / `_dupe_rank()`, `collapse=` param, default ON. On a large
  (~20k-track) library this collapses to roughly two-thirds unique songs. View-only.
- **Library volumes / offline** — `library_volumes()` (superset) and `offline_summary()`
  (thin filter over it) power the Library Drives panel: per-volume status, counts, share
  bars. `volume=` and `online_only=` search params filter server-side.
- **Genre normalization** — `parse_genre_field()` + `normalize_genre()` (see `DOMAIN.md`).
- **Gaps / to-do** — `_track_gaps()` computes per-field missing metadata; `missing=`
  filter surfaces them.

---

## REST API surface (`app.py`)

| Method & path | Purpose |
|---|---|
| `GET /` | serve the single-page UI |
| `GET/POST /api/config` | read / write `config.json` (paths, source) |
| `POST /api/browse` | server-side file/folder picker for the config screen |
| `POST /api/reload` | re-parse library, bust cache |
| `GET /api/library/stats` | counts, coverage, distributions |
| `GET /api/library/comment_tags` | auto-mined "vibe tags" from Comments |
| `GET /api/library/search` | main paged track query (genre/energy/BPM/key/rating/year/`volume`/`online_only`/`collapse`/`missing`) |
| `GET /api/library/offline` | offline-tracks summary by volume |
| `GET /api/library/volumes` | all volumes the library spans + mount status |
| `POST /api/track/setrating` | write a rating to `deckard_ratings.json` (universal) |
| `POST /api/rate` | write a star rating into the file's ID3 tag (needs `mutagen`) |
| `GET /api/track/by-filename` | fallback single-track lookup |
| `GET /api/key/<key>/neighbours` | harmonic (Camelot) neighbours for a key |
| `GET /api/mix/suggestions` | rank next track: genre → energy → BPM → key, Ease/Keep/Build intent |
| `GET /api/audio` | stream a track for in-app preview (409 for unresolved stubs) |
| `POST /api/import/set` | load an existing set/playlist back in |
| `POST /api/export/nml` | export set as Traktor NML |
| `POST /api/export/rekordbox` | export set as rekordbox XML (safe default) |
| `POST /api/export/rekordbox-direct` | write set straight into rekordbox `master.db` (needs `pyrekordbox`; backs up first) |

---

## Optional dependencies

`flask` is required. `pyrekordbox` (direct DB write) and `mutagen` (ID3 rating writes)
are optional — the app degrades gracefully without them. See `requirements.txt`.

## Known-fragile areas

The **Traktor NML export path** has several deferred edge cases (rekordbox-sourced export
emits an unusable key, `ENTRIES` count vs actual entries, filename-basename collisions,
empty `PRIMARYKEY`, 500 on missing body). They're documented with line refs in
`ROADMAP.md` under "NML export edge cases." The current rekordbox→rekordbox workflow
doesn't touch them, hence deferred — but fix them before promoting NML export as
first-class.
