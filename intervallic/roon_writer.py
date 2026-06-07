from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

from .plex_client import PlexPlaylist, PlexTrack
from .config import Config


def _safe_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _resolve_track_path(track: PlexTrack, config: Config) -> str:
    return config.remap_path(track.file_path)


def write_playlist(playlist: PlexPlaylist, config: Config) -> Path:
    ext = f".{config.output.format}"
    filename = _safe_filename(playlist.name) + ext
    out_dir = Path(config.output.directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / filename

    if dest.exists() and not config.output.overwrite:
        return dest

    lines: List[str] = ["#EXTM3U", f"#PLAYLIST:{playlist.name}", ""]

    for track in playlist.tracks:
        duration = -1  # unknown; Roon will read from file
        artist_title = f"{track.artist} - {track.title}" if track.artist else track.title
        lines.append(f"#EXTINF:{duration},{artist_title}")
        lines.append(_resolve_track_path(track, config))

    content = "\n".join(lines) + "\n"

    encoding = "utf-8" if config.output.format == "m3u8" else "latin-1"
    dest.write_text(content, encoding=encoding)
    return dest


def write_all_playlists(playlists: List[PlexPlaylist], config: Config) -> List[Path]:
    return [write_playlist(pl, config) for pl in playlists]
