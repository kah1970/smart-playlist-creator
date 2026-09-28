#!/usr/bin/env python3
"""
archive_nonsongs.py — relocate sample / recording / mix clutter out of the DJ
library, reversibly.

Same classifier the app uses (classify.py), so what gets hidden in Smart
Playlist Creator and what this script moves never drift apart.

WHAT IT MOVES (default buckets: recording, mix, stem):
  recording  your own Traktor session captures (~/Music/Traktor/Recordings/)
  mix        long-form DJ mixes / RIPEcasts / podcasts filed among your tracks
  stem       Traktor stem files
It DOES NOT move `sample` (Traktor Factory Sounds) — those are part of the
Traktor install, not your library. Hide them in the app; leave them on disk.

SAFETY (hard rules honored):
  * Dry-run by DEFAULT. Nothing moves until you pass --apply.
  * Never touches the live collection.nml — Traktor keeps its DB pointers; you
    re-link or remove those entries in Traktor/Lexicon later at your leisure.
  * Never stat()s a sleeping NAS. Files under /Volumes are SKIPPED unless you
    pass --include-nas AND the volume is currently mounted.
  * Only moves real, on-disk, non-empty files (online-only stubs are skipped).
  * Writes a manifest (JSON) of every move so it is fully reversible:
        python3 archive_nonsongs.py --undo <manifest.json>

USAGE
  # See the plan (moves nothing):
  python3 archive_nonsongs.py

  # Only recordings + mixes, custom archive location:
  python3 archive_nonsongs.py --buckets recording,mix --dest ~/Documents/_deckard-archive

  # Actually move:
  python3 archive_nonsongs.py --apply

  # Undo a previous run:
  python3 archive_nonsongs.py --undo ~/Documents/_deckard-archive/manifest-YYYYMMDD-HHMMSS.json
"""

import argparse
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

from classify import classify, is_movable

def _default_nml():
    """Default NML path = config.json's nml_path (the same config the app uses),
    so this script and the app always point at the same library. Empty if unset —
    then --nml is required."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config.json")) as f:
            return json.load(f).get("nml_path", "")
    except (OSError, ValueError):
        return ""


DEFAULT_NML = _default_nml()
DEFAULT_DEST = os.path.expanduser("~/Documents/_deckard-archive")
DEFAULT_BUCKETS = ["recording", "mix", "stem"]


def resolve_local_path(volume, dir_raw, filename):
    """Turn a Traktor LOCATION into a POSIX path. Returns (path, on_nas)."""
    d = (dir_raw or "").replace("/:", "/")
    if volume in ("", "Macintosh HD"):
        return d + filename, False
    return f"/Volumes/{volume}{d}{filename}", True


def mounted_volumes():
    try:
        return set(os.listdir("/Volumes"))
    except OSError:
        return set()


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def plan_moves(nml_path, buckets, dest, include_nas):
    """Walk the NML; return (moves, skipped) for the requested buckets."""
    mounts = mounted_volumes()
    tree = ET.parse(nml_path)
    root = tree.getroot()
    moves, skipped = [], {"not_movable_bucket": 0, "no_file_on_disk": 0,
                          "empty_stub": 0, "nas_skipped": 0, "already_archived": 0}
    dest_abs = os.path.abspath(os.path.expanduser(dest))
    for entry in root.iter("ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        volume = loc.get("VOLUME", "")
        dir_raw = loc.get("DIR", "")
        filename = loc.get("FILE", "")
        info = entry.find("INFO")
        try:
            playtime = int(info.get("PLAYTIME", "0")) if info is not None else 0
        except (TypeError, ValueError):
            playtime = 0

        ctype = classify(dir_raw, filename, playtime)
        if ctype not in buckets or not is_movable(ctype):
            continue

        path, on_nas = resolve_local_path(volume, dir_raw, filename)
        if on_nas:
            vol = volume
            if not include_nas or vol not in mounts:
                skipped["nas_skipped"] += 1
                continue
        if os.path.abspath(path).startswith(dest_abs):
            skipped["already_archived"] += 1
            continue
        if not os.path.isfile(path):
            skipped["no_file_on_disk"] += 1
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            skipped["no_file_on_disk"] += 1
            continue
        if size == 0:
            skipped["empty_stub"] += 1
            continue

        # Archive path: <dest>/<bucket>/<original dir tree>/<file>
        rel = (dir_raw.replace("/:", "/")).lstrip("/")
        target = os.path.join(dest_abs, ctype, rel, filename)
        moves.append({"type": ctype, "src": path, "dst": target, "size": size})
    return moves, skipped


def do_apply(moves, dest):
    dest_abs = os.path.abspath(os.path.expanduser(dest))
    os.makedirs(dest_abs, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest_path = os.path.join(dest_abs, f"manifest-{stamp}.json")
    done = []
    for m in moves:
        os.makedirs(os.path.dirname(m["dst"]), exist_ok=True)
        dst = m["dst"]
        if os.path.exists(dst):  # never clobber
            base, ext = os.path.splitext(dst)
            dst = f"{base}__{stamp}{ext}"
        try:
            shutil.move(m["src"], dst)
            done.append({"type": m["type"], "from": m["src"], "to": dst, "size": m["size"]})
        except Exception as e:  # noqa: BLE001
            print(f"  ! failed: {m['src']} -> {dst}: {e}", file=sys.stderr)
    with open(manifest_path, "w") as f:
        json.dump({"created": stamp, "moves": done}, f, indent=2)
    print(f"\nMoved {len(done)} file(s). Manifest: {manifest_path}")
    print(f"Undo with:  python3 archive_nonsongs.py --undo {manifest_path}")


def do_undo(manifest_path):
    with open(manifest_path) as f:
        data = json.load(f)
    moves = data.get("moves", [])
    restored = 0
    for m in moves:
        src, dst = m["to"], m["from"]  # reverse
        if not os.path.isfile(src):
            print(f"  ! archived file missing, skipping: {src}", file=sys.stderr)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            print(f"  ! original path occupied, skipping: {dst}", file=sys.stderr)
            continue
        shutil.move(src, dst)
        restored += 1
    print(f"Restored {restored}/{len(moves)} file(s) to their original locations.")


def main():
    ap = argparse.ArgumentParser(description="Reversibly archive sample/recording/mix clutter.")
    ap.add_argument("--nml", default=DEFAULT_NML, help="Traktor collection.nml")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="archive root")
    ap.add_argument("--buckets", default=",".join(DEFAULT_BUCKETS),
                    help="comma list of buckets to move (recording,mix,stem; 'sample' is refused)")
    ap.add_argument("--include-nas", action="store_true",
                    help="also move files on a MOUNTED external/NAS volume (default: skip)")
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry-run)")
    ap.add_argument("--undo", metavar="MANIFEST", help="reverse a prior run from its manifest")
    args = ap.parse_args()

    if args.undo:
        do_undo(args.undo)
        return

    if not args.nml:
        ap.error("no NML path — set nml_path in config.json (copy config.example.json) "
                 "or pass --nml /path/to/collection.nml")

    buckets = [b.strip() for b in args.buckets.split(",") if b.strip()]
    if "sample" in buckets:
        print("Refusing to move 'sample' (Traktor Factory Sounds are install content). "
              "Hide them in the app instead.", file=sys.stderr)
        buckets = [b for b in buckets if b != "sample"]

    moves, skipped = plan_moves(args.nml, buckets, args.dest, args.include_nas)
    by_type = {}
    total = 0
    for m in moves:
        by_type.setdefault(m["type"], [0, 0])
        by_type[m["type"]][0] += 1
        by_type[m["type"]][1] += m["size"]
        total += m["size"]

    mode = "APPLY" if args.apply else "DRY-RUN (nothing moves — pass --apply to act)"
    print(f"=== archive_nonsongs — {mode} ===")
    print(f"buckets: {', '.join(buckets)}   dest: {os.path.expanduser(args.dest)}")
    print(f"\nMovable files found: {len(moves)}  ({human(total)})")
    for t, (n, sz) in sorted(by_type.items()):
        print(f"  {n:5d}  {t:10s}  {human(sz)}")
    print("\nSkipped:")
    for k, v in skipped.items():
        if v:
            print(f"  {v:5d}  {k}")
    if moves[:8]:
        print("\nSample of what would move:")
        for m in moves[:8]:
            print(f"  [{m['type']}] {m['src']}")

    if args.apply:
        if not moves:
            print("\nNothing to move.")
            return
        do_apply(moves, args.dest)


if __name__ == "__main__":
    main()
