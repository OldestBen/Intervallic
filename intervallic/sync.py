from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .config import Config
from .plex_client import PlexClient, PlexPlaylist
from .roon_writer import write_all_playlists


def run_sync(config: Config, dry_run: bool = False) -> None:
    client = PlexClient(url=config.plex.url, token=config.plex.token)

    print("Fetching playlists from Plex…")
    playlists = client.get_playlists(
        include=config.plex.playlist_filter,
        exclude=config.plex.playlist_exclude,
    )

    if not playlists:
        print("No matching audio playlists found.")
        return

    print(f"Found {len(playlists)} playlist(s):")
    for pl in playlists:
        print(f"  • {pl.name!r} ({len(pl.tracks)} tracks)")

    if dry_run:
        print("\nDry run — no files written.")
        return

    written = write_all_playlists(playlists, config)

    print(f"\nWrote {len(written)} playlist file(s) to {config.output.directory}:")
    for path in written:
        print(f"  {path}")
