# Intervallic

**One-way playlist sync from Plex to Roon.**

Intervallic pulls your audio playlists from Plex and writes them as M3U/M3U8 files directly to wherever Roon can find them — over SMB, SFTP, or a local path. Run it once to catch up, or drop it in a cron job to stay in sync automatically.

---

## How it works

```
Plex  ──► Intervallic ──► M3U files ──► Roon
                  ▲
           path remapping
         (Plex paths → Roon paths)
```

1. Connects to your Plex server and fetches all audio playlists (or a filtered subset).
2. Applies any path mappings so the file paths inside the playlist make sense to Roon.
3. Writes one `.m3u` or `.m3u8` file per playlist to your chosen destination.
4. Roon picks them up automatically on the next library scan.

---

## Requirements

- Python 3.9+
- A running Plex Media Server
- Roon with at least one watched storage location (SMB share, local path, or SSH-accessible directory)

---

## Installation

```bash
git clone https://github.com/OldestBen/Intervallic.git
cd Intervallic
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

---

## Quick start

Run the interactive setup wizard:

```bash
intervallic setup
```

The wizard walks you through three steps:

1. **Plex authentication** — sign in via browser OAuth (no password stored in plaintext)
2. **Output destination** — choose SMB (recommended for Roon), SFTP, or a local path
3. **Path mapping** — translate Plex-side file paths to the paths Roon sees

When finished it writes `config.yaml`. Test it immediately:

```bash
intervallic sync --dry-run   # lists playlists, writes nothing
intervallic sync             # the real thing
```

---

## Configuration

`config.yaml` is the single source of truth. Run `intervallic setup` to generate it, or write it by hand using `config.example.yaml` as a reference.

### Minimal example (SMB)

```yaml
plex:
  url: "http://192.168.1.10:32400"
  token: "YOUR_PLEX_TOKEN"

output:
  format: m3u
  overwrite: true
  smb:
    server: "192.168.1.20"     # NAS IP or hostname
    share: "music"             # share name as seen in Roon → Settings → Storage
    directory: "Playlists"     # subfolder within the share (created if absent)
    username: "roon"
    password: "secret"
```

### Full reference

```yaml
plex:
  url: "http://<host>:32400"
  token: "<plex-auth-token>"
  playlist_filter:             # only sync these playlists (empty = all)
    - "My Favourites"
    - "Chill"
  playlist_exclude:            # always skip these
    - "Watch Later"

output:
  format: m3u          # m3u or m3u8 (both use UTF-8 encoding)
  overwrite: true      # overwrite existing files on each sync

  # ── Option A: SMB share ──────────────────────────────────────────────────
  smb:
    server: "192.168.1.20"
    share: "music"
    directory: "Playlists"     # optional subdirectory
    username: "roon"
    password: "secret"
    domain: ""                 # leave blank for workgroup / local accounts

  # ── Option B: SFTP to Roon host ──────────────────────────────────────────
  # sftp:
  #   host: "192.168.1.50"
  #   port: 22
  #   username: "root"
  #   remote_directory: "/mnt/music/Playlists"
  #   key_path: "~/.ssh/id_rsa"
  #   # password: "secret"

  # ── Option C: local path ─────────────────────────────────────────────────
  # directory: "/mnt/music/Playlists"

# ── Path mapping ─────────────────────────────────────────────────────────────
# Translate Plex-side paths to the paths Roon sees on the same storage.
# Only needed when Plex and Roon mount the same library at different paths.
path_mapping:
  - from: "/data/music"
    to:   "/mnt/nas/music"
```

---

## Path mapping explained

Plex stores the absolute file path it knows for each track, e.g.:

```
/shared/Music/John Martyn/Solid Air/01 Solid Air.flac
```

Roon mounts the same NAS share at a different path, e.g.:

```
/mnt/RoonStorage_9fdc3ae4/Music/John Martyn/Solid Air/01 Solid Air.flac
```

Without a mapping, Roon can't find the files referenced in the playlist. Add a mapping:

```yaml
path_mapping:
  - from: "/shared/Music"
    to:   "/mnt/RoonStorage_9fdc3ae4/Music"
```

Multiple mappings are supported; the first match wins.

**How to find your Roon path:** browse to any track in Roon → right-click → File Info, or use the `diagnose` command (see below).

---

## Commands

### `intervallic sync`

Fetch playlists from Plex and write them to your configured destination.

```
Usage: intervallic sync [OPTIONS]

Options:
  -c, --config PATH  Path to config file  [default: config.yaml]
  --dry-run          List playlists without writing any files
  --help             Show this message and exit
```

### `intervallic setup`

Interactive wizard that creates `config.yaml`.

```
Usage: intervallic setup [OPTIONS]

Options:
  -o, --output PATH  Where to write the generated config file  [default: config.yaml]
  --help             Show this message and exit
```

### `intervallic audit`

Scan your Plex music library for incomplete albums — missing tracks, gaps in track numbering, and files with no track number at all.

```
Usage: intervallic audit [OPTIONS]

Options:
  -c, --config PATH   Path to config file  [default: config.yaml]
  --section TEXT      Music library section name (default: all music sections)
  -o, --output FILE   Write full report to a CSV file
  --help              Show this message and exit
```

**What it checks:**

| Issue | Meaning |
|-------|---------|
| `MISSING` | A gap in the track sequence — e.g. tracks 1, 2, 4, 5 means track 3 is absent |
| `NO NUM` | The file has no track number set in Plex at all |
| `DUPE` | Two files share the same track number |

Terminal output shows every affected album with per-track detail. Pass `-o report.csv` to export the full list as a CSV for sorting and filtering in a spreadsheet.

```bash
intervallic audit                         # scan all music sections
intervallic audit --section "Music"       # specific section
intervallic audit -o incomplete.csv       # export to CSV
```

### `intervallic diagnose HOST`

SSH into your Roon host and report everything found: mounts, audio directories, existing playlist locations. Useful for setting up `path_mapping` or `remote_directory`.

```
Usage: intervallic diagnose [OPTIONS] HOST

Options:
  --port INTEGER     SSH port  [default: 22]
  -u, --username TEXT  SSH username  [default: root]
  -p, --password TEXT  SSH password (omit to use key auth)
  -i, --key TEXT       Path to SSH private key
  --help             Show this message and exit

Examples:
  intervallic diagnose 192.168.1.50 -u root -p mypassword
  intervallic diagnose 192.168.1.50 -i ~/.ssh/id_rsa
```

---

## Automating sync with cron

To keep playlists in sync, schedule a daily run:

```bash
# Edit crontab
crontab -e

# Add a line — runs every day at 3 AM
0 3 * * * /path/to/.venv/bin/intervallic sync --config /path/to/config.yaml
```

---

## Troubleshooting

**Roon doesn't see the playlists after sync**

- Check that Roon has a watched storage location that covers the directory you're writing to.
- Trigger a library rescan: Roon → Settings → Storage → Force Rescan.
- Confirm the path inside the `.m3u` file matches the path Roon expects. Use `--dry-run` first and inspect the output files manually.

**Smart / dynamic playlists are skipped with a warning**

Plex smart playlists can't return their track list via the standard API. Intervallic logs a warning and skips them — this is expected behaviour.

**`Failed to load config: 'from'`**

The `path_mapping` section uses `from` and `to` as keys (not `plex`/`roon`):

```yaml
path_mapping:
  - from: "/plex/path"
    to:   "/roon/path"
```

**SMB connection errors**

- Verify the server IP, share name, and credentials in `config.yaml`.
- Check that the share is accessible from the machine running Intervallic (e.g. `smbclient //server/share -U username`).
- If using a domain account, add `domain: "MYDOMAIN"` to the `smb` section.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
