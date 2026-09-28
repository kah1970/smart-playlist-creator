# Roadmap & Status

Working notes for the Smart Playlist Creator. The [README](README.md) is the
user-facing intro; this file is the dev-facing "where we are / where we're
going." Kept honest on purpose — known-broken edges live here, not hidden.

---

## v1.0 checklist (prioritized)

The goal is a rock-solid **rekordbox/Traktor set-builder + export core** — no AI, no
external APIs (those are post-v1.0, see "Post-v1.0 roadmap" below). Ordered by
release-blocking weight. Full detail in "v1.0 release gate".

**🔴 P0 — release blockers**
- [x] **1. Verify all three export paths live** — machine-verified 2026-08-11 (see "Export verification" below). rekordbox XML ✅, direct rekordbox DB write ✅ (against a throwaway master.db copy), Traktor NML ✅ happy-path. Remaining: the user's one-click GUI-import confirmation (rekordbox imports the XML; Traktor loads the NML).
- [x] **2. Fix deferred NML export edge cases** — DONE 2026-08-11 (branch `nml-export-fixes`): rekordbox-sourced keys now synthesized from paths + a cross-source warning (no more silent empty import); `ENTRIES` counts actual entries; lookup prefers stable `id` (basename collisions); tracks with no usable key are skipped not emitted empty; non-JSON body returns clean JSON. All re-verified live.

**🟠 P1 — should-have polish**
- [x] **3. Set Builder drag-to-reorder** — DONE 2026-08-12 (⋮⋮ handle, HTML5 DnD, drop indicator).
- [x] **4. Harmonic flow warnings in the Set Builder** — DONE 2026-08-12 (Camelot clash detection: red accent + ⚠ tooltip + set-header clash count; updates live on reorder).

**🟡 P2 — nice-to-have (candidate to defer to v1.1)**
- [ ] **5. BPM Phase Builder** → deferred to v1.1.

**All v1.0 CODE gates (#1–#4) are complete.** The only thing left for v1.0 is the user's
one-click **GUI export sign-off** (import a generated rekordbox XML into rekordbox; load a
Traktor NML into Traktor) — then tag v1.0.

### Shipped this session (bonus — beyond the original v1.0 scope)
Path hover tooltip · Apple Music direct-create playlist (AppleScript) · ➕ Add all → Set ·
multi-drive scope + ★ Foundation + scope banner · collapsible filter panel + "In Library /
Shown" counts · **BPM-from-filename** (missing-BPM 1,464 → 653) · **Gig Check** (playlist
online/offline readiness) · **Rate Mode energy (1/2/3)** + shows existing rating/energy.

### New backlog from this session
- Apple Music export: **batch warning + chunked import** (bulk NAS/SMB imports hang Music).
- **Gig Check: rekordbox source** (Traktor-NML only today).
- Filter panel: **peek rows** for Vibe Tags / Release Year (deferred from the UI pass).
- **Gig Check → load a flagged playlist into the Set / export it.**

*Fastest shippable path: #1 GUI sign-off → tag v1.0. #5 + the backlog above are v1.1.*

---

## v1.1 scope (LOCKED 2026-08-12)

Set-builder + gig value only, no new dependencies. ~3–4 sessions. 🟢 small · 🟡 medium.
- [ ] **Gig Check → load a flagged playlist into the Set / export it** 🟢 — make Gig Check actionable.
- [ ] **Filter panel: peek rows** for Vibe Tags / Release Year 🟢 — finish the UI-clarity pass.
- [ ] **In-app genre editing** 🟢 — edit a track's genre from the UI, same overlay pattern as
  Rating Mode / energy: new `deckard_genres.json` + `/api/track/setgenre`, overlaid on load in the
  same loop (app.py ~222). Dropdown populated from the existing canonical set (`GENRE_RULES`, parser.py).
  Writes Deckard state ONLY — never rewrites the live `collection.nml`'s packed `N - Genre` field
  (energy stays in its own overlay, so no prefix clash); propagation to files via Lexicon is a later
  step, like ratings. Best folded into Rating Mode as a combined "classify" pass (rate + energy +
  genre over untagged tracks). ~1hr. (Requested 2026-08-24: "can rate a song but not change genre".)
- [ ] **Rate-to-file for .wav / .flac** 🟡 — `/api/rate` (app.py ~1041) is MP3-only (ID3 POPM).
  Extend the writer: **WAV** — `mutagen.wave.WAVE` carries an ID3 chunk, so the same Traktor-format
  POPM frame (`traktor@native-instruments.de`, rating×51) writes cleanly. **FLAC** — `mutagen.flac.FLAC`
  Vorbis comment `RATING` (0–100); works, but cross-app read-back (Traktor/rekordbox) is less
  standardized than MP3 POPM. NOTE: the **Deckard rating overlay (`deckard_ratings.json`) already
  covers non-MP3 universally** and the NML/rekordbox exports carry it — so file-tag write for
  wav/flac is a convenience for people who want the rating baked into the file, not the only path.
  Dispatch on extension in `api_rate`; keep MP3 behavior unchanged. (Requested 2026-08-24.)
- [ ] **BPM Phase Builder** 🟡 — auto-filter by the documented phase progressions (107→122→128).
- [ ] **Gig Check: rekordbox source** 🟡 — parse rekordbox playlists (pyrekordbox) too.
- [ ] **Export metadata parity (rekordbox XML + direct write)** 🟡 — the rekordbox export paths
  flatten the user's metadata. Found during v1.0 TC-101/102 sign-off (2026-08-20):
  (a) both rekordbox paths write the *normalized* genre `t["genre"]` (`"Funk House"`) instead of
  `t["genre_raw"]` (`"2 - Funk House"`), **dropping the `N -` energy prefix** — trips the "don't
  fix the energy prefix" hard rule; rekordbox has no native energy field so the prefix IS the energy.
  (b) the **direct `→ RB` write** (`api_export_rekordbox_direct` → `_set_meta`, app.py ~1452) never
  sets **Comment** (e.g. `"4A - 122 - Purchased…"`) or **Key** — only Title/BPM/Rating/Artist/Genre.
  Fix: use `t.get("genre_raw") or t.get("genre")` in both `api_export_rekordbox` (XML) and
  `_set_meta` (direct); add comment + key to `_set_meta`. Ratings + BPM already survive. Small.
  NOTE: the **Traktor NML path is already fixed** (v1.0, 2026-08-20) — it now embeds a verbatim
  source `<COLLECTION>` so it preserves genre prefix + comments + ratings + cues + beatgrids in full.

**Deferred beyond v1.1:** MIK 1–10 energy filter · year-backfill review sheet (ffprobe/Discogs) ·
"Chuck" `export.pdb` parser · the AI vision (Tier B) · Beatport · iOS app.

---

## Export verification (2026-08-11)

Machine-verified all three export paths against the live large (~20k-track) library
(5-track online set; endpoints exercised directly, outputs validated).

- **rekordbox XML (`/api/export/rekordbox`) — ✅ PASS.** `DJ_PLAYLISTS` v1.0.0,
  `COLLECTION Entries` matches actual TRACK nodes, playlist refs all resolve (no
  dangling `Key`s), and every `Location` decodes to a real file on disk
  (`missing: 0`). Counts `chosen`, so it doesn't have the NML count bug.
- **Direct rekordbox DB write (`/api/export/rekordbox-direct`) — ✅ PASS.** Tested
  against a *throwaway copy* of `master.db` (never the live library). Backed up the
  DB first, added 5 tracks, committed; reopening the copy shows the new playlist
  with all 5 tracks (8 → 9 playlists). Correctly refuses while rekordbox is open.
- **Traktor NML (`/api/export/nml`) — ✅ happy-path PASS, with caveats.**
  `ENTRIES` matched, no empty KEYs. Two known issues reproduced live: the
  **`ENTRIES` overcount** (3 requested / 2 bogus → declared 3, wrote 1) and a
  **non-JSON body → 400 HTML page** (not a 500). Also note: the exported `KEY`
  carries the source collection's stale `/olduser/` path — consistent with the
  current (pre-reorg) `collection.nml`, so re-import matches today, but fragile if
  the collection is ever re-pointed. All folded into P0 #2.

**Still needs the user:** the GUI round-trip — import the XML into rekordbox and load
the NML into Traktor to confirm they appear and play. That's the last mile of #1.

---

## Where we left off

**Session 2026-08-03 — Library Drives panel (multi-drive awareness):**

- **`app.py` + `Templates/index.html` — Library Drives panel (NEW).** Evolved the
  offline banner into a compact, collapsible "Library Drives" view: every volume
  the library spans, each with a green/amber status dot, track count, share bar,
  and mounted/offline label. Auto-expands + goes amber when anything is offline;
  collapses to a subtle chip when all online. New `GET /api/library/volumes` +
  `library_volumes()` (superset of `offline_summary`, which is now a thin filter
  over it). Front-end `loadDrives()`/`renderDrives()`/`toggleDrives()` replace the
  banner's `checkOffline()`. Verified live: 8 volumes, 8,722 offline across 6
  drives (a main music drive + 5 externals), 2 mounted (the boot disk, the NAS).
  Groundwork for renamed-drive detection + relink. Why it matters: external
  drives get relabeled over the years while the NML still points at the old
  names, so tracks read as offline until the paths are reconciled.
- **Drive filters (NEW).** The Drives panel is now interactive: click a drive row
  to isolate that volume in the track list, or "Hide offline" to show only
  playable tracks. `GET /api/library/search` gained `volume` + `online_only`
  params (server-side, so it works across the paginated list). Verified live:
  filtered counts match the panel exactly (per-volume counts and the online total
  line up with the Drives panel on a large ~20k-track library).

**Session 2026-08-02 — "usable on the real library":**

- **`app.py` + `Templates/index.html` — offline-volumes banner (NEW).** When part
  of the library lives on an unplugged external drive, the app now shows
  *"X tracks offline — mount DRIVE"* (grouped by volume, with a Re-check button)
  instead of silently listing thousands of tracks that then fail one-by-one on
  play/export. New `GET /api/library/offline` endpoint + `offline_summary()` /
  `_volume_root()` helpers; front-end `checkOffline()` runs on load + reload.
  Verified live against a real library: correctly flagged an unplugged music
  drive + small externals while leaving mounted volumes (the NAS, the boot disk)
  alone. Mount state is resolved by name against a single `os.listdir(/Volumes)`
  snapshot rather than `os.path.exists()` per volume — a `stat()` on a hung
  SMB/NFS share (a sleeping NAS) blocks for the full network
  timeout and would otherwise wedge the whole endpoint. Caught live: the check
  hung until switched to the listing approach.
- **Track-list render freeze — verified already resolved.** The old "large-library
  rows make import look hung" report is fixed: the server pages via `limit`/`offset`
  (`api_search`) and the front-end infinite-scrolls 300 rows/page
  (`PAGE = 300`, `loadMore()`), so the DOM never holds the whole library. Live
  check: a 40,990-track library returns 300 rows on first paint.

**Previously shipped** (committed): native Traktor playlist key for NML export
(`nml_key`, `565daf0`); right-panel readability (`7d66ace`); `usb_preflight.py`
+ hardening (`ea7f438`, see below); acquisition-vs-release year filters and
Load-Set import (`e00b2d1`, `f063ac8`); rekordbox as a library *source*
(`eafb938`); initial public release (`0c240b8`).

### `usb_preflight.py` hardening (done this session)

- Staleness / unreadable-folder / no-audio conditions now exit **non-zero (3)**,
  so `usb_preflight.py … && eject` won't sail past a stale stick. Exit map:
  `0` pass · `1` hard fail · `2` bad usage (not mounted) · `3` warnings.
- Audio is scanned across the **whole stick**, not just `CONTENTS/` (audio in
  top-level folders was previously invisible → false "no audio"). `PIONEER/` is
  pruned from the walk so its analysis files aren't counted or descended.
- `os.walk` read errors are surfaced as a warning instead of being silently
  swallowed into a false PASS; `find_ci()` catches `OSError` rather than
  crashing on a permission-denied folder.
- Staleness wording softened + window widened (2→5 min) to absorb normal
  copy-lag false positives.

---

## Fixes / backlog

### NML export edge cases — ✅ RESOLVED 2026-08-11 (branch `nml-export-fixes`)

These came out of a code review, on the **Traktor NML export** path. Fixed as P0 #2
and re-verified live (kept here for the record). Summary of each fix follows; the
one deliberately-scoped-out item is the shared basename-collision root cause in the
*rekordbox* export routes (below).

- **rekordbox-sourced NML export emits an unusable key.** When the loaded source
  is rekordbox, tracks have no `nml_key`, so `app.py:628` falls back to a POSIX
  `full_path` that Traktor can't match — the export looks successful but imports
  empty. Fix: synthesize an `nml_key` equivalent for the rekordbox path, or
  refuse/warn on cross-source NML export.
- **`ENTRIES` count vs actual `<ENTRY>` mismatch** (`app.py:980` vs `:988`).
  **Confirmed live 2026-08-11:** 3 filenames (2 bogus) → declared `ENTRIES="3"`
  but only 1 `<ENTRY>` written. Count is `len(filenames)` but entries are only
  written for tracks found in the library. Strict Traktor builds may reject the
  playlist. Fix: count the entries actually written. (The rekordbox XML route
  already gets this right — it counts `chosen`.)
- **Filename-basename collisions** (`app.py:598`). `track_map` is keyed by bare
  filename, so two tracks named the same on different volumes collide; the
  path-specific `nml_key` makes the export point at the wrong file. Fix: key by
  full path / a composite identity.
- **Empty `PRIMARYKEY` for tracks with no `LOCATION`** (`parser.py:195`).
  `nml_key` and `full_path` both end up `''`, so KEY is written empty. Skip
  tracks without a usable key.
- **`api_export_nml` returns a 400 HTML page on a missing/non-JSON body**
  (`app.py:956`, `data = request.json`). **Verified 2026-08-11:** empty body →
  werkzeug `400 Bad Request` HTML, not a clean JSON error (and not the 500 this
  note previously claimed). The rekordbox route already guards with
  `request.json or {}`; apply the same here for a tidy JSON error.

**Scoped out (deferred):** the same basename-collision root cause (`track_map`
keyed by bare filename) still exists in the **rekordbox XML + direct-write**
routes. Left as-is for now to avoid destabilizing those already-verified paths;
the NML route was fixed via an optional stable-`id` lookup and the frontend now
sends `ids`, so the rekordbox routes could adopt the same pattern later.

### `usb_preflight.py` known limitations

- **Staleness is an mtime heuristic.** A timestamp-preserving copy (`rsync -t`,
  restore from backup) can hide genuinely new, unexported tracks (false
  negative). Inherent to mtimes — a real fix needs content hashing or reading
  the `.pdb` track list (see Phase 2 below).
- **"Intact" = non-zero bytes only.** A truncated/partially-copied file (USB
  yanked mid-write) passes. No header/length/hash validation.

---

## Future features / optimizations

- **Phase 2 parser ("Chuck") — read `export.pdb`.** Verify a *named* playlist is
  actually inside the export, per-playlist ✅/❌. This is the definitive version
  of what `usb_preflight.py` only hints at (staleness), and would also kill the
  mtime-heuristic false negatives above. **This is also the bridge to a standalone
  mobile "Gig Check"** (reads a USB stick natively, no laptop). Build the `.pdb` parser
  + per-playlist online/offline on the Mac first, then port the proven parser to an
  iOS app that reads a USB plugged into the phone (no laptop). A nearer-term half-step:
  parse the NML `<PLAYLISTS>` tree and join our existing per-track online/offline
  status for a library-side Gig Check with no `.pdb` work.
- **Windows support** (already noted in the README; macOS-only today).
- **Shared audio-extension / formatting constants.** `usb_preflight.py`
  re-implements the audio-extension set and a human-readable byte formatter that
  also exist informally in `app.py`; centralize so the web app and preflight
  tool can't drift on "what counts as audio."
- **Prune the walk further / progress on huge sticks** — preflight currently
  stats every audio file; fine today, worth revisiting for very large libraries.

---

## v1.0 release gate

The v1.0 goal is a **stable rekordbox / Traktor set-building + export core** — nothing
in this gate adds a new product surface (no AI, no external APIs). Ship when:

- [ ] **Tier A set-builder polish landed** (see Post-v1.0 below — these are v1.0-adjacent
      finishing work, not new surfaces):
  - [ ] BPM Phase Builder
  - [ ] Set Builder drag-to-reorder
  - [ ] Harmonic flow warnings in the Set Builder
- [ ] **Deferred NML export edge cases fixed** — everything under
      "NML export edge cases" above. NML export can't be promoted to first-class until
      these are closed (unusable rekordbox-sourced key, `ENTRIES` count mismatch,
      filename-basename collisions, empty `PRIMARYKEY`, 500 on missing body).
- [ ] **Export paths verified live** — rekordbox XML, direct rekordbox DB write (with
      backup), and Traktor NML all confirmed importing correctly against the real library.
- [ ] **USB pre-flight green** on a real gig stick (`usb_preflight.py`).

Everything below is explicitly **out of scope for v1.0** — deferred until the export core
is stable and released, so it can't destabilize the thing DJs depend on at a gig.

---

## Post-v1.0 roadmap

Tiered. Origin of most of Tiers B–D is the original "Deckard DJ Agent" vision
(see `DOMAIN.md`) — a different product
surface (AI classification + external APIs) that was intentionally set aside to ship the
set-builder first.

### Tier A — set-builder polish *(v1.0-adjacent; see release gate)*
Finishing work on code we already own, no new dependencies. Listed in the gate above.

### Tier B — library intelligence *(the original vision; never built)*
- **Library Organizer** — the AI classifier: Claude assigns genre + energy prefix,
  outputs a colour-coded confidence-tier review sheet, user approves, a file-mover
  executes (approved rows only — nothing moves without sign-off). The centrepiece of the
  original vision.
- **Discovery Engine** — Forgotten Gems (owned, rated 3–5, unplayed 6+ months), Style
  Match (seed track → similar in library), Gap Analysis.
- **Deckard Score** — composite ranking across energy, quality, recency, vibe-match, key,
  BPM-arc position, play frequency.

### Tier C — set intelligence *(needs gig-history ingestion)*
- Recent Play Dashboard · Overplayed Alert · Set Arc Analysis · Gig Journal ·
  Vignette Tracker.

**Not gig-history-gated** — these build on data SPC already has (Deckard energy 1/2/3,
MIK energy, BPM, genre, track duration), so they could land ahead of the rest of Tier C.
Both treat a set as *a shape over time*, not a flat list. (Requested 2026-08-28.)

- **Set-type / energy-curve-aware Mix Suggestions** 🟡. Today Mix Suggestions rank
  genre → energy → BPM → key off a single seed, context-free — the same seed always
  returns the same "what to play next." Add a **set profile** (e.g. Day-Party House,
  Cocktail-Hour Chill, Warm-Up, Peak-Time Club, After-Hours) that reshapes the ranking:
  a chill cocktail set and a peak-time club set should *not* get the same recommendations
  from the same seed. Model the set as a **target energy curve over position/time**
  (warm-up ramps up, cocktail hour stays low-steady, peak plateaus high, closer descends);
  bias suggestions toward tracks that fit the curve's target at the current set position,
  not just an energy-jump gate off the last track. Ships as presets first (starting curves),
  reshapeable later (drag the curve). This is the actionable, *prescriptive* side of the
  existing "Set Arc Analysis" line above.

- **Time-of-day Set Builder / running clock** 🟢 *(P3 nice-to-have)*. Option to start a
  set at a wall-clock time; the Set Builder then shows the approximate clock time at each
  track from cumulative durations (PLAYTIME is already parsed) — so building a 7–9pm
  cocktail set, the DJ sees roughly when each track lands. Small (start-time input +
  cumulative-duration math, no new deps). Ties into the energy curve: clock + curve together
  let you say "by 8:30 the room should be *here*" and get suggestions that fit the moment.

### Tier D — external / Beatport *(blocked on API access — open since day one)*
- Genre Taxonomy Comparison · Chart Monitoring · Taste Calibration · Similar Track
  Suggestions.
- **Last.fm scrobbling** *(NOT blocked — Last.fm API is free/open)*. Strictly opt-in, OFF by
  default; per-user `lastfm_key` / `lastfm_secret` / `lastfm_session` in `config.json` (blank =
  feature off, exactly like `dropbox_root`/`nas_root`) — preserves the local-first default for
  everyone who doesn't turn it on. No new deps: `urllib` + `hashlib` (md5 request signing) are stdlib.
  One-time auth handshake (getToken → browser authorize → getSession → store session key).
  **Two scrobble modes** (per the user 2026-08-24: fine abiding by the rules, does sometimes audition long
  enough to count): (a) **in-app previews** — fire `track.updateNowPlaying` on play and
  `track.scrobble` once a preview crosses Last.fm's threshold (track >30s, played >50% OR >4min), so
  short auditions don't pollute the profile; (b) **a played Set/session** — batch-scrobble a built set
  with timestamps (could later ingest Traktor history / `gig_event_tracklists.csv` for real gigs).
  Cloud-touching, so it lives here in Tier D, not the offline v1.1 core.

### Tier E — data-quality / infra *(opportunistic; independent of the release)*
- **Year backfill** (~4,050 tracks missing release year) via ffprobe / Discogs / AcoustID,
  as a propose→approve review sheet (never auto-write). Keys already in Keychain.
- **Delete + playlist-history dedup** via Lexicon (the `×N` collapse badges show where).
- **Phase 2 "Chuck" parser** — read `export.pdb` (already detailed under Future features).
- **Windows support**, shared audio-extension/format constants (both above).

---

*Questions / ideas: **team@djdeckard.com***
