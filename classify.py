"""
Content-type classifier — one source of truth for "is this a song, or is it
sample/recording clutter?"

Used by both parser.py (to tag tracks so the UI can hide them) and the archive
move script (so what gets hidden and what gets moved never drift apart).

Buckets (returned by ``classify``):
  'sample'    Traktor built-in Factory Sounds (loops/one-shots/FX). App content —
              HIDE in the list, but do NOT move (they live in the Traktor install).
  'stem'      Traktor stem files (stem decks). In your library trees — movable.
  'recording' Your own Traktor session captures (~/Music/Traktor/Recordings/).
  'mix'       Long-form (>= MIX_MIN_SEC) files elsewhere — RIPEcasts, other DJs'
              mixes/podcasts filed among your tracks.
  'song'      Everything else — a real, playable track. (Default.)

Coarse grouping for the two UI toggles:
  'samples'     = sample + stem
  'recordings'  = recording + mix
  (song -> None)
"""

import re

# A file at or above this length that isn't a factory sample is treated as a
# DJ mix / recording rather than a track. 18 minutes: the longest normal club
# edits top out well under this; sets/podcasts start well above it.
MIX_MIN_SEC = 18 * 60  # 1080

# content_type -> coarse group used by the UI toggles
GROUP = {
    "sample":    "samples",
    "stem":      "samples",
    "recording": "recordings",
    "mix":       "recordings",
    "song":      None,
}

# Whether a bucket's files are safe to relocate with the archive script.
# Factory Sounds are part of the Traktor install, not your library — hide them,
# never move them.
MOVABLE = {
    "sample":    False,
    "stem":      True,
    "recording": True,
    "mix":       True,
    "song":      False,
}

_RECORDINGS_RE = re.compile(r"/traktor/recordings/")


def classify(dir_raw, filename, playtime=0):
    """Classify one Traktor LOCATION into a content bucket.

    dir_raw   -- the raw Traktor DIR (may use '/:' separators) or a POSIX dir.
    filename  -- the FILE.
    playtime  -- track length in seconds (INFO PLAYTIME), 0 if unknown.
    """
    dpath = (dir_raw or "").replace("/:", "/").lower()
    fname = (filename or "").lower()
    blob = dpath + "/" + fname

    if "factory sounds" in blob:
        return "sample"
    if "traktor stems" in blob or ".stem." in fname:
        return "stem"
    if _RECORDINGS_RE.search(dpath):
        return "recording"
    try:
        pt = int(playtime or 0)
    except (TypeError, ValueError):
        pt = 0
    if pt >= MIX_MIN_SEC:
        return "mix"
    return "song"


def group_of(content_type):
    """Coarse UI group for a content_type ('samples' | 'recordings' | None)."""
    return GROUP.get(content_type)


def is_movable(content_type):
    return MOVABLE.get(content_type, False)
