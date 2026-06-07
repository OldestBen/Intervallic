from __future__ import annotations

import sys

import click

from .config import load_config
from .sync import run_sync


@click.group()
def main() -> None:
    """Intervallic — sync Plex playlists to Roon."""


@main.command()
@click.option(
    "--config", "-c", "config_path",
    default="config.yaml", show_default=True,
    help="Path to YAML config file.",
)
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Fetch playlists and report without writing any files.",
)
def sync(config_path: str, dry_run: bool) -> None:
    """Sync Plex playlists to Roon as M3U8 files."""
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        click.echo(f"Config file not found: {config_path}", err=True)
        click.echo("Run  intervallic setup  to create one.", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Failed to load config: {exc}", err=True)
        sys.exit(1)

    run_sync(config, dry_run=dry_run)


@main.command()
@click.option(
    "--output", "-o", "output_path",
    default="config.yaml", show_default=True,
    help="Where to write the generated config file.",
)
def setup(output_path: str) -> None:
    """Interactive setup wizard — configure Plex auth, output destination, and path mapping."""
    from .setup_wizard import run_wizard
    run_wizard(output_path)
