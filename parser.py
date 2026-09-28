"""
Smart Playlist Creator by Deckard — parser.py
Merges Traktor NML + MIK All Playlists JSON into unified track objects.
Cross-references by filename (case-insensitive).

Usage:
    python3 parser.py --nml /path/to/$COLLECTION.nml --mik /path/to/MIK_All_Playlists.json
    python3 parser.py --nml /path/to/$COLLECTION.nml --mik /path/to/MIK_All_Playlists.json --validate
"""

import xml.etree.ElementTree as ET
import json
import argparse
import os
import re
from urllib.parse import unquote

from classify import classify


# ---------------------------------------------------------------------------
# Acquisition year vs. release year
#   acquisition_year — when the track entered THIS library. Derived from the
#                      /YYYY/ folder segment nearest the library root. Near-
#                      complete coverage; matches how the crates are organised.
#   year (release)   — original release year (RELEASE_DATE / ID3 Year). Sparse,
#                      sometimes reissue-inaccurate. Kept as a separate dimension.
# The two are independent and must never be filtered using each other's value.
# ---------------------------------------------------------------------------
_ACQ_YEAR_RE = re.compile(r'(?:^|/)((?:19|20)\d{2})(?:/|$)')

def acquisition_year_from_path(path):
    """Acquisition year (int) from the first /YYYY/ path segment nearest the
    root, or None if the track isn't filed under a year folder."""
    if not path:
        return None
    m = _ACQ_YEAR_RE.findall(path)
    return int(m[0]) if m else None


# ---------------------------------------------------------------------------
# Traktor rating scale: internal 0-255 → stars 0-5
# ---------------------------------------------------------------------------
TRAKTOR_RATING_MAP = {
    0:   0,
    51:  1,
    102: 2,
    153: 3,
    204: 4,
    255: 5,
}

def traktor_rating_to_stars(raw):
    """Convert Traktor internal rating value to 0-5 stars."""
    if not raw:
        return 0
    try:
        val = int(raw)
    except ValueError:
        return 0
    # Find nearest key
    closest = min(TRAKTOR_RATING_MAP.keys(), key=lambda k: abs(k - val))
    return TRAKTOR_RATING_MAP[closest]


# ---------------------------------------------------------------------------
# Parse genre field: "[energy] - [genre]" → (energy_prefix, genre)
# e.g. "2 - House" → (2, "House")
# Falls back gracefully if format doesn't match
# ---------------------------------------------------------------------------
def parse_genre_field(genre_str):
    """Split '[energy] - [genre]' into (energy_prefix, genre_name)."""
    if not genre_str:
        return None, None
    parts = genre_str.split(' - ', 1)
    if len(parts) == 2:
        try:
            energy = int(parts[0].strip())
            return energy, parts[1].strip()
        except ValueError:
            pass
    return None, genre_str.strip()


# ---------------------------------------------------------------------------
# Genre normalization: collapse ~441 raw Traktor genre strings into the user's
# canonical set (~30) so Deckard's Genre filter is clean and diggable.
# Applied AFTER parse_genre_field(), so the "N - " energy prefix is already
# removed and energy_prefix is unaffected. Ordered rules, first substring match
# wins (specific -> general). genre_raw is preserved untouched on each track.
# ---------------------------------------------------------------------------
GENRE_RULES = [
    ('DJ Tools',        ['dj tool', 'acapella', 'accapella', 'a capella', 'a cappella']),
    ('Xmas',            ['xmas', 'christmas', 'holiday']),
    ('Deep House',      ['deep house']),
    ('Tech House',      ['tech house', 'tech-house', 'tech hou']),
    ('Bass House',      ['bass house', 'bassline house']),
    ('Funk House',      ['funk house']),
    ('Jackin House',    ['jackin', 'jacking']),
    ('House',           ['house', 'garage', '2 step', '2-step', 'progressive']),
    ('Nu Funk',         ['nu funk', 'nufunk', 'nu-funk']),
    ('Bass Funk',       ['bass funk']),
    ('Ghetto Funk',     ['ghetto']),
    ('Nu Disco',        ['nu disco', 'nu-disco', 'nudisco', 'indie dance']),
    ('Disco',           ['disco', 're-edit', 're edit', 'reedit', 'boogie']),
    ('Big Beat',        ['big beat', 'bigbeat']),
    ('Breakbeat',       ['breakbeat', 'breaks', 'break beat', 'uk bass', 'nu skool', 'glitch', 'florida break']),
    ('Drum & Bass',     ['drum & bass', 'drum and bass', 'dnb', 'd&b', 'jungle', 'liquid', 'neurofunk']),
    ('Bass',            ['dubstep', 'dub step', 'future bass', 'bassline', 'moombah', 'big room']),
    ('Techno',          ['techno', 'minimal', 'peaktime', 'peak time', 'schranz']),
    ('Trance',          ['trance', 'psy', 'goa']),
    ('Electro',         ['electro', 'electronic', 'electronica']),
    ('Hip-Hop',         ['hip-hop', 'hip hop', 'hiphop', 'rap', 'trap', 'g-funk', 'crunk', 'grime']),
    ('Reggae/Dub',      ['reggae', 'dancehall', 'ska', 'dub']),
    ('80s',             ['80s', "80's", 'eighties', 'new wave', 'synthwave', 'synthpop', 'synth-pop', 'synth pop']),
    ('Pop',             ['pop', 'top 40', 'top40', 'k-pop']),
    ('Rock',            ['rock', 'punk', 'metal', 'grunge', 'alternative']),
    ('Indie',           ['indie']),
    ('Blues/Jazz',      ['blues', 'jazz', 'swing', 'motown', 'rare groove']),
    ('Funk/Soul',       ['funk', 'soul', 'r&b', 'rnb', 'groove', 'boogaloo']),
    ('Downtempo/Chill', ['downtempo', 'chill', 'ambient', 'lounge', 'trip hop', 'trip-hop', 'balearic', 'nu jazz']),
    ('Mash-Up',         ['mash', 'bootleg', 'bootz']),
    ('Bass',            ['bass']),
]


def normalize_genre(genre_name):
    """Map a parsed genre name to the user's canonical set (see GENRE_RULES).
    Returns 'Other' for unrecognised non-empty strings, None for empty."""
    if not genre_name:
        return None
    gl = genre_name.lower().strip()
    for canon, keys in GENRE_RULES:
        if any(k in gl for k in keys):
            return canon
    return 'Other'


# ---------------------------------------------------------------------------
# Camelot wheel neighbours
# ---------------------------------------------------------------------------
CAMELOT_MINOR = ['1A','2A','3A','4A','5A','6A','7A','8A','9A','10A','11A','12A']
CAMELOT_MAJOR = ['1B','2B','3B','4B','5B','6B','7B','8B','9B','10B','11B','12B']

def camelot_neighbours(key):
    """
    Return harmonic neighbours for a Camelot key.

    Two tiers:
      - Safe/perfect moves — same, relative (major<->minor), and +/-1 hour
        (energy_up/energy_down). Blend anything.
      - Energy boost — +/-2 hours on the same ring (boost_up/boost_down). A
        bigger, deliberate lift/drop; works well but is a noticeable step, not a
        lock. Kept separate so callers can weight/label it below the safe moves.

    Returns dict with keys: same, energy_up, energy_down, relative,
    boost_up, boost_down.
    """
    if not key:
        return {}

    key = key.upper().strip()

    if key in CAMELOT_MINOR:
        ring = CAMELOT_MINOR
        rel_ring = CAMELOT_MAJOR
    elif key in CAMELOT_MAJOR:
        ring = CAMELOT_MAJOR
        rel_ring = CAMELOT_MINOR
    else:
        return {}

    idx = ring.index(key)
    return {
        'same':         key,
        'energy_up':    ring[(idx + 1) % 12],
        'energy_down':  ring[(idx - 1) % 12],
        'relative':     rel_ring[idx],
        'boost_up':     ring[(idx + 2) % 12],
        'boost_down':   ring[(idx - 2) % 12],
    }


# ---------------------------------------------------------------------------
# Load MIK JSON → dict keyed by filename (lowercase, no extension)
# ---------------------------------------------------------------------------
def load_mik(mik_path):
    """
    Load MIK All Playlists JSON.
    Returns dict: { 'filename_lower_no_ext': track_dict }
    Also builds a secondary index by full filename for direct lookup.
    """
    with open(mik_path, 'r', encoding='utf-8') as f:
        mik_data = json.load(f)

    by_stem = {}   # key = lowercase filename without extension
    by_file = {}   # key = lowercase full filename with extension

    for track in mik_data:
        url = track.get('url', '').replace('\\/', '/')
        filename = url.split('/')[-1]
        stem = os.path.splitext(filename)[0].lower()
        full = filename.lower()

        entry = {
            'mik_key':    track.get('key', ''),
            'mik_energy': track.get('energy'),
            'mik_bpm':    round(track.get('tempo', 0), 2),
            'mik_url':    url,
            'cue_points': track.get('cuePoints', []),
        }

        by_stem[stem] = entry
        by_file[full] = entry

    return by_stem, by_file


# ---------------------------------------------------------------------------
# Resolve a Traktor LOCATION to a real absolute path
# ---------------------------------------------------------------------------
def _resolve_location(volume, dir_clean, filename):
    """Turn a Traktor LOCATION (VOLUME + DIR + FILE) into an absolute path.

    The boot volume (e.g. "Macintosh HD") is mounted at "/", so its tracks
    live at DIR+FILE. External drives live under /Volumes/<volume>. We prefer
    whichever form actually exists, defaulting to the boot-volume form.
    """
    boot = f"{dir_clean}{filename}"
    if not volume:
        return boot
    ext = f"/Volumes/{volume}{dir_clean}{filename}"
    if os.path.exists(boot):
        return boot
    if os.path.exists(ext):
        return ext
    # Neither found (file moved/deleted) — choose the form based on whether
    # this volume is the startup disk (symlinked at "/") or an external drive.
    mount = f"/Volumes/{volume}"
    if os.path.exists(mount) and os.path.realpath(mount) == "/":
        return boot
    return ext


# ---------------------------------------------------------------------------
# Load Traktor NML → list of track dicts
# ---------------------------------------------------------------------------
def load_traktor_nml(nml_path):
    """
    Parse Traktor NML collection.
    Returns list of raw track dicts with all metadata fields.
    """
    tree = ET.parse(nml_path)
    root = tree.getroot()
    collection = root.find('COLLECTION')

    if collection is None:
        raise ValueError("No COLLECTION element found in NML file.")

    tracks = []
    for entry in collection.findall('ENTRY'):
        title  = entry.get('TITLE', '')
        artist = entry.get('ARTIST', '')

        loc    = entry.find('LOCATION')
        info   = entry.find('INFO')
        tempo  = entry.find('TEMPO')

        # Build file path from LOCATION
        if loc is not None:
            volume   = loc.get('VOLUME', '')
            dir_raw  = loc.get('DIR', '')
            filename = loc.get('FILE', '')
            # Native Traktor playlist key = VOLUME + DIR + FILE (DIR keeps its /: separators).
            # This is what Traktor matches playlist ENTRY/PRIMARYKEY against — NOT the POSIX path.
            nml_key = volume + dir_raw + filename
            # DIR uses /: as separator — normalise for the on-disk POSIX path
            dir_clean = dir_raw.replace('/:', '/')
            full_path = _resolve_location(volume, dir_clean, filename)
        else:
            filename  = ''
            full_path = ''
            nml_key   = ''
            dir_raw   = ''

        # INFO fields
        genre_raw  = info.get('GENRE', '')    if info is not None else ''
        comment    = info.get('COMMENT', '')  if info is not None else ''
        ranking    = info.get('RANKING', '')  if info is not None else ''
        play_count = info.get('PLAYCOUNT', '0') if info is not None else '0'
        key_raw    = info.get('KEY', '')      if info is not None else ''
        release    = info.get('RELEASE_DATE', '') if info is not None else ''
        playtime_raw = info.get('PLAYTIME', '0') if info is not None else '0'
        try:
            playtime = int(playtime_raw)
        except (TypeError, ValueError):
            playtime = 0

        # Release year (RELEASE_DATE is "YYYY/M/D")
        year = None
        if release:
            try:
                year = int(release.split('/')[0])
            except (ValueError, IndexError):
                year = None

        # BPM
        bpm_raw = tempo.get('BPM', '0') if tempo is not None else '0'
        try:
            bpm = round(float(bpm_raw), 2)
        except ValueError:
            bpm = 0.0

        # Parse genre field (strip "N - " energy prefix) then normalize to canonical
        energy_prefix, genre_name = parse_genre_field(genre_raw)
        genre_canon = normalize_genre(genre_name)

        # Ratings
        stars = traktor_rating_to_stars(ranking)

        tracks.append({
            'title':          title,
            'artist':         artist,
            'filename':       filename,
            'full_path':      full_path,
            'nml_key':        nml_key,           # native Traktor playlist key (for NML export)
            'genre_raw':      genre_raw,
            'genre':          genre_canon,       # canonical (normalized) genre for filtering/digging
            'genre_sub':      genre_name,        # parsed sub-genre before normalization (e.g. "Funky House")
            'energy_prefix':  energy_prefix,   # 1, 2, or 3 from your tagging system
            'comment':        comment,
            'stars':          stars,
            'play_count':     int(play_count) if play_count.isdigit() else 0,
            'traktor_key':    key_raw,          # key stored in Traktor (may differ from MIK)
            'bpm':            bpm,
            'year':           year,             # release year from RELEASE_DATE
            'acquisition_year': acquisition_year_from_path(full_path),  # /YYYY/ folder the track is filed in
            'playtime':       playtime,         # track length in seconds (INFO PLAYTIME)
            'content_type':   classify(dir_raw, filename, playtime),  # song / sample / stem / recording / mix
        })

    return tracks


# ---------------------------------------------------------------------------
# Merge: cross-reference Traktor tracks with MIK data by filename
# ---------------------------------------------------------------------------
def merge_libraries(traktor_tracks, mik_by_stem, mik_by_file):
    """
    Merge MIK analysis into Traktor track list.
    Matching strategy:
      1. Exact filename match (case-insensitive)
      2. Stem match (filename without extension, case-insensitive)
    Returns (merged_tracks, match_stats)
    """
    merged   = []
    matched  = 0
    unmatched = 0

    for track in traktor_tracks:
        filename    = track['filename']
        stem        = os.path.splitext(filename)[0].lower()
        file_lower  = filename.lower()

        mik = mik_by_file.get(file_lower) or mik_by_stem.get(stem)

        if mik:
            matched += 1
            # MIK key takes priority — it's been analyzed
            camelot_key = mik['mik_key']
            neighbours  = camelot_neighbours(camelot_key)

            merged.append({
                **track,
                'camelot_key':      camelot_key,
                'mik_energy':       mik['mik_energy'],
                'mik_bpm':          mik['mik_bpm'],
                'cue_points':       mik['cue_points'],
                'neighbours':       neighbours,
                'mik_matched':      True,
            })
        else:
            unmatched += 1
            # Still include track — just without MIK data
            # Try to extract Camelot key from filename (pattern: "- 8A -")
            camelot_key = extract_key_from_filename(filename)
            neighbours  = camelot_neighbours(camelot_key) if camelot_key else {}

            merged.append({
                **track,
                'camelot_key':      camelot_key or track['traktor_key'],
                'mik_energy':       None,
                'mik_bpm':          None,
                'cue_points':       [],
                'neighbours':       neighbours,
                'mik_matched':      False,
            })

    # Fill missing BPM from the filename (MIK convention "... - <key> - <bpm>").
    # Non-destructive: only fills when the NML had no tempo; never overrides it.
    bpm_from_name = 0
    for m in merged:
        if not m.get('bpm'):
            fb = extract_bpm_from_filename(m['filename'])
            if fb:
                m['bpm'] = fb
                m['bpm_from_name'] = True
                bpm_from_name += 1

    stats = {
        'total':     len(traktor_tracks),
        'matched':   matched,
        'unmatched': unmatched,
        'match_pct': round(matched / len(traktor_tracks) * 100, 1) if traktor_tracks else 0,
        'bpm_from_name': bpm_from_name,
    }

    return merged, stats


# ---------------------------------------------------------------------------
# Utility: extract Camelot key from filename
# Filename convention: "Artist - Title - 8A - 128.mp3"
# ---------------------------------------------------------------------------
import re

KEY_PATTERN = re.compile(r'\b(\d{1,2}[AB])\b', re.IGNORECASE)

def extract_key_from_filename(filename):
    """Extract Camelot key from filename if embedded (e.g. '8A', '12B')."""
    matches = KEY_PATTERN.findall(filename)
    for m in matches:
        m_upper = m.upper()
        if m_upper in CAMELOT_MINOR or m_upper in CAMELOT_MAJOR:
            return m_upper
    return None


# Trailing BPM in the MIK filename convention: "Artist - Title - 8A - 128.mp3".
# Allow an optional processing suffix after the BPM (e.g. Platinum Notes "_PN":
# "... - 4A - 137_PN") so those still backfill.
BPM_PATTERN = re.compile(r'-\s*(\d{2,3}(?:\.\d+)?)(?:_[A-Za-z]{1,4})?\s*$')

def extract_bpm_from_filename(filename):
    """Extract a trailing BPM embedded in the filename (MIK convention
    '... - <key> - <bpm>'). Returns a float in a plausible DJ range (50–220),
    else None. Display-only enrichment — mirrors extract_key_from_filename,
    writes nothing to disk."""
    stem = os.path.splitext(filename)[0]
    m = BPM_PATTERN.search(stem)
    if not m:
        return None
    try:
        bpm = float(m.group(1))
    except ValueError:
        return None
    return round(bpm, 2) if 50 <= bpm <= 220 else None


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------
def print_validation_report(merged, stats):
    print("\n" + "=" * 60)
    print("PARSER VALIDATION REPORT")
    print("=" * 60)
    print(f"  Total Traktor tracks:  {stats['total']:,}")
    print(f"  Matched with MIK:      {stats['matched']:,}  ({stats['match_pct']}%)")
    print(f"  No MIK match:          {stats['unmatched']:,}")

    # Genre breakdown
    genres = {}
    for t in merged:
        g = t['genre'] or 'untagged'
        genres[g] = genres.get(g, 0) + 1
    print(f"\n--- GENRE BREAKDOWN ---")
    for g, count in sorted(genres.items(), key=lambda x: -x[1])[:15]:
        print(f"  {g:<35} {count:>5}")

    # Energy prefix breakdown
    energies = {}
    for t in merged:
        e = t['energy_prefix']
        key = f"Energy {e}" if e else "untagged"
        energies[key] = energies.get(key, 0) + 1
    print(f"\n--- ENERGY PREFIX (your 1-3 system) ---")
    for e, count in sorted(energies.items()):
        print(f"  {e:<20} {count:>5}")

    # MIK energy breakdown (1-10 scale)
    mik_energies = {}
    for t in merged:
        e = t['mik_energy']
        key = f"MIK Energy {e}" if e else "no MIK data"
        mik_energies[key] = mik_energies.get(key, 0) + 1
    print(f"\n--- MIK ENERGY (1-10 scale) ---")
    for e, count in sorted(mik_energies.items()):
        print(f"  {e:<20} {count:>5}")

    # Star rating breakdown
    stars = {}
    for t in merged:
        s = t['stars']
        stars[s] = stars.get(s, 0) + 1
    print(f"\n--- STAR RATINGS ---")
    for s, count in sorted(stars.items()):
        bar = '★' * s + '☆' * (5 - s)
        print(f"  {bar}  {count:>5}")

    # Camelot key coverage
    has_key = sum(1 for t in merged if t['camelot_key'])
    print(f"\n--- KEY COVERAGE ---")
    print(f"  Tracks with Camelot key: {has_key:,} / {len(merged):,}")

    # Sample matched tracks
    print(f"\n--- SAMPLE MATCHED TRACKS (5) ---")
    shown = 0
    for t in merged:
        if t['mik_matched'] and t['genre'] and t['stars'] > 0:
            print(f"\n  {t['artist']} - {t['title']}")
            print(f"  BPM: {t['bpm']}  Key: {t['camelot_key']}  "
                  f"MIK Energy: {t['mik_energy']}  Stars: {'★' * t['stars']}")
            print(f"  Genre: {t['genre_raw']}  Comment: {t['comment']}")
            print(f"  Neighbours: {t['neighbours']}")
            shown += 1
        if shown >= 5:
            break

    # Sample unmatched tracks
    print(f"\n--- SAMPLE UNMATCHED TRACKS (3) ---")
    shown = 0
    for t in merged:
        if not t['mik_matched']:
            print(f"\n  {t['artist']} - {t['title']}")
            print(f"  BPM: {t['bpm']}  Filename: {t['filename']}")
            shown += 1
        if shown >= 3:
            break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_playlists(nml_path):
    """Parse the NML <PLAYLISTS> tree into a flat list of playlists for the Gig
    Check. Each = {'name', 'path' (folder breadcrumb), 'keys': [nml_key, ...]}.
    Keys are the native Traktor PRIMARYKEYs, which match tracks' nml_key exactly
    (same NML source). Empty playlists are skipped."""
    try:
        root = ET.parse(nml_path).getroot()
    except Exception:
        return []
    pl_root = root.find('PLAYLISTS')
    if pl_root is None:
        return []
    playlists = []

    def walk(subnodes, crumb):
        for node in subnodes.findall('NODE'):
            ntype, name = node.get('TYPE'), node.get('NAME', '')
            if ntype == 'FOLDER':
                sub = node.find('SUBNODES')
                if sub is not None:
                    walk(sub, crumb + ([name] if name != '$ROOT' else []))
            elif ntype == 'PLAYLIST':
                pl = node.find('PLAYLIST')
                keys = ([pk.get('KEY') for pk in pl.iter('PRIMARYKEY')
                         if pk.get('TYPE') == 'TRACK' and pk.get('KEY')]
                        if pl is not None else [])
                if keys:
                    playlists.append({'name': name, 'path': ' / '.join(crumb), 'keys': keys})

    walk(pl_root, [])   # <PLAYLISTS>'s direct child is the $ROOT folder
    return playlists


def load_library(nml_path, mik_path):
    """
    Main entry point for the Flask app.
    Returns list of merged track dicts.
    """
    print(f"Loading Traktor NML: {nml_path}")
    traktor_tracks = load_traktor_nml(nml_path)
    print(f"  → {len(traktor_tracks):,} tracks loaded")

    print(f"Loading MIK JSON:    {mik_path}")
    mik_by_stem, mik_by_file = load_mik(mik_path)
    print(f"  → {len(mik_by_file):,} MIK tracks loaded")

    print("Merging libraries...")
    merged, stats = merge_libraries(traktor_tracks, mik_by_stem, mik_by_file)
    print(f"  → {stats['matched']:,} matched ({stats['match_pct']}%)")

    return merged, stats


# ---------------------------------------------------------------------------
# Load a rekordbox library (master.db) → same unified track dicts
# ---------------------------------------------------------------------------
_MUSICAL_TO_CAMELOT = {
    # minor → A
    'Abm':'1A','G#m':'1A','Ebm':'2A','D#m':'2A','Bbm':'3A','A#m':'3A','Fm':'4A',
    'Cm':'5A','Gm':'6A','Dm':'7A','Am':'8A','Em':'9A','Bm':'10A','F#m':'11A',
    'Gbm':'11A','Dbm':'12A','C#m':'12A',
    # major → B
    'B':'1B','F#':'2B','Gb':'2B','Db':'3B','C#':'3B','Ab':'4B','G#':'4B','Eb':'5B',
    'D#':'5B','Bb':'6B','A#':'6B','F':'7B','C':'8B','G':'9B','D':'10B','A':'11B','E':'12B',
}

def rb_key_to_camelot(scale):
    """Map a rekordbox key (Open Key like '5m'/'12d', musical like 'Am'/'C',
    or already-Camelot '8A') to Camelot notation."""
    if not scale:
        return ''
    s = scale.strip()
    m = re.match(r'^(\d{1,2})([ABab])$', s)          # already Camelot
    if m:
        return m.group(1) + m.group(2).upper()
    m = re.match(r'^(\d{1,2})([mdMD])$', s)           # Open Key: Nm=minor, Nd=major
    if m:
        n = int(m.group(1))
        letter = 'A' if m.group(2).lower() == 'm' else 'B'
        return f"{((n + 6) % 12) + 1}{letter}"        # 1m→8A, 2m→9A, 12d→7B
    return _MUSICAL_TO_CAMELOT.get(s) or _MUSICAL_TO_CAMELOT.get(s.capitalize(), '')


def load_rekordbox(db_path=''):
    """Read a rekordbox master.db into the same track dicts the app uses.
    Genre keeps the user's 'N - Genre' energy prefix; comments keep vibe tags.
    Returns (tracks, stats). Read-only — safe while rekordbox is open."""
    from pyrekordbox import Rekordbox6Database
    db = Rekordbox6Database(path=db_path) if db_path else Rekordbox6Database()

    tracks = []
    for c in db.get_content():
        path      = c.FolderPath or ''
        genre_raw = c.Genre.Name if c.Genre else ''
        energy_prefix, genre_name = parse_genre_field(genre_raw)
        camelot   = rb_key_to_camelot(c.Key.ScaleName if c.Key else '')
        try:
            bpm = round((c.BPM or 0) / 100.0, 2)
        except (TypeError, ValueError):
            bpm = 0.0
        try:
            year = int(c.ReleaseYear) if getattr(c, 'ReleaseYear', None) else None
        except (TypeError, ValueError):
            year = None

        artist = (c.Artist.Name if c.Artist else '') or ''
        # Skip rekordbox factory sampler/noise rows (no real metadata at all)
        if not bpm and not camelot and not genre_name and not artist:
            continue

        tracks.append({
            'title':         c.Title or '',
            'artist':        artist,
            'filename':      os.path.basename(path),
            'full_path':     path,
            'genre_raw':     genre_raw,
            'genre':         genre_name,
            'energy_prefix': energy_prefix,
            'comment':       getattr(c, 'Commnt', None) or '',
            'stars':         int(c.Rating or 0),
            'play_count':    int(getattr(c, 'DJPlayCount', 0) or 0),
            'traktor_key':   '',
            'bpm':           bpm,
            'year':          year,
            'acquisition_year': acquisition_year_from_path(path),  # /YYYY/ folder the track is filed in
            'camelot_key':   camelot,
            'mik_energy':    None,
            'mik_bpm':       None,
            'cue_points':    [],
            'neighbours':    camelot_neighbours(camelot) if camelot else {},
            'mik_matched':   False,
        })

    keyed = sum(1 for t in tracks if t['camelot_key'])
    stats = {
        'source': 'rekordbox',
        'match_pct': None,
        'key_pct': round(100 * keyed / len(tracks)) if tracks else 0,
        'total': len(tracks),
    }
    return tracks, stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DJ Library Parser')
    parser.add_argument('--nml', required=True, help='Path to Traktor $COLLECTION.nml')
    parser.add_argument('--mik', required=True, help='Path to MIK All Playlists JSON')
    parser.add_argument('--validate', action='store_true', help='Print validation report')
    args = parser.parse_args()

    merged, stats = load_library(args.nml, args.mik)

    if args.validate:
        print_validation_report(merged, stats)
    else:
        print(f"\nDone. {len(merged):,} tracks ready.")
        print(f"Match rate: {stats['match_pct']}%")
