from __future__ import annotations

import click

from .config import Config
from .plex_client import PlexClient

_TICK  = click.style("✓", fg="green", bold=True)
_WARN  = click.style("⚠", fg="yellow", bold=True)
_ARROW = click.style("→", fg="cyan")


def run_sync(config: Config, dry_run: bool = False) -> None:
    client = PlexClient(url=config.plex.url, token=config.plex.token)

    click.echo(f"\n  {_ARROW}  Connecting to Plex…", nl=False)
    playlists = client.get_playlists(
        include=config.plex.playlist_filter,
        exclude=config.plex.playlist_exclude,
    )
    click.echo(f"\r  {_TICK}  Connected to Plex           ")

    if not playlists:
        click.echo(f"\n  {_WARN}  No matching audio playlists found.\n")
        return

    click.echo()
    for pl in playlists:
        name   = click.style(pl.name, bold=True)
        count  = click.style(str(len(pl.tracks)), fg="cyan")
        click.echo(f"       ♫  {name}  ({count} tracks)")

    if dry_run:
        click.echo(click.style("\n  Dry run — no files written.\n", fg="yellow"))
        return

    if config.output.sftp:
        destination = f"sftp://{config.output.sftp.host}{config.output.sftp.remote_directory}"
    elif config.output.smb:
        cfg = config.output.smb
        destination = f"//{cfg.server}/{cfg.share}/{cfg.directory or ''}".rstrip("/")
    else:
        destination = config.output.directory

    click.echo(f"\n  {_ARROW}  Writing to {click.style(destination, fg='cyan')}…\n")

    total   = len(playlists)
    written = []
    errors  = []

    for i, pl in enumerate(playlists, 1):
        label = f"[{i}/{total}]"
        name  = pl.name
        click.echo(f"       {click.style(label, dim=True)}  {name}…", nl=False)
        try:
            path = _write_one(pl, config)
            written.append(path)
            click.echo(f"\r       {click.style(label, dim=True)}  {name:<40}  {_TICK}")
        except Exception as exc:
            errors.append((name, exc))
            click.echo(
                f"\r       {click.style(label, dim=True)}  {name:<40}  "
                + click.style("✗", fg="red", bold=True)
            )
            click.echo(f"              {click.style(str(exc), fg='red')}")

    click.echo()
    if written:
        click.echo(
            f"  {_TICK}  {click.style(str(len(written)), bold=True)} "
            f"file(s) written successfully."
        )
    if errors:
        click.echo(
            f"  {click.style('✗', fg='red', bold=True)}  "
            f"{click.style(str(len(errors)), fg='red', bold=True)} "
            f"file(s) failed."
        )
    click.echo()


def _write_one(pl, config):
    from .output import write_playlist
    return write_playlist(pl, config)
