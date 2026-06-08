from __future__ import annotations

import sys

import click

from . import __version__
from .config import load_config
from .sync import run_sync

_BANNER = click.style("Intervallic", fg="cyan", bold=True)


def _err(msg: str) -> None:
    click.echo(f"  {click.style('✗', fg='red', bold=True)}  {msg}", err=True)


@click.group()
@click.version_option(__version__, prog_name="intervallic")
def main() -> None:
    """Intervallic — sync Plex playlists to Roon."""


@main.command()
@click.option("--config", "-c", "config_path", default="config.yaml", show_default=True,
              help="Path to config file.")
@click.option("--dry-run", is_flag=True, default=False,
              help="List playlists without writing any files.")
def sync(config_path: str, dry_run: bool) -> None:
    """Sync Plex playlists to Roon as M3U/M3U8 files."""
    click.echo(f"\n  {_BANNER}\n")

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        _err(f"Config file not found: {config_path}")
        click.echo(
            f"       Run  {click.style('intervallic setup', bold=True)}  to create one.",
            err=True,
        )
        sys.exit(1)
    except Exception as exc:
        _err(f"Failed to load config: {exc}")
        sys.exit(1)

    run_sync(config, dry_run=dry_run)


@main.command()
@click.option("--output", "-o", "output_path", default="config.yaml", show_default=True,
              help="Where to write the generated config file.")
def setup(output_path: str) -> None:
    """Interactive first-time setup wizard."""
    from .setup_wizard import run_wizard
    run_wizard(output_path)


@main.command()
@click.argument("host")
@click.option("--port", default=22, show_default=True, help="SSH port.")
@click.option("--username", "-u", default="root", show_default=True, help="SSH username.")
@click.option("--password", "-p", default=None, help="SSH password (omit to use key auth).")
@click.option("--key", "-i", default=None, help="Path to SSH private key.")
def diagnose(host: str, port: int, username: str, password: str, key: str) -> None:
    """SSH into a Roon host and report mounts, audio dirs, and playlist paths.

    \b
    Examples:
      intervallic diagnose 192.168.1.50 -u root -p mypassword
      intervallic diagnose 192.168.1.50 -i ~/.ssh/id_rsa
    """
    from .roon_discovery import find_remote_playlist_paths, ScanDiagnostics

    click.echo(f"\n  {_BANNER}\n")
    click.echo(f"  Connecting to {click.style(f'{username}@{host}:{port}', bold=True)} …")

    diag       = ScanDiagnostics()
    candidates = find_remote_playlist_paths(
        host=host, port=port, username=username,
        password=password, key_path=key,
        diag=diag,
    )

    click.echo(f"\n{click.style('  Diagnostics', bold=True)}")
    click.echo("  " + "─" * 58)
    click.echo(diag.report())

    if candidates:
        click.echo(f"\n{click.style('  Suggested playlist paths', bold=True)} (ranked best first)")
        click.echo("  " + "─" * 58)
        for i, c in enumerate(candidates):
            click.echo(f"    [{i + 1}]  {c}")
    else:
        click.echo(f"\n  {click.style('⚠', fg='yellow', bold=True)}  No candidates found.")

    if diag.commands_run:
        click.echo(f"\n{click.style('  Commands run on remote host', bold=True)}")
        click.echo("  " + "─" * 58)
        for cmd, output in diag.commands_run:
            click.echo(f"\n  $ {click.style(cmd, fg='cyan')}")
            for line in output[:20]:
                click.echo(f"    {line}")
            if len(output) > 20:
                click.echo(click.style(f"    … ({len(output) - 20} more lines)", dim=True))

    click.echo()
