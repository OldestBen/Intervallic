"""
Roon Core auto-discovery and remote path detection.

Key facts about Roon playlist import (verified against Roon docs):
  - M3U8 files must be placed INSIDE a folder Roon is already watching
    for music. There is no separate playlist import folder.
  - For SMB/CIFS mounts, the files go into the mount point, not the
    local LXC filesystem.
  - Roon's watched folder config is in a proprietary database — not
    directly readable. We infer watched folders from CIFS mounts and
    directories containing audio files.

Roon data directories by install type:
  - Easy installer:  /var/roon/RoonServer
  - Manual tar.gz:   ~/.RoonServer
  - DietPi:          /mnt/dietpi_userdata/roonserver
  - Custom:          $ROON_DATAROOT/RoonServer
"""
from __future__ import annotations

from typing import Optional


# ── Core discovery via SOOD UDP broadcast ─────────────────────────────────────

def discover_roon_core(timeout: int = 6) -> Optional[str]:
    """
    Broadcast a SOOD query and return the IP of the first Roon Core found,
    or None. Only works on the same VLAN/broadcast domain.
    """
    try:
        from roonapi import RoonDiscovery
    except ImportError:
        return None

    import threading

    result: dict = {}

    def _run():
        disc = RoonDiscovery()
        host, _ = disc.first()
        if host:
            result["host"] = host

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result.get("host")


# ── Remote path detection via SSH ─────────────────────────────────────────────

def _home_for_user(sftp, username: str) -> str:
    """Return the home directory for username, handling root correctly."""
    if username == "root":
        return "/root"
    try:
        # Read /etc/passwd to find the home dir
        with sftp.open("/etc/passwd") as f:
            for line in f.read().decode(errors="replace").splitlines():
                parts = line.split(":")
                if len(parts) >= 6 and parts[0] == username:
                    return parts[5]
    except Exception:
        pass
    return f"/home/{username}"


def find_remote_playlist_paths(
    host: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> list[str]:
    """
    SSH into host and return a ranked list of candidate directories where
    Roon-watched M3U8 files should be placed.

    Strategy (in priority order):
      1. SMB/CIFS mounts — almost certainly Roon's watched music library
      2. Directories containing audio files under common mount points
      3. Existing .m3u/.m3u8 files (confirms where imports already live)
      4. Falls back to a Playlists subdir under the user's home

    Returns [] on any failure — caller falls back to manual entry.
    """
    try:
        import paramiko
    except ImportError:
        return []

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kw: dict = dict(hostname=host, port=port, username=username or None)
    if key_path:
        connect_kw["key_filename"] = key_path
    if password:
        connect_kw["password"] = password

    def run(cmd: str, timeout: int = 15) -> list[str]:
        try:
            _, stdout, _ = client.exec_command(cmd, timeout=timeout)
            return [
                l.strip()
                for l in stdout.read().decode(errors="replace").splitlines()
                if l.strip()
            ]
        except Exception:
            return []

    candidates: list[str] = []

    try:
        client.connect(**connect_kw, timeout=10)
        sftp = client.open_sftp()
        home = _home_for_user(sftp, username)
        sftp.close()

        # ── 1. SMB/CIFS mounts ───────────────────────────────────────────────
        # These are Roon's watched music library folders. Playlist files must
        # go here (or in a subfolder) for Roon to import them.
        mounts = run("awk '$3==\"cifs\" || $3==\"smb3\" || $3==\"smb\" {print $2}' /proc/mounts")

        confirmed_music: list[str] = []
        for mount in mounts:
            # Verify it actually contains audio files (confirms Roon watches it)
            has_audio = run(
                f"find {mount} -maxdepth 4 "
                r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.alac' "
                r"-o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.wav' \) "
                "-print -quit 2>/dev/null",
                timeout=10,
            )
            if has_audio:
                confirmed_music.append(mount)

        # Check for existing playlist files inside confirmed music mounts
        # (tells us exactly where imports already live)
        for mount in confirmed_music:
            existing = run(
                f"find {mount} -maxdepth 4 "
                r"\( -iname '*.m3u8' -o -iname '*.m3u' \) "
                r"-printf '%h\n' 2>/dev/null | sort -u | head -5",
                timeout=10,
            )
            for d in existing:
                if d not in candidates:
                    candidates.append(d)

        # Suggest a Playlists subdir inside each confirmed music mount
        for mount in confirmed_music:
            suggestion = mount.rstrip("/") + "/Playlists"
            if suggestion not in candidates:
                candidates.append(suggestion)

        # ── 2. Audio files under common non-CIFS mount points ────────────────
        # Catches NFS mounts, bind mounts, or direct local libraries
        if not confirmed_music:
            audio_dirs = run(
                r"find /mnt /media /data /srv -maxdepth 5 "
                r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.alac' \) "
                r"-printf '%h\n' 2>/dev/null | sort -u | head -8",
                timeout=15,
            )
            for d in audio_dirs:
                suggestion = d.rstrip("/") + "/Playlists"
                if suggestion not in candidates:
                    candidates.append(suggestion)

        # ── 3. DietPi music directory ─────────────────────────────────────────
        dietpi_music = "/mnt/dietpi_userdata/Music"
        dietpi_lines = run(f"test -d {dietpi_music} && echo yes")
        if dietpi_lines:
            suggestion = dietpi_music + "/Playlists"
            if suggestion not in candidates:
                candidates.append(suggestion)

        # ── 4. Fallback: Playlists subdir in user home ───────────────────────
        fallback = home.rstrip("/") + "/Music/Playlists"
        if fallback not in candidates:
            candidates.append(fallback)

    except Exception:
        pass
    finally:
        client.close()

    return candidates
