# Dual-OS Claude Code Sync

Synchronize Claude Code project contexts across Windows and Linux dual-boot setups — including automatic path translation inside JSON/JSONL files.

## Problem

You dual-boot Windows and Linux on the same machine. You use Claude Code on both OSes. But:

- Claude Code stores session data in `~/.claude/projects/` with platform-specific absolute paths
- A Windows path like `E:\DL\vggt` embedded in a JSONL file is meaningless on Linux
- Manually copying directories breaks the paths, causing Claude Code to lose context

This tool syncs the `.claude/projects/` directories between OSes and **rewrites all embedded paths** so every session works natively on either system.

## Features

- **Bidirectional sync** — auto-detects which side is newer, or force a direction
- **Path rewriting** — finds and replaces Windows ↔ Linux paths inside `.jsonl` and `.json` files, including paths embedded in user messages, `cwd` fields, and IDE selection tags
- **Auto-discovery** (`--auto`) — scans `.claude/projects/` on both sides, reads `cwd` from session files, matches projects across OSes, and generates sync tasks automatically
- **Safety** — read-only mount detection, split-brain protection (backup + abort), atomic writes via temp directory
- **State tracking** — records per-side mtimes so repeated runs are fast (only syncs when something changed)

## Requirements

- Python 3.10+
- Linux: Windows partitions mounted (via `/etc/fstab` or manual mount)
- Dependencies: `pydantic`, `pyyaml`

```bash
pip install pydantic pyyaml
```

## Quick Start

### 1. Configure mount points

Edit `config.yaml` to match your mount layout:

```yaml
access:
  linux_mount_win_c: "/mnt/c"                          # C: drive mount
  linux_mount_win_d: "/mnt/d"                          # D: drive mount
  linux_mount_win_e: "/mnt/e"                          # E: drive mount
```

### 2. Sync everything (auto-discover)

```bash
# Preview what would happen
python -m dual_os_sync.cli -c config.yaml --auto --dry-run -v

# Execute sync
python -m dual_os_sync.cli -c config.yaml --auto
```

### 3. Force a full re-sync

If you've deleted data on one side and want to overwrite it from the other:

```bash
python -m dual_os_sync.cli -c config.yaml --auto --force-sync
```

### 4. Manual direction control

```bash
# Push Linux → Windows
python -m dual_os_sync.cli -c config.yaml --auto --force linux_to_win

# Push Windows → Linux
python -m dual_os_sync.cli -c config.yaml --auto --force win_to_linux
```

## Configuration

### `config.yaml`

```yaml
settings:
  sync_strategy: "auto_newest"         # auto_newest | force_win_to_linux | force_linux_to_win
  state_file_path: "~/.config/dual_os_sync/sync_state.json"

access:
  linux_mount_win_c: "/home/xhkzdepartedream/windows_c"
  linux_mount_win_d: "/home/xhkzdepartedream/data"
  linux_mount_win_e: "/home/xhkzdepartedream/data2"

base_mappings:
  - id: dl_workspace
    win: "E:\\DL\\"
    linux: "/home/xhkzdepartedream/data2/DL/"
```

| Section | Purpose |
|---------|---------|
| `settings.sync_strategy` | Direction detection mode. `auto_newest` compares mtimes |
| `access` | Linux-side mount points for Windows drives. Used for path resolution and content replacement fallback |
| `base_mappings` | Explicit path prefix mappings for content replacement. Auto-discover can also infer these |

With `--auto`, you don't need to manually list `tasks` — they are discovered by scanning `.claude/projects/` on both OSes.

## Auto-Discovery (`--auto`)

The auto-discovery flow:

```
Scan .claude/projects/ on both OSes
        │
        ▼
Read cwd from each project's .jsonl sessions
        │
        ▼
Match Windows ↔ Linux projects by resolving paths
        │
        ▼
Infer base mappings from matched pairs (e.g., D:\tools\ ↔ /mnt/d/tools/)
        │
        ▼
Generate TaskConfig for each project → run sync
```

This replaces the manual `tasks` list in `config.yaml`.

## CLI Reference

```
usage: python -m dual_os_sync.cli [-h] [-c CONFIG] [-t TASK] [--dry-run]
                                   [--force {win_to_linux,linux_to_win}]
                                   [-v] [--auto] [--force-sync]

Options:
  -c, --config              Config file path (default: config.yaml)
  -t, --task                Run only the named task
  --dry-run                 Preview without making changes
  --force {win_to_linux,linux_to_win}
                            Override sync direction
  -v, --verbose             Enable debug logging
  --auto                    Auto-discover projects instead of using configured tasks
  --force-sync              Ignore saved state and always sync
```

### Output format

```
[SYNCED]   auto: D:\tools\sync — /src → /dst
[OK]       auto: E:\DL\vggt — already in sync
[DRY-RUN]  auto: C:\Users\30413 — would sync ... → ...
[CONFLICT] auto: E:\notes\... — split-brain detected, backed up
[ERROR]    auto: D:\sync — target parent is read-only, aborting
[SKIP]     auto: ... — neither side has data
```

## Safety Mechanisms

| Mechanism | What it does |
|-----------|-------------|
| **Read-only detection** | Checks `os.W_OK` on target parent before writing. Aborts with `[ERROR]` if read-only |
| **Split-brain protection** | If both sides changed since last sync, backs up target to `.bak` and aborts |
| **Atomic writes** | Copies to `.tmp_sync/` first, then `os.replace()` for atomic swap |
| **Binary protection** | Only `.jsonl`/`.json` files get content replacement — other files are copied as-is |

## Deployment

### Linux (systemd shutdown hook)

```ini
# /etc/systemd/system/dual-os-sync.service
[Unit]
Description=Dual-OS Claude Code Sync (shutdown trigger)
DefaultDependencies=no
Before=shutdown.target reboot.target halt.target

[Service]
Type=oneshot
RemainAfterExit=true
ExecStop=/usr/bin/python -m dual_os_sync.cli --auto -c /home/user/.config/dual_os_sync/config.yaml
User=user
WorkingDirectory=/home/user/tools/sync

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable dual-os-sync.service
sudo systemctl start dual-os-sync.service
```

### Windows (Task Scheduler)

Use Task Scheduler to run on shutdown with `--force win_to_linux` (since Windows can't easily read Linux partitions, sync direction is fixed).

### Manual alias

```bash
alias cc-sync='python -m dual_os_sync.cli --auto -c ~/.config/dual_os_sync/config.yaml'
```

## How It Works

```
Scan both directories (recursive mtime crawl)
        │
        ▼
Determine direction (compare mtimes + saved state)
        │
        ▼
Copy source → .tmp_sync/
        │
        ▼
Walk .jsonl/.json files, replace embedded paths:
  - Structured fields (cwd, filePath, filename)
  - Embedded paths in user messages and IDE tags
  - Both backslash and forward-slash variants
        │
        ▼
Atomic swap: .tmp_sync/ → target/
        │
        ▼
Update sync_state.json with per-side mtimes
```

## Project Structure

```
dual_os_sync/
├── __init__.py
├── cli.py                 # CLI entry point
├── config.py              # Pydantic config models
├── state.py               # Sync state tracking (mtime persistence)
├── direction.py           # Direction detection + mtime comparison
├── path_mapper.py         # Path prefix mapping functions
├── content_replacer.py    # JSON/JSONL path rewriting
├── sync_engine.py         # Orchestration (copy, replace, safety)
└── auto_discover.py       # Project auto-discovery (--auto)
```

## License

MIT
