from __future__ import annotations

import sys

import click

from .config import load_config
from .sync import run_sync


@click.group()
def main() -> None:
    """Intervallic — sync Plex playlists to Roon."""


@main.command()
@click.option("--config", "-c", "config_path", default="config.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, default=False,
              help="Report without writing any files.")
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
@click.option("--output", "-o", "output_path", default="config.yaml", show_default=True)
def setup(output_path: str) -> None:
    """Interactive setup wizard."""
    from .setup_wizard import run_wizard
    run_wizard(output_path)


@main.command()
@click.argument("host")
@click.option("--port", default=22, show_default=True)
@click.option("--username", "-u", default="root", show_default=True)
@click.option("--password", "-p", default=None, help="SSH password (omit to use key auth)")
@click.option("--key", "-i", default=None, help="Path to SSH private key")
def diagnose(host: str, port: int, username: str, password: str, key: str) -> None:
    """
    SSH into a Roon host and report everything found:
    mounts, audio directories, existing playlists, and the raw /proc/mounts.

    Example:
      intervallic diagnose 10.1.1.41 -u root -p mypassword
      intervallic diagnose 10.1.1.41 -i ~/.ssh/id_rsa
    """
    from .roon_discovery import find_remote_playlist_paths, ScanDiagnostics
    diag = ScanDiagnostics()

    click.echo(f"Connecting to {username}@{host}:{port} …")
    candidates = find_remote_playlist_paths(
        host=host, port=port, username=username,
        password=password, key_path=key,
        diag=diag,
    )

    click.echo("\n── Diagnostics ──────────────────────────────────────────────")
    click.echo(diag.report())

    if candidates:
        click.echo("\n── Suggested playlist paths (ranked) ────────────────────────")
        for i, c in enumerate(candidates):
            click.echo(f"  [{i + 1}]  {c}")
    else:
        click.echo("\nNo candidates found.")

    if diag.commands_run:
        click.echo("\n── Commands run on remote host ──────────────────────────────")
        for cmd, output in diag.commands_run:
            click.echo(f"\n  $ {cmd}")
            for line in output[:20]:
                click.echo(f"    {line}")
            if len(output) > 20:
                click.echo(f"    … ({len(output) - 20} more lines)")
