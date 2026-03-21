"""
Cleanup script for app/input/ and app/output/ folders.

Usage:
  # Delete files older than 24 hours (default)
  python cleanup.py

  # Delete files older than N hours
  python cleanup.py --hours 48

  # Dry run — show what would be deleted without deleting
  python cleanup.py --dry-run

  # Keep only the N most recent files in each folder
  python cleanup.py --keep 20

  # Run as a scheduled loop every X minutes
  python cleanup.py --watch --interval 60
"""

import argparse
import os
import time
from pathlib import Path

APP_DIR    = Path(__file__).parent
INPUT_DIR  = APP_DIR / "input"
OUTPUT_DIR = APP_DIR / "output"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cleanup(
    max_age_hours: float = 24.0,
    keep: int = 0,
    dry_run: bool = False,
):
    deleted = 0
    freed   = 0

    for folder in (INPUT_DIR, OUTPUT_DIR):
        if not folder.exists():
            print(f"[skip]  {folder} does not exist")
            continue

        files = sorted(
            [f for f in folder.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
            reverse=True,   # newest first
        )

        cutoff = time.time() - max_age_hours * 3600

        for i, f in enumerate(files):
            mtime = f.stat().st_mtime
            age_h = (time.time() - mtime) / 3600
            size  = f.stat().st_size

            # Delete if: older than max_age OR beyond keep limit
            should_delete = (age_h > max_age_hours) or (keep > 0 and i >= keep)

            if should_delete:
                if dry_run:
                    print(f"[dry]   {f.name}  ({human_size(size)}, {age_h:.1f}h old)")
                else:
                    f.unlink()
                    print(f"[del]   {f.name}  ({human_size(size)}, {age_h:.1f}h old)")
                deleted += 1
                freed   += size
            else:
                print(f"[keep]  {f.name}  ({human_size(size)}, {age_h:.1f}h old)")

    print(f"\n{'[dry-run] Would remove' if dry_run else 'Removed'} "
          f"{deleted} file(s), freed {human_size(freed)}")


def main():
    p = argparse.ArgumentParser(description="Clean app/input/ and app/output/")
    p.add_argument("--hours",    type=float, default=24.0,
                   help="Delete files older than this many hours (default: 24)")
    p.add_argument("--keep",     type=int,   default=0,
                   help="Keep only the N most recent files per folder (0 = no limit)")
    p.add_argument("--dry-run",  action="store_true",
                   help="Show what would be deleted without actually deleting")
    p.add_argument("--watch",    action="store_true",
                   help="Run in a loop (combine with --interval)")
    p.add_argument("--interval", type=int,   default=60,
                   help="Loop interval in minutes when --watch is set (default: 60)")
    a = p.parse_args()

    if a.watch:
        print(f"[watch] Running cleanup every {a.interval} min "
              f"(max age: {a.hours}h, keep: {a.keep or 'all'})")
        while True:
            print(f"\n[{time.strftime('%H:%M:%S')}] Running cleanup ...")
            cleanup(max_age_hours=a.hours, keep=a.keep, dry_run=a.dry_run)
            time.sleep(a.interval * 60)
    else:
        cleanup(max_age_hours=a.hours, keep=a.keep, dry_run=a.dry_run)


if __name__ == "__main__":
    main()