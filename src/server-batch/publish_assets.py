"""Publish web assets directly with the existing rsync/SSH workflow.

Windows calls rsync through WSL; Linux uses native rsync. This is independent
of application CI. Pass --dry-run for a preview; otherwise files are uploaded.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

DEFAULT_DESTINATION = "bfeist@162.246.19.235:/var/www/html/media.artemisinrealtime.org/"
SSH = "ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o TCPKeepAlive=yes"


def command(source, mission="all", *, dry_run=False, destination=DEFAULT_DESTINATION,
            transport="auto", distribution=None):
    use_wsl = transport == "wsl" or (transport == "auto" and os.name == "nt")
    if distribution and not use_wsl:
        raise ValueError("--distribution requires WSL transport")
    if mission != "all" and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", mission):
        raise ValueError("Invalid mission slug")
    prefix = (["wsl.exe"] + (["--distribution", distribution] if distribution else [])
              + ["--exec"]) if use_wsl else []
    source = str(Path(source).resolve())
    if use_wsl:
        source = subprocess.check_output(prefix + ["wslpath", "-a", "-u", source],
                                         text=True, encoding="utf-8").strip()
    mission_dir = "/*/" if mission == "all" else f"/{mission}/"
    include = "/*/web/***" if mission == "all" else f"/{mission}/web/***"
    args = [*prefix, "rsync", "-rt", "--delete", "--prune-empty-dirs",
            "--partial", "--partial-dir=.rsync-partial", "--protect-args",
            "--info=flist2,progress2", "--stats", "-e", SSH,
            # Incomplete local release builds are not published.
            "--exclude=**/web/releases/.staging-*/***",
            "--exclude=**/.rsync-partial/***",
            "--include=" + mission_dir, "--include=" + include, "--exclude=*"]
    if dry_run:
        args.append("--dry-run")
    return args + [source.rstrip("/") + "/", destination]


def main():
    from config import DATA_DIR, MISSIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=["all", *MISSIONS], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Show rsync changes without uploading")
    parser.add_argument("--destination", default=DEFAULT_DESTINATION, help="rsync user@host:path destination")
    parser.add_argument("--transport", choices=["auto", "wsl", "native"], default="auto")
    parser.add_argument("--distribution", help="WSL distribution; defaults to your current WSL default")
    args = parser.parse_args()
    if not DATA_DIR.is_dir():
        parser.error(f"Asset directory does not exist: {DATA_DIR}")
    print(f"{'Previewing' if args.dry_run else 'Publishing'} {args.mission} web assets to {args.destination}", flush=True)
    result = subprocess.run(command(DATA_DIR, args.mission, dry_run=args.dry_run,
                                    destination=args.destination, transport=args.transport,
                                    distribution=args.distribution))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
