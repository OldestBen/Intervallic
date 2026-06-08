from __future__ import annotations

import click

from .config import Config
from .plex_client import PlexClient
from .output import write_all_playlists


def run_sync(config: Config, dry_run: bool = False) -> None:
    client = PlexClient(url=config.plex.url, token=config.plex.token)

    click.echo("Fetching playlists from Plex…")
    playlists = client.get_playlists(
        include=config.plex.playlist_filter,
        exclude=config.plex.playlist_exclude,
    )

    if not playlists:
        click.echo("No matching audio playlists found.")
        return

    click.echo(f"Found {len(playlists)} playlist(s):")
    for pl in playlists:
        click.echo(f"  • {pl.name!r} ({len(pl.tracks)} tracks)")

    if dry_run:
        click.echo("\nDry run — no files written.")
        return

    if config.output.sftp:
        destination = f"sftp://{config.output.sftp.host}{config.output.sftp.remote_directory}"
    elif config.output.smb:
        cfg = config.output.smb
        destination = f"//{cfg.server}/{cfg.share}/{cfg.directory or ''}".rstrip("/")
    else:
        destination = config.output.directory
    click.echo(f"\nWriting to {destination}…")

    written = write_all_playlists(playlists, config)

    click.echo(f"Wrote {len(written)} playlist file(s):")
    for path in written:
        click.echo(f"  {path}")
