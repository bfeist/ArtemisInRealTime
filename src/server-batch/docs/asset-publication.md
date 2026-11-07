# Publishing assets to InterServer

Asset publication stays separate from application CI. `publish_assets.py` adopts
the existing `C:/Users/Feist/wsl_scripts/rsync_artirt_web_to_interserver.sh`
workflow: direct rsync over SSH to
`bfeist@162.246.19.235:/var/www/html/media.artemisinrealtime.org/`.

From `src/server-batch` in PowerShell:

```powershell
# Publish both missions, just like the existing manual script.
.venv/Scripts/python.exe publish_assets.py

# Publish one mission.
.venv/Scripts/python.exe publish_assets.py --mission artemis-ii

# Optional preview.
.venv/Scripts/python.exe publish_assets.py --mission artemis-ii --dry-run
```

Windows automatically invokes WSL's rsync and SSH, using the existing WSL keys
and SSH configuration. `--distribution Ubuntu` selects a particular WSL
distribution. On Linux, the same command uses native rsync. `DATA_DIR` supplies
the local asset root; `--destination user@host:/path/` overrides the server.

The pipeline can invoke it directly:

```powershell
.venv/Scripts/python.exe refresh_assets.py --mission artemis-ii --stage publish
# Or:
.venv/Scripts/python.exe run_all.py --mission artemis-ii --step assets-publish
```

`publish` is separate from `build`, so a local rebuild does not start an upload.
The refresh runner saves the usual job log. The direct command displays rsync
progress in the terminal and returns its exit status.

From WSL, the repository launcher uses the existing Windows Python environment:

```bash
bash /mnt/f/_repos/ArtemisInRealTime/src/server-batch/publish_assets_wsl.sh
# Optional preview:
bash /mnt/f/_repos/ArtemisInRealTime/src/server-batch/publish_assets_wsl.sh --dry-run
```

This replaces the manual script as the maintained entry point; the original
file has not been edited. No deployment-plan files, remote Python helper,
remote verification protocol, or publication approval step is required.

## What is copied

The command keeps the original `-rt`, `--delete`, partial-transfer support,
progress/stats, and SSH keepalive settings. It selects mission `web` trees and
skips traversing raw/processed holdings. A single-mission invocation leaves other
missions excluded from copying and deletion. Unfinished `releases/.staging-*`
builds and `.rsync-partial` directories are excluded.

Files are uploaded/replaced directly, and stale included files are deleted to
match the local tree. Finalized releases and any local `active-release.json`
are ordinary files in that tree and are copied too. This command does not
create or change the local pointer. It does not enforce a release-only
allowlist: keeping the local web tree tidy determines what gets hosted.

JSON `.gz` sidecars are copied along with their originals. Whether browsers
receive compressed JSON still depends on the web server's compression setup;
rsync does not configure HTTP serving.

## Validation

The direct command passed a real Artemis II dry run against InterServer on
2026-11-07. It examined 58,350 files and reported 2,001 files to transfer
(1,029,569,496 bytes), with three stale files to delete. No file contents were
uploaded during the preview. Local rsync fixtures also confirmed replacement,
deletion, raw/staging exclusions, and preservation of other missions when
selecting only Artemis II.
