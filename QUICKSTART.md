# Quick Start — Smart Playlist Creator by Deckard

Get from zero to a set exported into rekordbox in about 5 minutes. It's a small
local web app — it runs on your Mac, reads your existing DJ library, and nothing
leaves your machine.

---

## 1. What you need

- **macOS** (Windows is on the roadmap)
- **Python 3** (`python3 --version` — macOS usually has it)
- **A library source**, either:
  - **Traktor** — your `collection.nml` **+** a Mixed In Key "All Playlists" JSON export, or
  - **rekordbox** — your `master.db`
- **rekordbox** installed if you want to export to it

## 2. Install & run

```bash
git clone <repo-url> smart-playlist-creator
cd smart-playlist-creator
./launch.sh          # auto-installs Flask if needed, opens http://localhost:5001
```

That's it — `launch.sh` handles dependencies and starts the server.

## 3. First run — point it at your library

On first launch you'll get a **config screen**. Use the **Browse…** buttons:

- **Traktor:** pick your `collection.nml` and your Mixed In Key JSON.
- **rekordbox:** switch Source to *rekordbox* and pick your `master.db`.

Save → the library loads. (A big library takes ~10–15s the first time.)

## 4. The core loop

1. **Filter / dig** — use the left panel (Genre, Vibe Tags, Energy, BPM, Key, Rating, Year).
2. **Pick a track** — click it; the right panel shows **Mix Suggestions** (what to play next,
   ranked genre → energy → BPM → key).
3. **Build a set** — click **+** on tracks (or **➕ Add all → Set** for a whole filter). Drag the
   **⋮⋮** handle to reorder; a red **⚠** flags any non-harmonic key jump.
4. **Export** — from the Set Builder:
   - **⬇ XML** — rekordbox XML (the safe, universal import). In rekordbox:
     Preferences ▸ Advanced ▸ Database ▸ *rekordbox xml* → point at the file → drag the
     playlist in from the *rekordbox xml* tree.
   - **→ RB** — write straight into rekordbox (quit rekordbox first; it backs up your library).
   - **NML** — Traktor NML.

## 5. Handy extras

- **🎚 Gig Check** (top bar) — every playlist's readiness: how many tracks are online vs missing.
- **Rate these** (Gaps/To-Do) — fast keyboard loop to rate quality (1–5) and energy (q/w/e).
- **🍎 Apple Music playlist** — turn a filter into an Apple Music playlist (e.g. to analyze in MIK).
- **Camelot ↔ musical** key toggle · **day/night** theme.

## 6. Optional / power-user config

These `config.json` keys are **blank by default** — only set them if they match your setup:

| Key | What it does |
|---|---|
| `dropbox_root` | A local library tree to index for path re-linking (if your library's stored paths are stale) |
| `nas_root` | A NAS/network twin to resolve online-only files to (used with `dropbox_root`) |
| `foundation_volume` | A drive name to feature as **★ Foundation** in the Drives panel |

Leave them empty for a normal install — everything works without them.

## 7. Troubleshooting

- **Port already in use / page won't open:** the app runs on **5001** by default (5000 is
  reserved by macOS AirPlay Receiver). To use another port: `SPC_PORT=8080 ./launch.sh`.
  To free the current one: `lsof -ti tcp:5001 | xargs kill` then `./launch.sh`.
- **First load feels slow:** normal for a large library; it caches after.
- **Direct rekordbox write / rate-to-file greyed out:** those need the optional
  `pyrekordbox` / `mutagen` packages (`pip3 install pyrekordbox mutagen`). The **XML export
  is the safe default** and needs neither.
- **Preview won't play a track:** the file must be on disk (cloud "online-only" placeholders
  won't play until downloaded).

---

📖 **Deep dive:** open [`MANUAL.html`](MANUAL.html). · 🛠 **Contributors:** start with [`CLAUDE.md`](CLAUDE.md).
Questions / ideas: **team@djdeckard.com**
