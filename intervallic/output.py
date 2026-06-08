"""Handles writing playlist files locally, via SFTP, or directly to an SMB share."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import List, Tuple

from .config import Config, SftpConfig, SmbConfig
from .plex_client import PlexPlaylist


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
    dest.write_text(_build_m3u_content(playlist, config), encoding=_encoding(config.output.format))
    return dest


# ── SFTP output ───────────────────────────────────────────────────────────────

def _open_sftp(sftp_cfg: SftpConfig):
    from .ssh_util import _open_ssh
    client = _open_ssh(
        sftp_cfg.host, sftp_cfg.port, sftp_cfg.username,
        password=sftp_cfg.password, key_path=sftp_cfg.key_path,
    )
    return client, client.open_sftp()


def _sftp_mkdir_p(sftp, remote_dir: str) -> None:
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


def write_sftp(playlist: PlexPlaylist, config: Config) -> str:
    assert config.output.sftp
    cfg = config.output.sftp
    filename = _safe_filename(playlist.name) + f".{config.output.format}"
    remote_path = posixpath.join(cfg.remote_directory, filename)
    content = _build_m3u_content(playlist, config).encode(_encoding(config.output.format))

    ssh, sftp = _open_sftp(cfg)
    try:
        _sftp_mkdir_p(sftp, cfg.remote_directory)
        try:
            sftp.stat(remote_path)
            if not config.output.overwrite:
                return remote_path
        except FileNotFoundError:
            pass
        with sftp.open(remote_path, "wb") as f:
            f.write(content)
    finally:
        sftp.close()
        ssh.close()
    return remote_path


def test_sftp_connection(sftp_cfg: SftpConfig) -> Tuple[bool, str]:
    try:
        ssh, sftp = _open_sftp(sftp_cfg)
        sftp.close()
        ssh.close()
        return True, "Connection successful."
    except Exception as exc:
        return False, str(exc)


# ── SMB output ────────────────────────────────────────────────────────────────

def _smb_unc(smb_cfg: SmbConfig, filename: str) -> str:
    """Return the full smbclient UNC path for a file."""
    base = f"//{smb_cfg.server}/{smb_cfg.share}"
    if smb_cfg.directory:
        return f"{base}/{smb_cfg.directory.strip('/')}/{filename}"
    return f"{base}/{filename}"


def _register_smb_session(smb_cfg: SmbConfig) -> None:
    import smbclient
    kw: dict = {}
    if smb_cfg.username:
        kw["username"] = smb_cfg.username
    if smb_cfg.password:
        kw["password"] = smb_cfg.password
    if smb_cfg.domain:
        kw["auth_protocol"] = "ntlm"
    smbclient.register_session(smb_cfg.server, **kw)


def write_smb(playlist: PlexPlaylist, config: Config) -> str:
    import smbclient
    assert config.output.smb
    cfg = config.output.smb
    filename = _safe_filename(playlist.name) + f".{config.output.format}"
    unc_path = _smb_unc(cfg, filename)
    content = _build_m3u_content(playlist, config).encode(_encoding(config.output.format))

    _register_smb_session(cfg)

    # Create the subdirectory if needed
    if cfg.directory:
        dir_unc = f"//{cfg.server}/{cfg.share}/{cfg.directory.strip('/')}"
        try:
            smbclient.makedirs(dir_unc, exist_ok=True)
        except Exception:
            pass

    try:
        smbclient.stat(unc_path)
        if not config.output.overwrite:
            return unc_path
    except Exception:
        pass

    with smbclient.open_file(unc_path, mode="wb") as f:
        f.write(content)

    return unc_path


def test_smb_connection(smb_cfg: SmbConfig) -> Tuple[bool, str]:
    try:
        import smbclient
        _register_smb_session(smb_cfg)
        unc = f"//{smb_cfg.server}/{smb_cfg.share}"
        smbclient.listdir(unc)
        return True, "Connection successful."
    except Exception as exc:
        return False, str(exc)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def write_playlist(playlist: PlexPlaylist, config: Config):
    if config.output.smb:
        return write_smb(playlist, config)
    if config.output.sftp:
        return write_sftp(playlist, config)
    return write_local(playlist, config)


def write_all_playlists(playlists: List[PlexPlaylist], config: Config) -> list:
    return [write_playlist(pl, config) for pl in playlists]
