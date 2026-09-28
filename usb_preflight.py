#!/usr/bin/env python3
"""
usb_preflight.py  —  rekordbox USB "Export Sanity Check"  (Phase 1, zero dependencies)

Catches the #1 failure: a playlist that looks fine in the rekordbox UI but was never
"Export to Device"-d, so a CDJ can't read it. This script does NOT parse export.pdb
(that's Phase 2 / Chuck). It checks the one thing rekordbox refuses to show you visually:
does this stick actually carry a real, fresh export, and is its audio intact?

  python3 usb_preflight.py --usb /Volumes/YOUR_STICK_NAME

Exit codes (safe to chain, e.g. `usb_preflight.py --usb /Volumes/X && eject`):
  0  clean PASS
  1  hard FAIL (no export / zero-byte audio)
  2  bad usage (stick not mounted / not found)
  3  warnings — staleness, unreadable folders, or no audio (NOT conclusive)

What it can tell you DEFINITIVELY (no parser needed):
  * No /PIONEER/rekordbox/export.pdb  -> nothing on this stick will load on a CDJ. Hard fail.
  * export.pdb present + CONTENTS audio present + USBANLZ present -> looks like a real export.

What it can only HINT at (needs Phase 2 to be definitive):
  * Whether a SPECIFIC playlist is inside export.pdb. This tool reports staleness
    (export.pdb older than your newest audio file => you added tracks after the last
    export, so those additions aren't on the CDJ) but cannot name playlists. That's Chuck.
"""

import argparse
import os
import sys
import time

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".aiff", ".aif", ".flac", ".ogg", ".alac"}


def find_ci(parent, name):
    """Case-insensitive child lookup (USB filesystems vary on case)."""
    if not parent or not os.path.isdir(parent):
        return None
    target = name.lower()
    try:
        entries = os.listdir(parent)
    except OSError:
        # Unreadable directory (permissions / flaky USB) — treat as "not found"
        # rather than crashing the whole preflight with a traceback.
        return None
    for entry in entries:
        if entry.lower() == target:
            return os.path.join(parent, entry)
    return None


def fmt_age(mtime):
    secs = max(0, time.time() - mtime)
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{secs / 3600:.1f} hours ago"
    return f"{secs / 86400:.1f} days ago"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    ap = argparse.ArgumentParser(description="rekordbox USB Export Sanity Check")
    ap.add_argument("--usb", required=True, help="Path to the mounted USB (e.g. /Volumes/GIGSTICK)")
    args = ap.parse_args()

    root = args.usb
    if not os.path.isdir(root):
        print(f"  ✗  '{root}' is not a mounted folder. Is the stick plugged in and named right?")
        print(f"     Tip: on macOS run  ls /Volumes  to see the exact name.")
        sys.exit(2)

    print("=" * 64)
    print(f"  EXPORT SANITY CHECK  ·  {root}")
    print("=" * 64)

    problems = []   # hard fails
    warnings = []   # soft / staleness
    pdb_mtime = None  # set once if export.pdb exists; reused for the staleness check

    # ---- 1. The PIONEER export tree -----------------------------------------
    pioneer = find_ci(root, "PIONEER")
    rb_dir = find_ci(pioneer, "rekordbox") if pioneer else None
    export_pdb = find_ci(rb_dir, "export.pdb") if rb_dir else None
    export_ext = find_ci(rb_dir, "exportExt.pdb") if rb_dir else None
    usbanlz = find_ci(pioneer, "USBANLZ") if pioneer else None

    print("\n--- rekordbox export database (what the CDJ reads) ---")
    if not pioneer:
        print("  ✗  No PIONEER folder on this stick.")
        problems.append("No PIONEER folder — this stick has no rekordbox export at all. "
                        "Nothing will load on a CDJ.")
    elif not export_pdb:
        print(f"  ✓  PIONEER/        {pioneer}")
        print("  ✗  rekordbox/export.pdb  MISSING")
        problems.append("export.pdb missing — the stick was never properly Export-to-Device'd. "
                        "A CDJ will show no playlists.")
    else:
        st = os.stat(export_pdb)
        pdb_mtime = st.st_mtime
        print(f"  ✓  export.pdb      {human(st.st_size):>8}   last written {fmt_age(st.st_mtime)}")
        if export_ext:
            print(f"  ✓  exportExt.pdb   {human(os.path.getsize(export_ext)):>8}   (RB6/7 extended)")
        else:
            warnings.append("exportExt.pdb missing — fine for older CDJs, but RB6/7 normally writes it.")
        if usbanlz:
            print(f"  ✓  USBANLZ/        present   (beat grids / waveforms exported)")
        else:
            warnings.append("USBANLZ folder missing — tracks may lack beat grids on the CDJ.")

    # ---- 2. Audio anywhere on the stick + integrity -------------------------
    # Walk the whole stick (audio can live in CONTENTS or in top-level folders),
    # but prune the PIONEER export tree so its thousands of analysis files are
    # neither counted as music nor needlessly descended into.
    print("\n--- audio on the stick ---")
    newest_audio_mtime = 0
    audio_count = 0
    zero_byte = []
    walk_errors = []
    for dirpath, dirs, files in os.walk(root, onerror=walk_errors.append):
        if pioneer:
            dirs[:] = [d for d in dirs if os.path.join(dirpath, d) != pioneer]
        for f in files:
            if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                audio_count += 1
                fp = os.path.join(dirpath, f)
                try:
                    s = os.stat(fp)
                    newest_audio_mtime = max(newest_audio_mtime, s.st_mtime)
                    if s.st_size == 0:
                        zero_byte.append(fp)
                except OSError:
                    pass
    print(f"  {audio_count} audio files found on the stick (excluding PIONEER/)")
    if walk_errors:
        # Don't claim a clean count when part of the tree couldn't be read.
        print(f"  ⚠  {len(walk_errors)} folder(s) could not be read while scanning:")
        for err in walk_errors[:5]:
            print(f"       {getattr(err, 'filename', err)}")
        warnings.append(
            f"{len(walk_errors)} folder(s) were unreadable during the scan — the audio "
            "count and 'newest file' check are incomplete, so PASS is not conclusive.")
    if zero_byte:
        print(f"  ✗  {len(zero_byte)} zero-byte file(s) — will fail to load:")
        for z in zero_byte[:5]:
            print(f"       {os.path.relpath(z, root)}")
        problems.append(f"{len(zero_byte)} zero-byte audio file(s) on the stick.")
    if audio_count == 0:
        warnings.append("No audio files found — nothing to play even if playlists exist.")

    # ---- 3. Staleness heuristic (the soft version of your bug) ---------------
    # Heuristic only: mtimes are unreliable. A fresh export can trip this if the
    # audio copy finished after export.pdb was written (copy lag), and a
    # timestamp-preserving copy (rsync -t / restore) can hide genuinely new
    # tracks. Use a 5-minute window to absorb normal copy lag, and word it as a
    # prompt to double-check rather than a definitive failure.
    STALE_WINDOW = 300
    if pdb_mtime and newest_audio_mtime:
        gap = newest_audio_mtime - pdb_mtime
        if gap > STALE_WINDOW:
            warnings.append(
                "Newest audio is newer than the last export.pdb write — you MAY have added "
                f"tracks ({fmt_age(newest_audio_mtime)}) after your last export "
                f"({fmt_age(pdb_mtime)}). If so, those additions and any new playlist are "
                "NOT on the CDJ — re-run Export to Device. (Heuristic: a fresh export whose "
                "audio copy lagged the database write can also trip this; verify in rekordbox.)")

    # ---- Verdict ------------------------------------------------------------
    print("\n" + "=" * 64)
    if problems:
        print("  VERDICT:  ✗ FAIL — this stick is not CDJ-ready as-is")
        for p in problems:
            print(f"     • {p}")
    elif warnings:
        print("  VERDICT:  ⚠ NOT CONCLUSIVE — looks exported, but check the warnings")
    else:
        print("  VERDICT:  ✓ PASS — real export present, no integrity problems found")
    for w in warnings:
        print(f"     ⚠ {w}")
    print("=" * 64)

    # Honest scope reminder
    print("\nNote: a PASS means a real, fresh export is present and no zero-byte files were")
    print("seen — it does NOT deep-verify every file, nor confirm a *named* playlist is")
    print("inside export.pdb (that's the Phase 2 parser, per-playlist ✅/❌).")

    # Exit codes (so `preflight && eject`-style gates behave):
    #   0 = clean PASS    1 = hard FAIL (problems)    3 = warnings (not conclusive)
    # Staleness, unreadable folders, and "no audio" are warnings — non-zero on
    # purpose so a script does NOT silently proceed past them.
    if problems:
        sys.exit(1)
    print("\nExit code:", 3 if warnings else 0,
          "(0=pass, 1=fail, 3=warnings — see above)")
    sys.exit(3 if warnings else 0)


if __name__ == "__main__":
    main()
