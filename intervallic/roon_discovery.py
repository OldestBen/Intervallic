"""
Roon Core auto-discovery and remote path detection.

Roon's API does not expose watched folder paths, so we:
  1. Use RoonDiscovery (UDP multicast/broadcast) to find the Core IP
  2. SSH into the Core host and scan for likely playlist import directories
"""
from __future__ import annotations

from typing import Optional


# ── Core discovery ─────────────────────────────────────────────────────────────

def discover_roon_core(timeout: int = 8) -> Optional[tuple[str, int]]:
    """
    Broadcast a SOOD query and return (host, http_port) of the first
    Roon Core found on the network, or None if none responded within timeout.
    """
    try:
        from roonapi import RoonDiscovery
    except ImportError:
        return None

    import threading

    result: dict = {}

    def _run():
        disc = RoonDiscovery()
        host, port = disc.first()
        if host:
            result["host"] = host
            result["port"] = port

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if result.get("host"):
        return result["host"], int(result["port"])
    return None


# ── Remote path detection via SSH ─────────────────────────────────────────────

# Directories we try on the remote host, in priority order.
# We're looking for a folder Roon can watch for imported M3U8 playlists;
# it must sit inside (or alongside) the music library root.
_CANDIDATE_COMMANDS = [
    # Existing Playlists dirs anywhere under common roots
    r"find /mnt /media /home /data /srv /opt -maxdepth 6 "
    r"-type d \( -iname 'playlists' -o -iname 'imported' -o -iname 'roon*playlist*' \) "
    r"2>/dev/null | head -20",

    # Directories containing audio files (use the parent as the library root)
    r"find /mnt /media /home /data /srv /opt -maxdepth 5 "
    r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.alac' -o -iname '*.aac' \) "
    r"-printf '%h\n' 2>/dev/null | sort -u | head -10",
]


def find_remote_playlist_paths(
    host: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> list[str]:
    """
    SSH into host and return a ranked list of candidate playlist directories.
    Returns empty list on any failure (non-fatal — caller falls back to manual entry).
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

    candidates: list[str] = []
    try:
        client.connect(**connect_kw, timeout=10)

        existing_playlist_dirs: list[str] = []
        library_roots: list[str] = []

        for cmd in _CANDIDATE_COMMANDS:
            _, stdout, _ = client.exec_command(cmd, timeout=15)
            lines = [l.strip() for l in stdout.read().decode(errors="replace").splitlines() if l.strip()]
            if not existing_playlist_dirs and lines and "find" in cmd and "playlists" in cmd.lower():
                existing_playlist_dirs = lines
            elif lines:
                library_roots = lines

        # Existing playlist dirs are best candidates
        candidates.extend(existing_playlist_dirs)

        # Suggest a Playlists subdir next to each library root
        for root in library_roots:
            suggestion = root.rstrip("/") + "/Playlists"
            if suggestion not in candidates:
                candidates.append(suggestion)

    except Exception:
        pass
    finally:
        client.close()

    return candidates
