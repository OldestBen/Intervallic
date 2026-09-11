from __future__ import annotations

import sys

import click

from . import __version__
from .config import find_config, load_config
from .sync import run_sync

_BANNER = click.style("Intervallic", fg="cyan", bold=True)

_TICK = click.style("✓", fg="green", bold=True)
_CROSS = click.style("✗", fg="red", bold=True)
_WARN = click.style("⚠", fg="yellow", bold=True)


def _err(msg: str) -> None:
    click.echo(f"  {_CROSS}  {msg}", err=True)


def _load(config_path: str | None):
    """Find and load a config, failing with guidance rather than a traceback."""
    found = find_config(config_path)

    if found is None:
        _err("No config file found.")
        click.echo(
            f"\n  Run  {click.style('intervallic setup', bold=True)}  to create one "
            f"— it takes about a minute.\n",
            err=True,
        )
        sys.exit(1)

    if not found.is_file():
        _err(f"Config file not found: {found}")
        click.echo(
            f"\n  Run  {click.style('intervallic setup', bold=True)}  to create one.\n",
            err=True,
        )
        sys.exit(1)

    try:
        return load_config(str(found)), found
    except KeyError as exc:
        _err(f"Config is missing a required setting: {exc}")
        click.echo(f"       {click.style(str(found), dim=True)}", err=True)
        click.echo(
            f"\n  Run  {click.style('intervallic setup', bold=True)}  to regenerate it.\n",
            err=True,
        )
        sys.exit(1)
    except Exception as exc:
        _err(f"Could not read config: {exc}")
        click.echo(f"       {click.style(str(found), dim=True)}\n", err=True)
        sys.exit(1)


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="intervallic")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Intervallic — sync Plex playlists to Roon.

    \b
    Running `intervallic` on its own does the sensible thing: it walks you
    through setup the first time, and syncs every time after that.
    """
    if ctx.invoked_subcommand is not None:
        return

    # No subcommand given — pick the one the user almost certainly wants.
    if find_config(None) is None:
        click.echo(
            f"\n  {_BANNER}\n\n"
            "  No config found, so let's create one.\n"
        )
        ctx.invoke(setup)
    else:
        ctx.invoke(sync)


@main.command()
@click.option("--config", "-c", "config_path", default=None,
              help="Path to config file (default: search the usual locations).")
@click.option("--dry-run", is_flag=True, default=False,
              help="List playlists without writing any files.")
def sync(config_path: str, dry_run: bool) -> None:
    """Sync Plex playlists to Roon as M3U/M3U8 files."""
    click.echo(f"\n  {_BANNER}\n")
    config, path = _load(config_path)
    click.echo(click.style(f"  Using {path}", dim=True))
    run_sync(config, dry_run=dry_run)


@main.command()
@click.option("--config", "-c", "config_path", default=None,
              help="Path to config file (default: search the usual locations).")
def doctor(config_path: str) -> None:
    """Check everything end to end and explain anything that is broken.

    \b
    Verifies that Plex is reachable, the destination is writable, and — most
    importantly — that the paths written into your playlists are paths Roon
    will actually recognise.
    """
    from .doctor import run_diagnostics, OK, WARN, FAIL, SKIP

    click.echo(f"\n  {_BANNER}  doctor\n")
    config, path = _load(config_path)
    click.echo(click.style(f"  Using {path}\n", dim=True))

    report = run_diagnostics(config)

    marks = {
        OK:   _TICK,
        WARN: _WARN,
        FAIL: _CROSS,
        SKIP: click.style("–", dim=True),
    }

    for check in report.checks:
        click.echo(f"  {marks[check.status]}  {click.style(check.name, bold=True)}")
        click.echo(f"       {check.detail}")
        if check.fix:
            click.echo(click.style(f"       → {check.fix}", fg="cyan"))
        if check.snippet:
            click.echo()
            for line in check.snippet.splitlines():
                click.echo(click.style(f"         {line}", fg="yellow"))
        click.echo()

    click.echo("  " + click.style("─" * 58, dim=True))
    if report.healthy and not report.warned:
        click.echo(f"  {_TICK}  Everything looks good. Run "
                   f"{click.style('intervallic sync', bold=True)}.\n")
    elif report.healthy:
        click.echo(f"  {_TICK}  No blocking problems — see the warnings above.\n")
    else:
        click.echo(
            f"  {_CROSS}  {len(report.failed)} problem(s) need fixing before "
            f"sync will work.\n"
        )
        sys.exit(1)


@main.command()
@click.option("--output", "-o", "output_path", default=None,
              help="Where to write the config file (default: ~/.config/intervallic/config.yaml).")
def setup(output_path: str) -> None:
    """Interactive first-time setup wizard."""
    from .config import default_config_path
    from .setup_wizard import run_wizard

    if output_path is None:
        target = default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        output_path = str(target)

    run_wizard(output_path)


@main.command()
@click.option("--config", "-c", "config_path", default=None,
              help="Path to config file (default: search the usual locations).")
@click.option("--section", default=None, help="Music library section name (default: all music sections).")
@click.option("--output", "-o", default=None, help="Write full report to a CSV file.")
def audit(config_path: str, section: str, output: str) -> None:
    """Scan your Plex music library for incomplete albums and missing tracks."""
    click.echo(f"\n  {_BANNER}\n")
    config, _ = _load(config_path)

    from .audit import audit_library, write_csv

    click.echo(f"  {click.style('→', fg='cyan')}  Scanning Plex library…", nl=False)
    reports, warnings = audit_library(
        url=config.plex.url,
        token=config.plex.token,
        section_name=section,
    )
    click.echo(f"\r  {click.style('✓', fg='green', bold=True)}  Scan complete.         \n")

    for w in warnings:
        click.echo(f"  {click.style('⚠', fg='yellow', bold=True)}  {w}")

    if not reports:
        click.echo(f"  {click.style('✓', fg='green', bold=True)}  No issues found — all albums look complete.\n")
        return

    # Summary line
    total_albums  = len(reports)
    total_missing = sum(r.missing_count for r in reports)
    total_unnum   = sum(r.unnumbered_count for r in reports)

    click.echo(
        f"  Found {click.style(str(total_albums), bold=True, fg='yellow')} album(s) with issues"
        + (f"  ·  {click.style(str(total_missing), bold=True)} gap(s)" if total_missing else "")
        + (f"  ·  {click.style(str(total_unnum), bold=True)} unnumbered track(s)" if total_unnum else "")
        + "\n"
    )

    # Per-album detail
    for r in reports:
        artist_album = click.style(f"{r.artist} — {r.album}", bold=True)
        year_str     = click.style(f"({r.year})", dim=True) if r.year else ""
        click.echo(f"  {artist_album}  {year_str}")

        for issue in r.issues:
            num_str = f"#{issue.track_number:<3}" if issue.track_number is not None else "   "
            if issue.issue == "gap_before":
                tag   = click.style("MISSING", fg="red")
                title = click.style(issue.title, dim=True)
            elif issue.issue == "no_number":
                tag   = click.style("NO NUM ", fg="yellow")
                title = issue.title
            else:
                tag   = click.style("DUPE   ", fg="magenta")
                title = issue.title
            click.echo(f"       {tag}  {num_str}  {title}")

        click.echo()

    if output:
        write_csv(reports, output)
        click.echo(
            f"  {click.style('✓', fg='green', bold=True)}  "
            f"Full report written to {click.style(output, bold=True)}\n"
        )
    else:
        click.echo(
            click.style(
                f"  Tip: run with  -o report.csv  to export the full list.\n",
                dim=True,
            )
        )


@main.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--execute", is_flag=True, default=False,
              help="Actually move files. Without this, runs as a dry run.")
@click.option("--no-zips", is_flag=True, default=False,
              help="Only move folders, leave archives where they are.")
@click.option("--report", "-r", default=None, help="Write the full plan to a CSV file.")
def organize(path: str, execute: bool, no_zips: bool, report: str) -> None:
    """Sort "Album - Artist" downloads into per-artist folders.

    \b
    Infers the artist from the structure of the batch itself — repeated
    artists, self-titled albums and release markers all vote on where each
    split belongs. Dry run by default.
    """
    from pathlib import Path
    from .organize import build_plan, execute_plan, write_csv

    click.echo(f"\n  {_BANNER}\n")

    root = Path(path).resolve()
    click.echo(f"  {click.style('→', fg='cyan')}  Scanning {click.style(str(root), bold=True)}…")

    plan = build_plan(root, include_zips=not no_zips)

    if not plan.decisions:
        click.echo(f"\n  {click.style('⚠', fg='yellow', bold=True)}  Nothing to sort.\n")
        for name, reason in plan.skipped:
            click.echo(click.style(f"       {name}  —  {reason}", dim=True))
        click.echo()
        return

    grouped = plan.by_artist()
    click.echo(
        f"  {click.style('✓', fg='green', bold=True)}  "
        f"{click.style(str(len(plan.decisions)), bold=True)} item(s)  ·  "
        f"{click.style(str(len(grouped)), bold=True)} artist(s)\n"
    )

    _CONF_STYLE = {
        "certain": ("", "green"),
        "high":    ("", "green"),
        "medium":  ("?", "yellow"),
        "low":     ("!", "red"),
    }

    for artist in sorted(grouped, key=str.lower):
        decisions = grouped[artist]
        click.echo(f"  {click.style(artist, fg='yellow', bold=True)}")
        for d in decisions:
            mark, colour = _CONF_STYLE[d.confidence]
            flag = click.style(f" {mark}", fg=colour, bold=True) if mark else "  "
            kind = click.style("zip" if not d.item.is_dir else "dir", dim=True)
            click.echo(f"      {flag} {d.album}  {kind}")
        click.echo()

    review = plan.review_items
    if review:
        click.echo(f"  {click.style('Needs review', fg='yellow', bold=True)}")
        click.echo("  " + click.style("─" * 58, dim=True))
        for d in review:
            if d.normalised_from:
                click.echo(
                    f"      {click.style(d.artist, bold=True)}  "
                    + click.style(f"← \"{d.normalised_from}\"  (name cleaned)", dim=True)
                )
            else:
                click.echo(
                    f"      {click.style(d.item.path.name, bold=True)}\n"
                    f"          → artist {click.style(d.artist, fg='yellow')}  "
                    + click.style(f"({d.confidence}: {d.reason})", dim=True)
                )
        click.echo()

    if plan.skipped:
        click.echo(click.style("  Skipped", dim=True))
        for name, reason in plan.skipped:
            click.echo(click.style(f"      {name}  —  {reason}", dim=True))
        click.echo()

    if report:
        write_csv(plan, report)
        click.echo(
            f"  {click.style('✓', fg='green', bold=True)}  "
            f"Plan written to {click.style(report, bold=True)}\n"
        )

    if not execute:
        click.echo(
            f"  {click.style('Dry run', fg='cyan', bold=True)} — nothing was moved.\n"
            f"  Re-run with {click.style('--execute', bold=True)} when the list above looks right.\n"
        )
        return

    moved, failures = execute_plan(plan)
    click.echo(
        f"  {click.style('✓', fg='green', bold=True)}  "
        f"Moved {click.style(str(moved), bold=True)} item(s)."
    )
    for name, reason in failures:
        click.echo(f"  {click.style('✗', fg='red', bold=True)}  {name}  —  {reason}")
    click.echo()


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
