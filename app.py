"""
Smart Playlist Creator by Deckard — app.py
Flask backend serving the Deckard UI.

Run:
    python3 app.py
    Open http://localhost:5001   (override the port with SPC_PORT=<n>)
"""

import copy
import json
import os
import re
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from parser import camelot_neighbours, load_library, load_rekordbox, load_playlists

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config — reads from config.json in same directory
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Deckard-side ratings store (Phase 2 "Rating Mode"). We do NOT write to the
# live Traktor NML — ratings persist here (JSON), get overlaid onto the library
# on load, and are propagated to Traktor/rekordbox later (via Lexicon).
# ---------------------------------------------------------------------------
RATINGS_PATH = Path(__file__).parent / "deckard_ratings.json"
_ratings = {}

def _load_ratings():
    global _ratings
    try:
        with open(RATINGS_PATH) as f:
            _ratings = json.load(f)
    except Exception:
        _ratings = {}

def _save_ratings():
    try:
        with open(RATINGS_PATH, "w") as f:
            json.dump(_ratings, f)
    except Exception as e:  # noqa: BLE001
        print("rating save failed:", e)

def _track_id(t):
    """Stable identity for a track across reloads (prefers the Traktor nml_key)."""
    return t.get("nml_key") or t.get("full_path") or t.get("filename") or ""

# Deckard-side energy store (the 1/2/3 prefix), same pattern as ratings — set in
# Rating Mode, overlaid on load. We don't rewrite the NML's "N - Genre" field.
ENERGY_PATH = Path(__file__).parent / "deckard_energy.json"
_energy = {}

def _load_energy():
    global _energy
    try:
        with open(ENERGY_PATH) as f:
            _energy = json.load(f)
    except Exception:
        _energy = {}

def _save_energy():
    try:
        with open(ENERGY_PATH, "w") as f:
            json.dump(_energy, f)
    except Exception as e:  # noqa: BLE001
        print("energy save failed:", e)

_load_ratings()
_load_energy()


# ---------------------------------------------------------------------------
# Library re-linker: the Traktor NML paths are badly stale (old /Users/olduser/
# home, Mixed-In-Key-renamed filenames with a " - <key> - <bpm>" suffix, and
# reorg moves), so most tracks don't resolve on disk -> everything shows offline
# and nothing previews. We index the REAL library by a normalized filename stem
# and rewrite each unresolved track's full_path to the actual file, restoring
# offline detection + Rating Mode preview. (This is the interim, in-Deckard form
# of the Phase-3 path unification.)
# ---------------------------------------------------------------------------
# Discovery walk is the LOCAL Dropbox tree (fast ~0.5s); resolution PREFERS the
# NAS twin (the volume named by config `nas_root`) when mounted, because Dropbox local is mostly
# online-only 0-byte stubs while the NAS holds real files. NAS = primary playback
# source, Dropbox = backup/discovery. A materialized local copy still wins so
# recent Dropbox-only tracks keep playing.
_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".aac", ".ogg", ".wma")
_relink_index = None

def _relink_roots():
    """Optional, per-user re-linker roots from config (both blank = feature off, a
    standard install). `dropbox_root` = a local library tree to index; `nas_root` =
    a network/NAS twin to resolve online-only stubs to."""
    cfg = load_config()
    return (os.path.expanduser(cfg.get("dropbox_root") or ""),
            (cfg.get("nas_root") or "").rstrip("/"))

def _nas_mounted(nas_root=None):
    if nas_root is None:
        nas_root = _relink_roots()[1]
    if not nas_root:
        return False
    try:
        return os.path.basename(nas_root) in os.listdir("/Volumes")
    except OSError:
        return False

def _norm_stem(filename):
    """Normalize a filename to a match key: drop extension, strip a trailing
    ' - <camelot> - <bpm>' (or lone key/bpm) MIK suffix, keep alphanumerics."""
    s = os.path.splitext(filename or "")[0].lower()
    s = re.sub(r"\s*-\s*\d{1,2}[ab]\s*-\s*\d{2,3}(\.\d+)?\s*$", "", s)
    s = re.sub(r"\s*-\s*\d{1,2}[ab]\s*$", "", s)
    s = re.sub(r"\s*-\s*\d{2,3}(\.\d+)?\s*$", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)

def _build_relink_index():
    """stem -> real file path. Optional per-user resolution: walks a configured
    local library tree, maps online-only stubs to their NAS twin (real bytes), and
    lets a materialized local copy win. No-op (empty) unless `dropbox_root` is set."""
    dbx, nas_root = _relink_roots()
    idx = {}          # key -> path
    idx_local = set() # keys whose stored path is a materialized local file
    if not dbx or not os.path.isdir(dbx):
        return idx
    nas = _nas_mounted(nas_root)
    for dp, dn, fn in os.walk(dbx):
        if "/_Archive/" in dp or "/#recycle" in dp or "/_MetadataSources" in dp:
            continue
        for f in fn:
            if f.startswith(".") or os.path.splitext(f)[1].lower() not in _AUDIO_EXTS:
                continue
            key = _norm_stem(f)
            if not key:
                continue
            full = os.path.join(dp, f)
            try:
                materialized = os.path.getsize(full) > 0
            except OSError:
                materialized = False
            if materialized:
                idx[key] = full            # a real local copy always wins
                idx_local.add(key)
            elif key not in idx_local and key not in idx:
                # online-only stub -> resolve to the NAS twin when mounted
                idx[key] = (nas_root + full[len(dbx):]) if (nas and nas_root) else full
    return idx

def _relink_library(tracks):
    """Rewrite full_path for tracks whose NML path no longer resolves. Returns count."""
    global _relink_index
    if _relink_index is None:
        _relink_index = _build_relink_index()
    relinked = 0
    for t in tracks:
        p = t.get("full_path") or ""
        try:
            if p and os.path.exists(p) and os.path.getsize(p) > 0:
                continue   # already resolves to a real (non-stub) file
        except OSError:
            pass
        cand = _relink_index.get(_norm_stem(t.get("filename") or ""))
        if cand:
            t["full_path"] = cand
            t["relinked"] = True
            relinked += 1
    return relinked


# ---------------------------------------------------------------------------
# In-memory library cache
# ---------------------------------------------------------------------------
_library_cache = None
_library_stats = None

def get_library():
    global _library_cache, _library_stats
    if _library_cache is None:
        cfg = load_config()
        source = cfg.get("source", "traktor")

        if source == "rekordbox":
            rb = cfg.get("rb_path", "")
            if rb and not os.path.exists(rb):
                return [], {"error": f"rekordbox database not found: {rb}"}
            try:
                _library_cache, _library_stats = load_rekordbox(rb)
            except Exception as e:  # noqa: BLE001
                return [], {"error": f"Couldn't read rekordbox library: {e}"}
        else:
            nml = cfg.get("nml_path", "")
            mik = cfg.get("mik_path", "")
            if not nml or not mik:
                return [], {}
            if not os.path.exists(nml):
                return [], {"error": f"NML not found: {nml}"}
            if not os.path.exists(mik):
                return [], {"error": f"MIK not found: {mik}"}
            _library_cache, _library_stats = load_library(nml, mik)

        # Overlay Deckard-side ratings + energy onto the freshly-loaded library
        if _library_cache and (_ratings or _energy):
            for t in _library_cache:
                rid = _track_id(t)
                if rid in _ratings:
                    t["stars"] = _ratings[rid]
                    t["rating_source"] = "deckard"
                if rid in _energy:
                    t["energy_prefix"] = _energy[rid]
                    t["energy_source"] = "deckard"
        # Re-link stale NML paths to the real files on disk (fixes offline + preview)
        if _library_cache:
            n = _relink_library(_library_cache)
            if isinstance(_library_stats, dict):
                _library_stats["relinked"] = n
            print(f"re-linked {n} stale track paths to real files")
    return _library_cache, _library_stats

_library_playlists = None

def get_playlists():
    """Parsed NML playlists (cached). Traktor source only for now."""
    global _library_playlists
    if _library_playlists is None:
        cfg = load_config()
        if cfg.get("source", "traktor") == "rekordbox":
            _library_playlists = []
        else:
            nml = cfg.get("nml_path", "")
            _library_playlists = load_playlists(nml) if nml and os.path.exists(nml) else []
    return _library_playlists

def bust_cache():
    global _library_cache, _library_stats, _library_playlists
    _library_cache = None
    _library_stats = None
    _library_playlists = None


def _volume_root(path):
    """Return the mount-root a path lives on.

    macOS mounts external drives at ``/Volumes/<NAME>``; everything else is
    treated as the always-present boot volume ("/"). This lets us tell an
    unmounted-drive miss apart from a genuinely-missing file.
    """
    if path.startswith("/Volumes/"):
        parts = path.split("/", 3)  # ['', 'Volumes', NAME, rest...]
        if len(parts) >= 3 and parts[2]:
            return "/Volumes/" + parts[2]
    return "/"


def _mounted_volume_names():
    """Names currently present under /Volumes, read WITHOUT stat-ing each mount.

    ``os.path.exists('/Volumes/<share>')`` stat()s the mount itself, which can
    block for the full SMB/NFS timeout when a network share (e.g. a sleeping
    NAS) is mounted but unresponsive. Listing /Volumes is a cheap local dirent
    read that never touches the network filesystems, so it can't hang.
    """
    try:
        return set(os.listdir("/Volumes"))
    except OSError:
        return set()


def library_volumes(tracks):
    """Every volume the library spans, with mount state + track counts.

    Powers the Library Drives panel (and, filtered, the offline banner). Mount
    state is resolved by name against a single /Volumes listing (see
    ``_mounted_volume_names``) so a hung network mount can't block the check.
    """
    from collections import defaultdict

    mounted_names = _mounted_volume_names()

    by_vol = defaultdict(int)
    for t in tracks:
        p = t.get("full_path") or ""
        if p:
            by_vol[_volume_root(p)] += 1

    volumes = []
    for vol, count in by_vol.items():
        if vol == "/":
            name, mounted = "Macintosh HD", True   # boot volume, always present
        else:
            name = vol.rsplit("/", 1)[-1]
            mounted = name in mounted_names
        volumes.append({
            "volume": name or vol,
            "path": vol,
            "tracks": count,
            "mounted": mounted,
        })
    # Offline first (they draw the eye), then most tracks first.
    volumes.sort(key=lambda v: (v["mounted"], -v["tracks"]))

    total = sum(v["tracks"] for v in volumes)
    online = sum(v["tracks"] for v in volumes if v["mounted"])
    return {
        "total": total,
        "online": online,
        "offline": total - online,
        "volume_count": len(volumes),
        "mounted_count": sum(1 for v in volumes if v["mounted"]),
        "volumes": volumes,
    }


def offline_summary(tracks):
    """Offline-only slice of ``library_volumes`` (kept for the banner endpoint)."""
    full = library_volumes(tracks)
    offline = [v for v in full["volumes"] if not v["mounted"]]
    return {"offline_total": full["offline"], "volumes": offline}


# ---------------------------------------------------------------------------
# Routes — UI
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    cfg = load_config()
    return render_template("index.html", configured=bool(cfg.get("nml_path")))


# ---------------------------------------------------------------------------
# Routes — Config
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_config_set():
    data = request.json
    cfg = load_config()
    if "nml_path" in data:
        cfg["nml_path"] = data["nml_path"]
    if "mik_path" in data:
        cfg["mik_path"] = data["mik_path"]
    if "rb_path" in data:
        cfg["rb_path"] = data["rb_path"]
    if "source" in data:
        cfg["source"] = data["source"]
    save_config(cfg)
    bust_cache()
    return jsonify({"ok": True})

@app.route("/api/browse", methods=["POST"])
def api_browse():
    """Open a native macOS file picker and return the chosen POSIX path.

    Works because the Flask server runs on the same Mac as the browser, so
    the AppleScript dialog appears on the user's screen. Returns
    {"ok": False, "cancelled": True} if the user dismisses the dialog.
    """
    data = request.json or {}
    prompt = data.get("prompt", "Select a file")
    exts = data.get("extensions")  # e.g. ["nml"] or ["json"]
    start = data.get("start", "")  # existing path to start near

    type_clause = ""
    if exts:
        quoted = ", ".join(f'"{e}"' for e in exts)
        type_clause = f" of type {{{quoted}}}"

    loc_clause = ""
    start_dir = os.path.dirname(start) if start else ""
    if start_dir and os.path.isdir(start_dir):
        loc_clause = f' default location (POSIX file "{start_dir}")'

    script = (
        f'POSIX path of (choose file with prompt "{prompt}"'
        f"{type_clause}{loc_clause})"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})

    if result.returncode != 0:
        # User cancelled (-128) or other AppleScript error
        if "-128" in result.stderr or "User canceled" in result.stderr:
            return jsonify({"ok": False, "cancelled": True})
        return jsonify({"ok": False, "error": result.stderr.strip()})

    return jsonify({"ok": True, "path": result.stdout.strip()})


@app.route("/api/reload", methods=["POST"])
def api_reload():
    bust_cache()
    tracks, stats = get_library()
    return jsonify({"ok": "error" not in stats, "stats": stats, "count": len(tracks)})


# ---------------------------------------------------------------------------
# Routes — Library
# ---------------------------------------------------------------------------
# Metadata gaps: which fields a track is missing. Drives the "gaps / to-do"
# dashboard (Phase 2) so curation is targeted, not blind. genre=="Other" counts
# as a gap (tagged but uncategorized -> still needs a real genre).
# ---------------------------------------------------------------------------
GAP_FIELDS = ["rating", "genre", "year", "key", "bpm", "energy", "artist"]

def _track_gaps(t):
    """Return the set of metadata fields this track is missing."""
    g = set()
    if not (t.get("artist") or "").strip():                 g.add("artist")
    if not t.get("genre") or t.get("genre") == "Other":     g.add("genre")
    if t.get("year") is None:                               g.add("year")
    if not t.get("bpm"):                                    g.add("bpm")
    if not (t.get("camelot_key") or t.get("traktor_key")):  g.add("key")
    if t.get("energy_prefix") is None:                      g.add("energy")
    if not t.get("stars"):                                  g.add("rating")
    return g


# ---------------------------------------------------------------------------
# Duplicate collapse: many songs appear as several Traktor entries (same track
# imported from different folders over the years). Group by normalized
# title+artist and show ONE representative row (the most complete copy).
# Non-destructive — just the view. "(Original Mix)" vs "(Remix)" differ in title
# so real versions stay separate.
# ---------------------------------------------------------------------------
def _dedup_key(t):
    def n(s): return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    return n(t.get("title")) + "|" + n(t.get("artist"))

def _dupe_rank(t):
    """Higher = the better copy to keep as the group's representative."""
    return (
        t.get("stars") or 0,
        1 if t.get("year") else 0,
        1 if (t.get("genre") and t.get("genre") != "Other") else 0,
        1 if (t.get("camelot_key") or t.get("traktor_key")) else 0,
        1 if t.get("bpm") else 0,
        1 if t.get("energy_prefix") else 0,
    )


# ---------------------------------------------------------------------------
@app.route("/api/library/stats")
def api_stats():
    tracks, stats = get_library()
    if not tracks:
        return jsonify({"error": "Library not loaded", "stats": stats})

    # Collect filter options
    genres = sorted(set(t["genre"] for t in tracks if t["genre"]))
    comments_raw = " ".join(t["comment"] for t in tracks if t["comment"])
    comment_words = sorted(set(re.findall(r'\b[a-zA-Z]{3,}\b', comments_raw)))

    # Metadata-gaps tally (for the gaps / to-do dashboard). Only real songs need
    # curating — skip sample/stem/recording/mix clutter so the To Do count reflects
    # tracks you'd actually rate/tag, not factory samples or your own DJ recordings.
    gaps = {f: 0 for f in GAP_FIELDS}
    for t in tracks:
        if t.get("content_type", "song") != "song":
            continue
        for f in _track_gaps(t):
            gaps[f] += 1

    return jsonify({
        "total": len(tracks),
        "stats": stats,
        "genres": genres,
        "comment_words": comment_words[:80],  # top 80 for UI
        "gaps": gaps,
    })

# Generic label / promo / delivery-service noise to keep out of vibe tags.
# Users can add their own terms via config `vibe_tag_exclude`.
_TAG_JUNK = {
    "tracks", "all", "various", "unknown", "traxsource", "mixupload",
    "delivered by inflyte", "inflyte", "loopmasters", "tr", "htm",
    "beatport", "djdownload", "promo",
}

def extract_comment_tags(tracks, limit=28, min_count=8, extra_junk=None):
    """Find the most frequent descriptive tags inside track comments.

    DJs comma-tag comments (e.g. 'Hip-Hop, Male Rap, Party People'). We split
    on common delimiters, drop keys/BPM/URLs/label-noise, and rank by frequency.

    ``min_count`` — a tag must appear on at least this many tracks to surface
    (config `vibe_tag_min_count`; lower it for a small library). ``extra_junk`` —
    additional terms to exclude (config `vibe_tag_exclude`).
    """
    from collections import Counter
    junk = _TAG_JUNK | {j.strip().lower() for j in (extra_junk or []) if str(j).strip()}
    camelot = re.compile(r"^\d{1,2}[ab]$", re.I)
    counts = Counter()
    for t in tracks:
        c = t.get("comment") or ""
        for part in re.split(r"[,;|/]| - ", c):
            p = " ".join(part.split()).strip()
            if not (3 <= len(p) <= 24):
                continue
            low = p.lower()
            if low in junk or camelot.match(p):
                continue
            if "." in p or any(ch.isdigit() for ch in p):
                continue
            if "www" in low or "http" in low:
                continue
            if len(p.split()) > 3:
                continue
            counts[p.title()] += 1
    return [
        {"tag": tag, "count": n}
        for tag, n in counts.most_common(limit)
        if n >= min_count
    ]


@app.route("/api/library/comment_tags")
def api_comment_tags():
    tracks, _ = get_library()
    if not tracks:
        return jsonify([])
    cfg = load_config()
    try:
        min_count = max(1, int(cfg.get("vibe_tag_min_count", 8)))
    except (TypeError, ValueError):
        min_count = 8
    extra_junk = cfg.get("vibe_tag_exclude") or []
    return jsonify(extract_comment_tags(tracks, min_count=min_count, extra_junk=extra_junk))


@app.route("/api/library/search")
def api_search():
    tracks, _ = get_library()
    if not tracks:
        return jsonify([])

    # --- Filter params ---
    genres     = [g.lower() for g in request.args.getlist("genre") if g]
    # Compile vibe-tags as word-boundary patterns so "Male Rap" doesn't match
    # "Female Rap" (substring) — match the tag as a whole token.
    ctag_res   = [re.compile(r"\b" + re.escape(c.lower()) + r"\b")
                  for c in request.args.getlist("ctag") if c]
    # Acquisition-year buckets ("yr" — the PRIMARY year control): each is "lo-hi"
    # (e.g. "2025-2025"); match any (OR). Filters the /YYYY/ folder the track is
    # filed in — NOT its release year.
    year_ranges = []
    for v in request.args.getlist("yr"):
        try:
            lo, hi = v.split("-")
            year_ranges.append((int(lo), int(hi)))
        except (ValueError, TypeError):
            pass
    # Release-year / era buckets ("ryr" — a SEPARATE control): same "lo-hi" form,
    # filters original release year. Independent of, and combinable with, acquisition.
    release_ranges = []
    for v in request.args.getlist("ryr"):
        try:
            lo, hi = v.split("-")
            release_ranges.append((int(lo), int(hi)))
        except (ValueError, TypeError):
            pass
    energy     = request.args.getlist("energy")
    stars_min  = int(request.args.get("stars_min", 0))
    bpm_min    = float(request.args.get("bpm_min", 0))
    bpm_max    = float(request.args.get("bpm_max", 999))
    keyword    = request.args.get("keyword", "").lower()
    key_filter = request.args.get("key", "").upper()
    limit      = int(request.args.get("limit", 200))
    offset     = max(0, int(request.args.get("offset", 0)))

    # Drive filters (from the Library Drives panel): scope to one or MORE volume
    # names, and/or online-only. Snapshot mounted volumes once — cheap + non-blocking.
    volume_f    = set(v for v in request.args.getlist("volume") if v)
    online_only = request.args.get("online_only", "") in ("1", "true", "yes")
    drive_mounted = _mounted_volume_names() if (volume_f or online_only) else None

    # Gaps / to-do filter (from the gaps dashboard): keep tracks missing ANY of
    # the requested fields — rating/genre/year/key/bpm/energy/artist.
    missing_f = set(m.strip().lower() for m in request.args.getlist("missing") if m.strip())
    # Collapse duplicate entries into one representative row (default ON)
    collapse = request.args.get("collapse", "1") not in ("0", "false", "no")

    # Content-type filter (samples / recordings clutter). `hide` is a comma list of
    # content_type buckets to drop: sample,stem,recording,mix. When the param is
    # ABSENT we default to hiding all non-song content (clean set-building list);
    # an explicit empty `hide=` shows everything. See classify.py.
    hide_raw = request.args.get("hide", None)
    if hide_raw is None:
        hide_types = {"sample", "stem", "recording", "mix"}
    else:
        hide_types = {h.strip().lower() for h in hide_raw.split(",") if h.strip()}

    # Multi-column sort: sort=col:dir pairs e.g. sort=bpm:asc&sort=stars:desc
    sort_params = request.args.getlist("sort")
    sort_cols = []
    for sp in sort_params:
        if ':' in sp:
            col, dir_ = sp.split(':', 1)
            sort_cols.append((col.strip(), dir_.strip()))
        else:
            sort_cols.append((sp.strip(), 'asc'))
    if not sort_cols:
        sort_cols = [('artist', 'asc')]

    energy_ints = [int(e) for e in energy if e.isdigit()]

    results = []
    for t in tracks:
        # Content type — hide sample/recording clutter (default: hide all non-song)
        if hide_types and t.get("content_type", "song") in hide_types:
            continue
        # Genre  (multi-select: match if track genre is any selected; None-safe)
        if genres and (t.get("genre") or "").lower() not in genres:
            continue
        # Comment vibe-tags (multi-select OR: comment matches any selected tag)
        if ctag_res:
            cl = (t.get("comment") or "").lower()
            if not any(rx.search(cl) for rx in ctag_res):
                continue
        # Acquisition Year (primary "yr" control) — the /YYYY/ folder the track is filed in.
        # Tracks with no acquisition year drop out only when this filter is active.
        if year_ranges:
            ay = t.get("acquisition_year")
            if ay is None or not any(lo <= ay <= hi for lo, hi in year_ranges):
                continue
        # Release Year / Era (separate "ryr" control) — original release year.
        # Missing release year = "unknown": excluded only when THIS filter is active,
        # never from acquisition-based views.
        if release_ranges:
            ry = t.get("year")
            if ry is None or not any(lo <= ry <= hi for lo, hi in release_ranges):
                continue
        # Energy prefix
        if energy_ints and t.get("energy_prefix") not in energy_ints:
            continue
        # Stars
        if t["stars"] < stars_min:
            continue
        # BPM
        if t["bpm"] and not (bpm_min <= t["bpm"] <= bpm_max):
            continue
        # Key
        if key_filter and (t.get("camelot_key") or "").upper() != key_filter:
            continue
        # Keyword — search title, artist, comment
        if keyword:
            haystack = f"{t.get('title') or ''} {t.get('artist') or ''} {t.get('comment') or ''}".lower()
            if keyword not in haystack:
                continue
        # Drive — filter by volume name and/or online-only (Library Drives panel)
        if volume_f or online_only:
            vroot = _volume_root(t.get("full_path") or "")
            vname = "Macintosh HD" if vroot == "/" else vroot.rsplit("/", 1)[-1]
            if volume_f and vname not in volume_f:
                continue
            if online_only and not ((vroot == "/") or (vname in drive_mounted)):
                continue
        # Gaps / to-do — keep only tracks missing at least one requested field
        if missing_f and not (_track_gaps(t) & missing_f):
            continue

        results.append(t)

    # Collapse duplicate entries (same song imported from multiple folders) into
    # one representative row (the most complete copy), carrying a dupe count.
    dupe_counts = {}
    if collapse:
        groups = {}
        for t in results:
            groups.setdefault(_dedup_key(t), []).append(t)
        reps = []
        for grp in groups.values():
            best = max(grp, key=_dupe_rank)
            reps.append(best)
            dupe_counts[_track_id(best)] = len(grp)
        results = reps

    # Multi-column sort — apply in reverse order so primary sort wins
    def sort_key_for_col(col):
        def key_fn(t):
            if col == 'bpm':        return t['bpm'] or 0
            if col == 'stars':      return t['stars']
            if col == 'energy':     return t.get('energy_prefix') or 0
            if col == 'play_count': return t['play_count']
            if col == 'genre':      return (t.get('genre') or '').lower()
            return t['artist'].lower()  # artist / track default
        return key_fn

    for col, dir_ in reversed(sort_cols):
        results.sort(key=sort_key_for_col(col), reverse=(dir_ == 'desc'))

    # Paginate: total matching count + one page (offset..offset+limit)
    total = len(results)
    out = []
    for t in results[offset:offset + limit]:
        out.append({
            "title":         t["title"],
            "artist":        t["artist"],
            "bpm":           t["bpm"],
            "camelot_key":   t.get("camelot_key", ""),
            "energy_prefix": t.get("energy_prefix"),
            "mik_energy":    t.get("mik_energy"),
            "stars":         t["stars"],
            "genre":         t.get("genre", ""),
            "comment":       t.get("comment", ""),
            "mik_matched":   t.get("mik_matched", False),
            "neighbours":    t.get("neighbours", {}),
            "filename":      t["filename"],
            "full_path":     t.get("full_path", ""),
            "play_count":    t["play_count"],
            "year":            t.get("year"),
            "acquisition_year": t.get("acquisition_year"),
            "id":            _track_id(t),
            "dupe_count":    dupe_counts.get(_track_id(t), 1),
            "content_type":  t.get("content_type", "song"),
        })

    return jsonify({"total": total, "offset": offset, "limit": limit, "tracks": out})


@app.route("/api/track/setrating", methods=["POST"])
def api_set_rating():
    """Set a track's rating in the Deckard-side store (universal — works for
    offline / non-MP3 tracks the ID3 writer /api/rate can't touch). Persisted to
    deckard_ratings.json, overlaid on load, propagated to the files later."""
    data = request.get_json(silent=True) or {}
    rid = (data.get("id") or "").strip()
    if not rid or data.get("stars") is None:
        return jsonify({"error": "id and stars required"}), 400
    stars = max(0, min(5, int(data.get("stars"))))
    if stars:
        _ratings[rid] = stars
    else:
        _ratings.pop(rid, None)
    _save_ratings()
    tracks, _ = get_library()
    for t in tracks:
        if _track_id(t) == rid:
            t["stars"] = stars
            t["rating_source"] = "deckard"
            break
    return jsonify({"ok": True, "id": rid, "stars": stars, "rated_total": len(_ratings)})


@app.route("/api/track/setenergy", methods=["POST"])
def api_set_energy():
    """Set a track's energy prefix (1/2/3) in the Deckard-side store. Same pattern
    as setrating — persisted to deckard_energy.json, overlaid on load. 0 clears."""
    data = request.get_json(silent=True) or {}
    rid = (data.get("id") or "").strip()
    if not rid or data.get("energy") is None:
        return jsonify({"error": "id and energy required"}), 400
    energy = max(0, min(3, int(data.get("energy"))))
    if energy:
        _energy[rid] = energy
    else:
        _energy.pop(rid, None)
    _save_energy()
    tracks, _ = get_library()
    for t in tracks:
        if _track_id(t) == rid:
            t["energy_prefix"] = energy or None
            t["energy_source"] = "deckard"
            break
    return jsonify({"ok": True, "id": rid, "energy": energy, "energy_total": len(_energy)})


@app.route("/api/library/offline")
def api_offline():
    """Report which tracks live on volumes that aren't currently mounted."""
    tracks, _ = get_library()
    if not tracks:
        return jsonify({"offline_total": 0, "volumes": []})
    return jsonify(offline_summary(tracks))


@app.route("/api/library/volumes")
def api_volumes():
    """Every volume the library spans + mount state (powers the Drives panel)."""
    tracks, _ = get_library()
    if not tracks:
        return jsonify({"total": 0, "online": 0, "offline": 0,
                        "volume_count": 0, "mounted_count": 0, "volumes": []})
    return jsonify(library_volumes(tracks))


@app.route("/api/playlists")
def api_playlists():
    """Gig Check: every playlist with how many of its tracks are online (on a
    mounted drive) vs offline/missing. Online is resolved by VOLUME mount state
    (cheap + safe — never stats a per-file path on a sleeping NAS)."""
    tracks, _ = get_library()
    pls = get_playlists()
    by_key = {t.get("nml_key"): t for t in tracks if t.get("nml_key")}
    mounted = _mounted_volume_names()

    def _online(t):
        vroot = _volume_root(t.get("full_path") or "")
        vname = "Macintosh HD" if vroot == "/" else vroot.rsplit("/", 1)[-1]
        return (vroot == "/") or (vname in mounted)

    out = []
    for pl in pls:
        total = len(pl["keys"])
        online = 0
        offline_names = []
        for k in pl["keys"]:
            t = by_key.get(k)
            if t and _online(t):
                online += 1
            else:
                # offline drive, or the track no longer exists in the library
                offline_names.append(os.path.basename((k or "").replace("/:", "/")))
        out.append({
            "name": pl["name"],
            "path": pl["path"],
            "total": total,
            "online": online,
            "offline": total - online,
            "offline_sample": offline_names[:80],
        })
    # Worst readiness first (most offline), then alphabetical.
    out.sort(key=lambda p: (-p["offline"], p["name"].lower()))
    return jsonify({"playlists": out, "count": len(out)})


# ---------------------------------------------------------------------------
# Routes — Harmonic neighbours for a given key
# ---------------------------------------------------------------------------
@app.route("/api/key/<key>/neighbours")
def api_key_neighbours(key):
    tracks, _ = get_library()
    neighbours = camelot_neighbours(key.upper())
    if not neighbours:
        return jsonify({"error": "Invalid Camelot key"})

    result = {}
    for rel, nkey in neighbours.items():
        matching = [
            {
                "title":         t["title"],
                "artist":        t["artist"],
                "bpm":           t["bpm"],
                "camelot_key":   t.get("camelot_key", ""),
                "energy_prefix": t.get("energy_prefix"),
                "stars":         t["stars"],
                "genre":         t.get("genre", ""),
                "comment":       t.get("comment", ""),
                "filename":      t["filename"],
            }
            for t in tracks
            if t.get("camelot_key", "").upper() == nkey.upper()
        ]
        matching.sort(key=lambda x: x["stars"], reverse=True)
        result[rel] = {"key": nkey, "tracks": matching[:30]}

    return jsonify(result)


def _bpm_match(seed_bpm, c_bpm, pct, halftime):
    """Return (ratio, kind) of the closest BPM anchor within pct, else None.
    kind: 'bpm' (direct), 'half' (candidate ~2x seed), 'double' (candidate ~½ seed)."""
    if not seed_bpm or not c_bpm:
        return None
    anchors = [(seed_bpm, "bpm")]
    if halftime:
        anchors += [(seed_bpm * 2, "half"), (seed_bpm / 2, "double")]
    best = None
    for anchor, kind in anchors:
        r = abs(c_bpm - anchor) / anchor
        if r <= pct and (best is None or r < best[0]):
            best = (r, kind)
    return best


@app.route("/api/mix/suggestions")
def api_mix_suggestions():
    """Rank mix-compatible tracks for a seed track using the user's mixing logic:
    genre > energy-tier > BPM proximity > harmonic key, with hard gates on
    BPM window and energy jumps. Also returns the pure-harmonic key groups."""
    filename = request.args.get("filename", "")
    pct      = float(request.args.get("bpm_pct", 6)) / 100.0
    intent   = request.args.get("intent", "keep")   # keep | build | ease
    halftime = request.args.get("halftime", "0") == "1"

    tracks, _ = get_library()
    # A song can appear as several copies under one filename (imported from
    # different folders over the years) and the copies don't always carry the
    # same tags. Pick the most-complete copy — the same representative the main
    # list shows — so the seed's genre/energy/key drive suggestions correctly.
    seed_copies = [t for t in tracks if t["filename"] == filename]
    seed = max(seed_copies, key=_dupe_rank) if seed_copies else None
    if not seed:
        return jsonify({"error": "seed not found"})

    # Candidate pool: collapse duplicates (same song from multiple folders) to one
    # representative — matches the main window so the panel never shows a song twice —
    # and drop sample/recording/mix clutter (never a valid "what to play next").
    seed_key = _dedup_key(seed)
    _pool_groups = {}
    for t in tracks:
        if t.get("content_type", "song") != "song":
            continue
        k = _dedup_key(t)
        if k == seed_key:
            continue
        cur = _pool_groups.get(k)
        if cur is None or _dupe_rank(t) > _dupe_rank(cur):
            _pool_groups[k] = t
    pool = list(_pool_groups.values())

    s_bpm   = seed.get("bpm") or 0
    s_ep    = seed.get("energy_prefix")
    s_genre = (seed.get("genre") or "").lower()
    s_key   = (seed.get("camelot_key") or "").upper()
    s_art   = (seed.get("artist") or "").lower()

    nb = camelot_neighbours(s_key) if s_key else {}
    # Two tiers of harmonic fit (see camelot_neighbours):
    #   near  = relative + ±1 hour → safe blend
    #   boost = ±2 hours same ring → deliberate energy lift/drop (works, but a step)
    near_keys  = {nb.get("energy_up"), nb.get("energy_down"), nb.get("relative")} if nb else set()
    boost_keys = {nb.get("boost_up"), nb.get("boost_down")} if nb else set()

    shift = {"build": 1, "ease": -1}.get(intent, 0)
    target_tier = None
    if s_ep is not None:
        target_tier = min(3, max(1, s_ep + shift))

    def score(c):
        why = []
        s = 0.0
        # Genre (top weight)
        cg = (c.get("genre") or "").lower()
        if s_genre and cg == s_genre:
            s += 40; why.append("✓" + (c.get("genre") or ""))
        # Energy tier (only when both tagged)
        c_ep = c.get("energy_prefix")
        if target_tier is not None and c_ep is not None:
            d = abs(c_ep - target_tier)
            if d == 0:   s += 25; why.append(f"E{c_ep}")
            elif d == 1: s += 12; why.append(f"E{c_ep}")
        # BPM proximity (gate already passed; bm = (ratio, kind))
        bm = c["_bm"]
        ratio, kind = bm
        if kind == "bpm":
            s += 20 * (1 - ratio / pct) if pct else 20
            why.append(f"{'+' if (c.get('bpm') or 0) >= s_bpm else '−'}{abs((c.get('bpm') or 0)-s_bpm):.0f}")
        else:
            s += 12  # half/double-time mix — solid but a tempo jump
            why.append(kind + "-time")
        # Harmonic key (tie-breaker now, not the driver). Tiered:
        # exact > safe move (~) > energy boost (^, +/-2 hours).
        ck = (c.get("camelot_key") or "").upper()
        if s_key and ck == s_key:
            s += 15; why.append(ck)
        elif ck and ck in near_keys:
            s += 9; why.append(ck + "~")
        elif ck and ck in boost_keys:
            # directional marker so the badge reads +2 / -2 (^ up, _ down)
            s += 5; why.append(ck + ("^" if ck == nb.get("boost_up") else "_"))
        # Stars nudge + same-artist penalty
        s += (c.get("stars") or 0)
        if s_art and (c.get("artist") or "").lower() == s_art:
            s -= 15; why.append("same artist")
        return s, why

    def slim(c):
        return {
            "title": c["title"], "artist": c["artist"], "bpm": c["bpm"],
            "camelot_key": c.get("camelot_key", ""), "energy_prefix": c.get("energy_prefix"),
            "stars": c["stars"], "genre": c.get("genre", ""), "filename": c["filename"],
            "id": _track_id(c),   # carry identity so set-adds from the panel export correctly
        }

    # Rank the deduped pool: passes BPM gate + energy-jump gate
    best = []
    for c in pool:
        bm = _bpm_match(s_bpm, c.get("bpm"), pct, halftime)
        if bm is None:
            continue
        if s_ep is not None and c.get("energy_prefix") is not None and abs(c["energy_prefix"] - s_ep) >= 2:
            continue  # never suggest a 2-tier jump (e.g. 1 -> 3)
        c["_bm"] = bm
        sc, why = score(c)
        best.append((sc, c.get("stars") or 0, slim(c) | {"score": round(sc, 1), "why": why}))
    best.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_out = [b[2] for b in best[:14]]

    # Pure-harmonic key groups (collapsed section in the UI).
    # Key is already fixed per group, so rank within it by how well the track
    # otherwise fits the seed: same genre first, then closest BPM, then stars.
    # (Keeps every harmonic match visible — just floats the genre/tempo-right
    # ones to the top instead of sorting purely by rating.)
    def _harmonic_rank(t):
        genre_match = 1 if (s_genre and (t.get("genre") or "").lower() == s_genre) else 0
        c_bpm = t.get("bpm") or 0
        bpm_gap = abs(c_bpm - s_bpm) if (s_bpm and c_bpm) else 9999
        return (-genre_match, bpm_gap, -(t.get("stars") or 0))

    harmonic = {}
    for rel, nkey in nb.items():
        grp = [t for t in pool if (t.get("camelot_key") or "").upper() == nkey.upper()]
        grp.sort(key=_harmonic_rank)
        harmonic[rel] = {"key": nkey, "tracks": [slim(t) for t in grp[:10]]}

    return jsonify({
        "seed": slim(seed) | {"energy_prefix": s_ep},
        "intent": intent, "target_tier": target_tier,
        "best": best_out, "harmonic": harmonic,
    })


@app.route("/api/audio")
def api_audio():
    """Stream a library track to the browser for in-app preview.
    Only serves files that belong to the loaded library (no arbitrary paths);
    conditional=True enables HTTP range requests so the player can seek."""
    filename = request.args.get("filename", "")
    if not filename:
        return jsonify({"error": "no filename"}), 400
    tracks, _ = get_library()
    t = next((x for x in tracks if x["filename"] == filename), None)
    if not t or not t.get("full_path") or not os.path.exists(t["full_path"]):
        return jsonify({"error": "file not on disk"}), 404
    if os.path.getsize(t["full_path"]) == 0:
        return jsonify({"error": "online-only — make available offline in Dropbox to preview"}), 409
    return send_file(t["full_path"], conditional=True)


@app.route("/api/rate", methods=["POST"])
def api_rate():
    """Write a star rating into the track's FILE as an ID3 POPM frame, in
    Traktor's native format (email traktor@native-instruments.de, rating =
    stars*51) so it reads natively in Traktor and in rekordbox once they
    re-read the tag. MP3 only for now."""
    data = request.json or {}
    filename = data.get("filename", "")
    try:
        stars = max(0, min(5, int(data.get("stars", 0))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad stars value"})

    tracks, _ = get_library()
    t = next((x for x in tracks if x["filename"] == filename), None)
    if not t:
        return jsonify({"ok": False, "error": "track not found"})
    p = t.get("full_path")
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": "file not on disk"})
    if os.path.getsize(p) == 0:
        return jsonify({"ok": False, "error": "online-only file — make it available offline first"})
    if os.path.splitext(p)[1].lower() != ".mp3":
        return jsonify({"ok": False,
                        "error": f"writing ratings to {os.path.splitext(p)[1]} isn't supported yet (MP3 only)"})

    try:
        from mutagen.id3 import ID3, POPM, ID3NoHeaderError
        try:
            tags = ID3(p)
        except ID3NoHeaderError:
            tags = ID3()
        if stars > 0:
            tags.setall("POPM", [POPM(email="traktor@native-instruments.de",
                                      rating=stars * 51, count=0)])
        else:
            tags.delall("POPM")
        tags.save(p)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})

    t["stars"] = stars   # reflect immediately in the in-memory library cache
    return jsonify({"ok": True, "stars": stars})


@app.route("/api/track/by-filename")
def api_track_by_filename():
    filename = request.args.get("filename", "")
    if not filename:
        return jsonify({"error": "no filename"})
    tracks, _ = get_library()
    for t in tracks:
        if t["filename"] == filename:
            return jsonify({
                "title":         t["title"],
                "artist":        t["artist"],
                "bpm":           t["bpm"],
                "camelot_key":   t.get("camelot_key", ""),
                "energy_prefix": t.get("energy_prefix"),
                "mik_energy":    t.get("mik_energy"),
                "stars":         t["stars"],
                "genre":         t.get("genre", ""),
                "comment":       t.get("comment", ""),
                "mik_matched":   t.get("mik_matched", False),
                "neighbours":    t.get("neighbours", {}),
                "filename":      t["filename"],
                "play_count":    t["play_count"],
            })
    return jsonify({"error": "not found"})


# ---------------------------------------------------------------------------
# Routes — NML Playlist Export
# ---------------------------------------------------------------------------
def _nml_key_from_path(path):
    """Synthesize a native Traktor PRIMARYKEY (``Volume/:dir/:file``) from a POSIX
    path, for tracks with no native ``nml_key`` (e.g. a rekordbox-sourced library).
    Mirrors the parser's ``volume + dir_raw + filename`` (dir keeps ``/:`` seps).
    Best-effort: an external drive keeps its volume name; anything else is assumed
    to be on the boot volume ("Macintosh HD", matching this library's keys)."""
    if not path:
        return ""
    if path.startswith("/Volumes/"):
        rest = path[len("/Volumes/"):]
        vol, sep, tail = rest.partition("/")
        if not sep or not tail:
            return ""
    else:
        vol = "Macintosh HD"
        tail = path.lstrip("/")
    dirpart, _, fname = tail.rpartition("/")
    if not fname:
        return ""
    dir_raw = ("/:" + "/:".join(dirpart.split("/")) + "/:") if dirpart else "/:"
    return vol + dir_raw + fname


def _split_nml_key(key):
    """Inverse of ``VOLUME + DIR + FILE``: split a native Traktor key back into
    (volume, dir, file) so a LOCATION element can be rebuilt from it."""
    i = key.rfind("/:")
    if i == -1:
        return "", "/:", key
    fname = key[i + 2:]
    head = key[:i + 2]                       # VOLUME + DIR (DIR ends with "/:")
    j = head.find("/:")
    if j == -1:
        return "", head, fname
    return head[:j], head[j:], fname


def _load_source_entries(nml_path, needed_keys):
    """Return {key: ENTRY Element} pulled verbatim from the source Traktor NML for
    the given keys. Lets the NML export embed a real COLLECTION so Traktor can
    resolve the playlist on import — a playlist-only NML (bare PRIMARYKEYs, no
    COLLECTION) imports as an empty playlist. Copying the source entries also
    preserves full fidelity: genre prefix, ratings, comments, cues, beatgrids."""
    found = {}
    if not (nml_path and os.path.exists(nml_path) and needed_keys):
        return found
    needed = set(needed_keys)
    try:
        root = ET.parse(nml_path).getroot()
    except Exception:                        # noqa: BLE001 — bad/locked NML: fall back to synth
        return found
    for e in root.iter("ENTRY"):
        loc = e.find("LOCATION")
        if loc is None:
            continue
        k = (loc.get("VOLUME") or "") + (loc.get("DIR") or "") + (loc.get("FILE") or "")
        if k in needed and k not in found:
            found[k] = e
            if len(found) == len(needed):
                break
    return found


def _synth_nml_entry(t, key):
    """Fallback COLLECTION entry for a track not found in the source NML (e.g. a
    rekordbox-sourced library). Minimal but enough for Traktor to add + match."""
    vol, dir_raw, fname = _split_nml_key(key)
    entry = ET.Element("ENTRY", attrib={
        "TITLE": t.get("title") or fname,
        "ARTIST": t.get("artist") or "",
    })
    ET.SubElement(entry, "LOCATION", attrib={
        "DIR": dir_raw, "FILE": fname, "VOLUME": vol, "VOLUMEID": vol,
    })
    info = {}
    if t.get("genre_raw"):                   # keep the "N - " energy prefix intact
        info["GENRE"] = t["genre_raw"]
    if t.get("comment"):
        info["COMMENT"] = t["comment"]
    if info:
        ET.SubElement(entry, "INFO", attrib=info)
    if t.get("bpm"):
        ET.SubElement(entry, "TEMPO", attrib={
            "BPM": f"{t['bpm']:.6f}", "BPM_QUALITY": "100.000000",
        })
    return entry


@app.route("/api/export/nml", methods=["POST"])
def api_export_nml():
    """
    Accept a set (track filenames, plus optional parallel stable ``ids``),
    generate an NML playlist XML, write it to the Traktor playlists folder.
    Body: { "name": "My Set", "tracks": ["file1.mp3", ...], "ids": ["…", …] }

    ``ids`` (each track's ``_track_id``) is preferred for lookup so two tracks that
    share a basename on different volumes don't collide; ``tracks`` is the legacy
    fallback. ENTRIES reflects what was actually written; tracks without a usable
    key are skipped rather than emitting an empty PRIMARYKEY.
    """
    data = request.get_json(silent=True) or {}      # no 400/500 on empty/non-JSON body
    playlist_name = data.get("name", "Deckard Export")
    filenames = data.get("tracks", [])
    ids = data.get("ids", [])

    tracks, _ = get_library()
    cfg = load_config()
    source = (cfg.get("source") or "traktor").lower()

    by_id = {_track_id(t): t for t in tracks}
    by_name = {}
    for t in tracks:
        by_name.setdefault(t["filename"], t)         # first occurrence wins (deterministic)

    def _resolve(i, fname):
        tid = ids[i] if i < len(ids) else ""
        if tid and tid in by_id:
            return by_id[tid]                        # stable id — avoids basename collisions
        return by_name.get(fname)

    # Resolve the set to (track, key) up front so ENTRIES matches reality and
    # tracks with no usable key are dropped, not written as empty PRIMARYKEYs.
    resolved, missing, skipped_no_key = [], 0, 0        # list of (track, key), in order
    for i, fname in enumerate(filenames):
        t = _resolve(i, fname)
        if not t:
            missing += 1
            continue
        key = t.get("nml_key") or _nml_key_from_path(t.get("full_path") or "")
        if not key:
            skipped_no_key += 1
            continue
        resolved.append((t, key))

    # Build NML XML
    nml = ET.Element("NML", attrib={"VERSION": "19"})
    head = ET.SubElement(nml, "HEAD", attrib={
        "COMPANY": "www.native-instruments.com",
        "PROGRAM": "Traktor",
    })

    # Self-contained COLLECTION: Traktor populates an imported playlist from the
    # file's OWN COLLECTION, not by matching bare PRIMARYKEYs against the loaded
    # library — so a playlist-only NML imports empty. Embed the verbatim source
    # ENTRY per unique track (synthesize one only if it isn't in the source NML).
    unique, seen = [], set()
    for t, key in resolved:
        if key not in seen:
            seen.add(key)
            unique.append((t, key))
    src_entries = _load_source_entries(cfg.get("nml_path", ""), [k for _, k in unique])
    collection = ET.SubElement(nml, "COLLECTION", attrib={"ENTRIES": str(len(unique))})
    for t, key in unique:
        src = src_entries.get(key)
        collection.append(copy.deepcopy(src) if src is not None else _synth_nml_entry(t, key))

    playlist_root = ET.SubElement(nml, "PLAYLISTS")
    node = ET.SubElement(playlist_root, "NODE", attrib={
        "TYPE": "FOLDER",
        "NAME": "$ROOT",
    })
    subnodes = ET.SubElement(node, "SUBNODES", attrib={"COUNT": "1"})
    pl_node = ET.SubElement(subnodes, "NODE", attrib={
        "TYPE": "PLAYLIST",
        "NAME": playlist_name,
    })
    playlist = ET.SubElement(pl_node, "PLAYLIST", attrib={
        "ENTRIES": str(len(resolved)),               # actual entries, not len(filenames)
        "TYPE": "LIST",
        "UUID": "",
    })
    for _t, key in resolved:
        entry = ET.SubElement(playlist, "ENTRY")
        ET.SubElement(entry, "PRIMARYKEY", attrib={"TYPE": "TRACK", "KEY": key})

    # Determine output path
    nml_dir = os.path.dirname(cfg.get("nml_path", ""))
    # Try Traktor playlists folder
    playlists_dir = os.path.join(nml_dir, "Playlists")
    if not os.path.exists(playlists_dir):
        playlists_dir = nml_dir

    safe_name = re.sub(r'[^\w\s-]', '', playlist_name).strip().replace(' ', '_')
    out_path = os.path.join(playlists_dir, f"{safe_name}.nml")

    tree = ET.ElementTree(nml)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)

    resp = {
        "ok": True,
        "path": out_path,
        "entries": len(resolved),
        "requested": len(filenames),
        "missing": missing,
        "skipped_no_key": skipped_no_key,
    }
    if source == "rekordbox":
        resp["warning"] = (
            "Library source is rekordbox — Traktor keys were synthesized from file "
            "paths and may not match a Traktor collection. Prefer the rekordbox XML "
            "or direct export, or verify this playlist imports in Traktor."
        )
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Routes — Load a saved set back INTO the Set Builder (import; the reverse of export)
# ---------------------------------------------------------------------------
@app.route("/api/import/set", methods=["POST"])
def api_import_set():
    """Read a previously-exported playlist back into the Set Builder so you can
    pick up where you left off. Accepts the text of a rekordbox XML
    (DJ_PLAYLISTS) or a Traktor NML, extracts the track filenames in order, and
    matches them to the loaded library.
    Body: { "content": "<xml...>", "filename": "MySet.xml" }
    """
    data = request.json or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Empty file"}), 400

    tracks, _ = get_library()
    if not tracks:
        return jsonify({"error": "Library not loaded"}), 400
    by_name = {}
    for t in tracks:
        fn = t.get("filename") or ""
        by_name.setdefault(fn, t)
        by_name.setdefault(fn.lower(), t)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return jsonify({"error": f"Not valid XML / NML: {e}"}), 400

    ordered_names, fmt = [], None
    if root.tag == "DJ_PLAYLISTS":                     # rekordbox XML
        fmt = "rekordbox"
        loc_by_id = {}
        for tr in root.iter("TRACK"):
            tid, loc = tr.get("TrackID"), tr.get("Location")
            if tid and loc:
                p = urllib.parse.unquote(loc.replace("file://localhost", "", 1))
                loc_by_id[tid] = os.path.basename(p)
        for node in root.iter("NODE"):                 # first playlist node's ordered refs
            if node.get("Type") == "1":
                refs = [c.get("Key") for c in node.findall("TRACK") if c.get("Key")]
                if refs:
                    ordered_names = [loc_by_id.get(k) for k in refs if loc_by_id.get(k)]
                    break
        if not ordered_names:                          # fallback: collection order
            ordered_names = list(loc_by_id.values())
    elif root.tag == "NML":                            # Traktor NML
        fmt = "traktor"
        for entry in root.iter("ENTRY"):
            pk = entry.find("PRIMARYKEY")
            if pk is not None and pk.get("TYPE") == "TRACK" and pk.get("KEY"):
                ordered_names.append(re.split(r"/:|/", pk.get("KEY"))[-1])
    else:
        return jsonify({"error": f"Unrecognized file (root <{root.tag}>). "
                                 f"Expected a rekordbox XML or a Traktor NML."}), 400

    matched, unmatched, seen = [], [], set()
    for name in ordered_names:
        if not name:
            continue
        t = by_name.get(name) or by_name.get(name.lower())
        if t and t["filename"] not in seen:
            seen.add(t["filename"])
            matched.append(t)
        elif not t:
            unmatched.append(name)

    out = [{
        "title":            t["title"],
        "artist":           t["artist"],
        "bpm":              t["bpm"],
        "camelot_key":      t.get("camelot_key", ""),
        "energy_prefix":    t.get("energy_prefix"),
        "mik_energy":       t.get("mik_energy"),
        "stars":            t["stars"],
        "genre":            t.get("genre", ""),
        "comment":          t.get("comment", ""),
        "filename":         t["filename"],
        "play_count":       t["play_count"],
        "year":             t.get("year"),
        "acquisition_year": t.get("acquisition_year"),
    } for t in matched]

    return jsonify({
        "ok": True,
        "format": fmt,
        "tracks": out,
        "matched": len(matched),
        "unmatched": len(unmatched),
        "unmatched_sample": unmatched[:15],
    })


# ---------------------------------------------------------------------------
# Routes — rekordbox XML Playlist Export
# ---------------------------------------------------------------------------
def _file_uri(path):
    """Absolute POSIX path -> rekordbox-style file URI (percent-encoded)."""
    return "file://localhost" + urllib.parse.quote(path)


def _kind_for(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return {
        "mp3": "MP3 File", "wav": "WAV File", "aiff": "AIFF File",
        "aif": "AIFF File", "flac": "FLAC File", "m4a": "M4A File",
        "aac": "AAC File", "ogg": "OGG File",
    }.get(ext, f"{ext.upper()} File" if ext else "Unknown")


@app.route("/api/export/rekordbox", methods=["POST"])
def api_export_rekordbox():
    """
    Build a rekordbox-importable XML (DJ_PLAYLISTS) for the given tracks.
    Rekordbox cannot read Traktor NML; this is the format it imports via
    Preferences -> Advanced -> Database -> rekordbox xml.
    Body: { "name": "My Set", "tracks": ["file1.mp3", ...] }
    """
    data = request.json or {}
    playlist_name = data.get("name", "Deckard Export")
    filenames = data.get("tracks", [])

    tracks, _ = get_library()
    track_map = {t["filename"]: t for t in tracks}

    # Resolve the set in order, skipping any we can't find in the library
    chosen = [track_map[f] for f in filenames if f in track_map]
    missing_files = [
        t["full_path"] for t in chosen
        if t.get("full_path") and not os.path.exists(t["full_path"])
    ]

    root = ET.Element("DJ_PLAYLISTS", attrib={"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", attrib={
        "Name": "rekordbox", "Version": "6.0.0", "Company": "AlphaTheta",
    })

    collection = ET.SubElement(root, "COLLECTION",
                               attrib={"Entries": str(len(chosen))})
    for i, t in enumerate(chosen, start=1):
        path = t.get("full_path") or t["filename"]
        attrib = {
            "TrackID":    str(i),
            "Name":       t.get("title") or "",
            "Artist":     t.get("artist") or "",
            "Genre":      t.get("genre") or "",
            "Kind":       _kind_for(path),
            "Comments":   t.get("comment") or "",
            "Rating":     str((t.get("stars") or 0) * 51),  # rekordbox: 0..255
            "PlayCount":  str(t.get("play_count") or 0),
            "Location":   _file_uri(path),
        }
        if t.get("bpm"):
            attrib["AverageBpm"] = f"{t['bpm']:.2f}"
        if t.get("camelot_key"):
            attrib["Tonality"] = t["camelot_key"]
        ET.SubElement(collection, "TRACK", attrib=attrib)

    playlists = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE", attrib={
        "Type": "0", "Name": "ROOT", "Count": "1",
    })
    pl_node = ET.SubElement(root_node, "NODE", attrib={
        "Name": playlist_name, "Type": "1", "KeyType": "0",
        "Entries": str(len(chosen)),
    })
    for i in range(1, len(chosen) + 1):
        ET.SubElement(pl_node, "TRACK", attrib={"Key": str(i)})

    # Write next to the configured NML, in a Rekordbox subfolder if possible
    cfg = load_config()
    base_dir = os.path.dirname(cfg.get("nml_path", "")) or os.getcwd()
    out_dir = os.path.join(base_dir, "Rekordbox")
    if not os.path.isdir(out_dir):
        out_dir = base_dir
    safe_name = re.sub(r"[^\w\s-]", "", playlist_name).strip().replace(" ", "_")
    out_path = os.path.join(out_dir, f"{safe_name}_rekordbox.xml")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)

    return jsonify({
        "ok": True,
        "path": out_path,
        "count": len(chosen),
        "missing": len(missing_files),
    })


# ---------------------------------------------------------------------------
# Routes — Direct write into the rekordbox database (pyrekordbox)
# ---------------------------------------------------------------------------
@app.route("/api/export/rekordbox-direct", methods=["POST"])
def api_export_rekordbox_direct():
    """Write a playlist straight into the rekordbox master.db via pyrekordbox.
    Safety: refuses while rekordbox is open; backs up master.db before writing;
    reuses tracks already in the collection, only adding genuinely new ones."""
    data = request.json or {}
    playlist_name = data.get("name", "Deckard Export")
    filenames = data.get("tracks", [])

    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.config import get_config
        try:
            from pyrekordbox.utils import get_rekordbox_pid
        except Exception:
            from pyrekordbox.db6.database import get_rekordbox_pid
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"pyrekordbox not available: {e}"})

    # Fail fast (and cleanly) if rekordbox is open — pyrekordbox can't write then
    try:
        if get_rekordbox_pid():
            return jsonify({"ok": False,
                            "error": "Rekordbox is open — quit it completely, then try again."})
    except Exception:
        pass

    tracks, _ = get_library()
    track_map = {t["filename"]: t for t in tracks}
    chosen = [track_map[f] for f in filenames if f in track_map]
    on_disk = [t for t in chosen if t.get("full_path") and os.path.exists(t["full_path"])]
    skipped = len(chosen) - len(on_disk)
    if not on_disk:
        return jsonify({"ok": False,
                        "error": "None of the set's files exist on disk, so none can be added."})

    # Locate master.db: explicit config override first, else pyrekordbox auto-detect
    custom_rb = (load_config() or {}).get("rb_path") or ""
    use_custom = bool(custom_rb and os.path.exists(custom_rb))
    db_path = custom_rb if use_custom else ""
    if not db_path:
        for key in ("rekordbox7", "rekordbox6"):
            try:
                cfg = get_config(key)
                if cfg and cfg.get("db_path"):
                    db_path = cfg["db_path"]
                    break
            except Exception:
                pass
    if not db_path:
        db_path = os.path.expanduser("~/Library/Pioneer/rekordbox/master.db")

    import shutil
    from datetime import datetime
    backup = f"{db_path}.deckard-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy(db_path, backup)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not back up master.db: {e}"})

    def _find_or_create(getter, adder, name):
        try:
            ex = getter(Name=name).first() if getter else None
            if ex:
                return ex
        except Exception:
            pass
        return adder(name)

    def _set_meta(db, content, t):
        # Populate a newly-added track so it isn't blank before rekordbox analyzes it
        if t.get("title"):
            content.Title = t["title"]
        if t.get("bpm"):
            content.BPM = int(round(t["bpm"] * 100))     # rekordbox stores bpm*100
        if t.get("stars"):
            content.Rating = int(t["stars"])             # rekordbox rating is 0-5
        if t.get("artist"):
            a = _find_or_create(getattr(db, "get_artist", None), db.add_artist, t["artist"])
            content.ArtistID = a.ID
        if t.get("genre"):
            g = _find_or_create(getattr(db, "get_genre", None), db.add_genre, t["genre"])
            content.GenreID = g.ID

    try:
        db = Rekordbox6Database(path=custom_rb) if use_custom else Rekordbox6Database()
        pl = db.create_playlist(playlist_name)
        added = 0
        for t in on_disk:
            p = t["full_path"]
            existing = db.get_content(FolderPath=p).first()
            if existing:
                content = existing
            else:
                content = db.add_content(p)
                _set_meta(db, content, t)            # only set metadata on NEW tracks
            db.add_to_playlist(pl, content)
            added += 1
        db.commit()
        return jsonify({"ok": True, "playlist": playlist_name,
                        "added": added, "skipped_missing": skipped, "backup": backup})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "running" in msg.lower():
            msg = "Rekordbox is open — quit it completely, then try again."
        return jsonify({"ok": False, "error": msg, "backup": backup})


# ---------------------------------------------------------------------------
# Routes — Apple Music (.m3u8) playlist export
# ---------------------------------------------------------------------------
@app.route("/api/export/m3u8", methods=["POST"])
def api_export_m3u8():
    """Write an .m3u8 playlist (absolute paths) for a set of tracks — for import
    into Apple Music, which Mixed In Key can then read to analyze BPM/Key. The
    typical use: filter by a Gaps/To-Do field (e.g. missing BPM), export the whole
    filtered list, drop it into Apple Music, analyze in MIK.

    Body: { "name": "...", "ids": ["…"], "tracks": ["file.mp3", …] }
    `ids` (each track's stable `_track_id`) is preferred; `tracks` (filenames) is a
    fallback. Tracks whose file isn't on disk (offline / online-only) are skipped
    and counted, since MIK can only analyze real files.
    """
    data = request.get_json(silent=True) or {}
    playlist_name = data.get("name") or "Deckard Gaps"
    ids = data.get("ids", [])
    filenames = data.get("tracks", [])

    tracks, _ = get_library()
    by_id = {_track_id(t): t for t in tracks}
    by_name = {}
    for t in tracks:
        by_name.setdefault(t["filename"], t)

    chosen, seen = [], set()
    for key in (ids or filenames):
        t = by_id.get(key) if ids else by_name.get(key)
        if t and id(t) not in seen:
            seen.add(id(t))
            chosen.append(t)

    lines = ["#EXTM3U"]
    on_disk = skipped = 0
    for t in chosen:
        p = t.get("full_path") or ""
        try:
            ok = bool(p) and os.path.exists(p) and os.path.getsize(p) > 0
        except OSError:
            ok = False
        if not ok:
            skipped += 1
            continue
        artist = (t.get("artist") or "").strip()
        title = (t.get("title") or t.get("filename") or "").strip()
        label = f"{artist} - {title}" if artist else title
        lines.append(f"#EXTINF:-1,{label}")
        lines.append(p)
        on_disk += 1

    cfg = load_config()
    base_dir = os.path.dirname(cfg.get("nml_path", "")) or os.getcwd()
    safe = re.sub(r"[^\w\s-]", "", playlist_name).strip().replace(" ", "_") or "Deckard_Gaps"
    out_path = os.path.join(base_dir, f"{safe}.m3u8")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    return jsonify({
        "ok": True,
        "path": out_path,
        "on_disk": on_disk,
        "skipped_offline": skipped,
        "requested": len(chosen),
    })


@app.route("/api/export/gig-folder", methods=["POST"])
def api_export_gig_folder():
    """Consolidate a set into ONE portable folder for the laptop: copy each real
    audio file in, plus a RELATIVE-path .m3u8 (bare filenames) that Traktor can
    import. Because paths are relative and the audio sits beside the playlist, the
    whole folder resolves wherever it lands (laptop, USB) — no NAS/Dropbox/cloud
    dependency at gig time, which is exactly what absolute-path exports can't do.

    Requires the source files to be reachable NOW (NAS mounted / files local);
    online-only stubs and offline tracks are skipped and reported, not silently
    dropped. Body: { name, ids[], tracks[], dest? }.
    """
    import shutil
    data = request.get_json(silent=True) or {}
    name = data.get("name") or "Deckard Set"
    ids = data.get("ids", [])
    filenames = data.get("tracks", [])
    dest_root = os.path.expanduser(data.get("dest") or "~/Documents/Deckard Gigs")

    tracks, _ = get_library()
    by_id = {_track_id(t): t for t in tracks}
    by_name = {}
    for t in tracks:
        by_name.setdefault(t["filename"], t)

    # Resolve each set item by id first, filename as fallback — set-adds from the
    # mix panel may lack an id, so a blank-id list must NOT shadow the filenames.
    chosen, seen = [], set()
    n = max(len(ids), len(filenames))
    for i in range(n):
        tid = ids[i] if i < len(ids) else ""
        fn = filenames[i] if i < len(filenames) else ""
        t = (by_id.get(tid) if tid else None) or (by_name.get(fn) if fn else None)
        if t and id(t) not in seen:
            seen.add(id(t))
            chosen.append(t)

    safe = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_") or "Deckard_Set"
    gig_dir = os.path.join(dest_root, safe)
    try:
        os.makedirs(gig_dir, exist_ok=True)
    except OSError as e:
        return jsonify({"ok": False, "error": f"can't create folder: {e}"}), 500

    lines = ["#EXTM3U"]
    copied = skipped = 0
    total_bytes = 0
    skipped_names = []
    used_names = set()
    for t in chosen:
        p = t.get("full_path") or ""
        try:
            ok = bool(p) and os.path.isfile(p) and os.path.getsize(p) > 0
        except OSError:
            ok = False
        if not ok:
            skipped += 1
            skipped_names.append(t.get("filename") or t.get("title") or "?")
            continue
        # Never clobber a same-named-but-different file already copied this run
        base = os.path.basename(p)
        rel = base
        if rel in used_names:
            stem, ext = os.path.splitext(base)
            n = 2
            while f"{stem} ({n}){ext}" in used_names:
                n += 1
            rel = f"{stem} ({n}){ext}"
        used_names.add(rel)
        target = os.path.join(gig_dir, rel)
        try:
            # Skip the copy if an identical-size file is already there (re-runnable)
            if not (os.path.exists(target) and os.path.getsize(target) == os.path.getsize(p)):
                shutil.copy2(p, target)
            total_bytes += os.path.getsize(target)
        except OSError as e:  # noqa: BLE001
            skipped += 1
            skipped_names.append(f"{base} (copy failed: {e})")
            continue
        artist = (t.get("artist") or "").strip()
        title = (t.get("title") or t.get("filename") or "").strip()
        label = f"{artist} - {title}" if artist else title
        lines.append(f"#EXTINF:-1,{label}")
        lines.append(rel)   # RELATIVE — resolves next to this playlist file
        copied += 1

    m3u8_path = os.path.join(gig_dir, f"{safe}.m3u8")
    with open(m3u8_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    return jsonify({
        "ok": True,
        "folder": gig_dir,
        "m3u8": m3u8_path,
        "copied": copied,
        "skipped_offline": skipped,
        "skipped_names": skipped_names[:50],
        "requested": len(chosen),
        "total_mb": round(total_bytes / (1024 * 1024), 1),
    })


@app.route("/api/export/applemusic", methods=["POST"])
def api_export_applemusic():
    """Create a playlist *directly* in Apple Music (Music.app) via AppleScript,
    populated with the given tracks' files. This actually imports the files into
    the Music library — unlike importing an .m3u8, which Music leaves empty when
    the files aren't already in its library. Only works on the local Mac.

    Body: { "name": "...", "ids": ["…"], "tracks": ["file.mp3", …] }
    """
    import tempfile
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Deckard Gaps").strip()
    ids = data.get("ids", [])
    filenames = data.get("tracks", [])

    tracks, _ = get_library()
    by_id = {_track_id(t): t for t in tracks}
    by_name = {}
    for t in tracks:
        by_name.setdefault(t["filename"], t)

    chosen, seen = [], set()
    for key in (ids or filenames):
        t = by_id.get(key) if ids else by_name.get(key)
        if t and id(t) not in seen:
            seen.add(id(t))
            chosen.append(t)

    paths, skipped = [], 0
    for t in chosen:
        p = t.get("full_path") or ""
        try:
            ok = bool(p) and os.path.exists(p) and os.path.getsize(p) > 0
        except OSError:
            ok = False
        if ok:
            paths.append(p)
        else:
            skipped += 1
    if not paths:
        return jsonify({"ok": False, "error": "No on-disk files to add (offline tracks were skipped)."})

    def _as(s):   # escape for an AppleScript string literal
        return s.replace("\\", "\\\\").replace('"', '\\"')

    nm = _as(name)
    # Add in CHUNK-sized batches with a tiny delay between them, so Music stays
    # responsive instead of locking up on one giant add of hundreds of NAS files.
    CHUNK = 25
    add_lines = []
    for i in range(0, len(paths), CHUNK):
        lit = ", ".join(f'POSIX file "{_as(p)}"' for p in paths[i:i + CHUNK])
        add_lines.append(f'    add {{{lit}}} to thePlaylist')
        add_lines.append('    delay 0.15')
    # Rebuild the playlist each run (delete+create) so re-exporting isn't additive.
    # `with timeout` extends AppleScript's default 60s AppleEvent limit so a slow
    # NAS add doesn't fail with -1712 mid-import.
    script = (
        'with timeout of 3600 seconds\n'
        '  tell application "Music"\n'
        f'    try\n        delete user playlist "{nm}"\n    end try\n'
        f'    set thePlaylist to make new user playlist with properties {{name:"{nm}"}}\n'
        + "\n".join(add_lines) + "\n"
        '    delay 0.3\n'
        '    set n to (count of tracks of thePlaylist)\n'
        '  end tell\n'
        'end timeout\n'
        'return n\n'
    )
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as fh:
            fh.write(script)
            spath = fh.name
        res = subprocess.run(["osascript", spath], capture_output=True, text=True, timeout=900)
        try:
            os.unlink(spath)
        except OSError:
            pass
        if res.returncode != 0:
            err = (res.stderr or "AppleScript failed").strip()
            if "-1712" in err or "timed out" in err.lower():
                err = ("Apple Music is busy — likely still importing a previous batch. "
                       "Wait for it to finish, or quit and reopen Music, then try again "
                       "with a smaller filter (~100 tracks).")
            return jsonify({"ok": False, "error": err[:400]})
        return jsonify({
            "ok": True,
            "playlist": name,
            "added": (res.stdout or "").strip(),
            "requested": len(paths),
            "skipped_offline": skipped,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Apple Music took too long — try a smaller filter."})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:400]})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Default 5001, not 5000 — modern macOS reserves 5000 for AirPlay Receiver.
    # Override with SPC_PORT=<n> (e.g. SPC_PORT=8080 ./launch.sh).
    port = int(os.environ.get("SPC_PORT", "5001"))
    # Debug/auto-reloader is OFF by default — the Werkzeug debugger is a remote
    # code-execution surface and shouldn't ship on. Opt in for development with
    # SPC_DEBUG=1 ./launch.sh
    debug = os.environ.get("SPC_DEBUG") == "1"
    print("\n🎛  Smart Playlist Creator by Deckard")
    print(f"   http://localhost:{port}\n")
    app.run(debug=debug, port=port)
