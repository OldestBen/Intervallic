"""Handles writing playlist files locally or uploading via SFTP."""
from __future__ import annotations

import io
import os
import posixpath
import re
import stat
import tempfile
from pathlib import Path
from typing import List, Tuple

from .config import Config, SftpConfig
from .plex_client import PlexPlaylist, PlexTrack


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _build_m3u_content(playlist: PlexPlaylist, config: Config) -> str:
    lines: List[str] = ["#EXTM3U", f"#PLAYLIST:{playlist.name}", ""]
    for track in playlist.tracks:
        artist_title = f"{track.artist} - {track.title}" if track.artist else track.title
        lines.append(f"#EXTINF:-1,{artist_title}")
        lines.append(config.remap_path(track.file_path))
    return "\n".join(lines) + "\n"


def _encoding(fmt: str) -> str:
    return "utf-8" if fmt == "m3u8" else "latin-1"


# ── Local output ──────────────────────────────────────────────────────────────

def write_local(playlist: PlexPlaylist, config: Config) -> Path:
    assert config.output.directory
    ext = f".{config.output.format}"
    out_dir = Path(config.output.directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / (_safe_filename(playlist.name) + ext)

    if dest.exists() and not config.output.overwrite:
        return dest

    dest.write_text(
        _build_m3u_content(playlist, config),
        encoding=_encoding(config.output.format),
    )
    return dest


# ── SFTP output ───────────────────────────────────────────────────────────────

def _sftp_mkdir_p(sftp, remote_dir: str) -> None:
    """Create remote directory tree, skipping existing nodes."""
    parts = remote_dir.replace("\\", "/").split("/")
    current = ""
    for part in parts:
        if not part:
            current = "/"
            continue
        current = posixpath.join(current, part) if current != "/" else "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _open_sftp(sftp_cfg: SftpConfig):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = dict(
        hostname=sftp_cfg.host,
        port=sftp_cfg.port,
        username=sftp_cfg.username or None,
    )
    if sftp_cfg.key_path:
        connect_kwargs["key_filename"] = sftp_cfg.key_path
    if sftp_cfg.password:
        connect_kwargs["password"] = sftp_cfg.password

    client.connect(**connect_kwargs)
    return client, client.open_sftp()


def write_sftp(playlist: PlexPlaylist, config: Config) -> str:
    assert config.output.sftp
    sftp_cfg = config.output.sftp
    ext = f".{config.output.format}"
    filename = _safe_filename(playlist.name) + ext
    remote_path = posixpath.join(sftp_cfg.remote_directory, filename)

    content = _build_m3u_content(playlist, config).encode(_encoding(config.output.format))

    ssh, sftp = _open_sftp(sftp_cfg)
    try:
        _sftp_mkdir_p(sftp, sftp_cfg.remote_directory)

        try:
            sftp.stat(remote_path)
            exists = True
        except FileNotFoundError:
            exists = False

        if exists and not config.output.overwrite:
            return remote_path

        with sftp.open(remote_path, "wb") as f:
            f.write(content)
    finally:
        sftp.close()
        ssh.close()

    return remote_path


def test_sftp_connection(sftp_cfg: SftpConfig) -> Tuple[bool, str]:
    """Returns (ok, message). Used by the setup wizard."""
    try:
        ssh, sftp = _open_sftp(sftp_cfg)
        sftp.close()
        ssh.close()
        return True, "Connection successful."
    except Exception as exc:
        return False, str(exc)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def write_playlist(playlist: PlexPlaylist, config: Config):
    if config.output.sftp:
        return write_sftp(playlist, config)
    return write_local(playlist, config)


def write_all_playlists(playlists: List[PlexPlaylist], config: Config) -> list:
    return [write_playlist(pl, config) for pl in playlists]
