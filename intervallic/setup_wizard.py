"""Interactive setup wizard.

Guides the user through:
  1. Locating / authenticating with their Plex server and obtaining a token
  2. Choosing local-directory or SFTP output
  3. Configuring path mapping if Plex and Roon see different mount points
  4. Writing config.yaml

Run via:  intervallic setup
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import click
import yaml

from .config import SftpConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prompt(msg: str, default: str = "", hide: bool = False) -> str:
    val = click.prompt(msg, default=default or None, hide_input=hide, show_default=bool(default))
    return val.strip() if isinstance(val, str) else val


def _confirm(msg: str, default: bool = True) -> bool:
    return click.confirm(msg, default=default)


def _header(msg: str) -> None:
    click.echo(f"\n{'─' * 60}")
    click.echo(f"  {msg}")
    click.echo(f"{'─' * 60}")


# ── Step 1 — Plex ─────────────────────────────────────────────────────────────

def _get_plex_token_via_login() -> tuple[str, str]:
    """Authenticate with plex.tv and return (server_url, token)."""
    click.echo(
        "\nYou can sign in with your Plex account to automatically retrieve your\n"
        "token and discover your server address. Your credentials are sent\n"
        "directly to plex.tv and are not stored.\n"
    )
    username = _prompt("Plex username / email")
    password = _prompt("Plex password", hide=True)

    try:
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(username, password)
    except Exception as exc:
        click.echo(f"\n  Login failed: {exc}", err=True)
        sys.exit(1)

    token = account.authenticationToken
    click.echo(f"\n  Token obtained.")

    # Let user pick a server if they have more than one
    try:
        resources = [r for r in account.resources() if r.provides == "server"]
    except Exception:
        resources = []

    if not resources:
        click.echo("  No servers found on this account. Enter the URL manually.")
        url = _prompt("Plex server URL", default="http://localhost:32400")
        return url, token

    if len(resources) == 1:
        res = resources[0]
        # Try to get a reachable connection
        url = _best_connection(res)
        click.echo(f"  Found server: {res.name}  →  {url}")
        if not _confirm("Use this server?"):
            url = _prompt("Plex server URL", default=url)
        return url, token

    click.echo("\n  Your Plex servers:")
    for i, res in enumerate(resources):
        click.echo(f"    [{i + 1}]  {res.name}")
    idx = click.prompt(
        "  Choose a server",
        type=click.IntRange(1, len(resources)),
        default=1,
    ) - 1
    res = resources[idx]
    url = _best_connection(res)
    click.echo(f"  Using: {res.name}  →  {url}")
    if not _confirm("Correct?"):
        url = _prompt("Plex server URL", default=url)
    return url, token


def _best_connection(resource) -> str:
    """Return the first reachable connection URL, or the first listed."""
    for conn in resource.connections:
        if not conn.local:
            return conn.uri
    if resource.connections:
        return resource.connections[0].uri
    return "http://localhost:32400"


def _manual_plex() -> tuple[str, str]:
    click.echo(
        "\nTo find your token manually:\n"
        "  1. Open Plex Web in a browser\n"
        "  2. Play any item, then open the browser dev tools → Network\n"
        "  3. Look for a request containing  X-Plex-Token=<value>  in the URL\n"
        "  4. Copy that value\n"
        "  Alternatively, go to a media item → ⋮ → Get Info → View XML.\n"
        "  The token appears in the URL as  ?X-Plex-Token=...\n"
    )
    url = _prompt("Plex server URL", default="http://localhost:32400")
    token = _prompt("Plex token (X-Plex-Token)")
    return url, token


def _test_plex(url: str, token: str) -> bool:
    try:
        from plexapi.server import PlexServer
        PlexServer(url, token)
        return True
    except Exception as exc:
        click.echo(f"  Could not connect: {exc}", err=True)
        return False


def wizard_plex() -> dict:
    _header("Step 1 of 3 — Plex server")

    method = click.prompt(
        "\nHow would you like to authenticate?",
        type=click.Choice(["login", "manual"], case_sensitive=False),
        default="login",
        show_choices=True,
    )

    if method == "login":
        url, token = _get_plex_token_via_login()
    else:
        url, token = _manual_plex()

    click.echo("\nTesting connection…", nl=False)
    if _test_plex(url, token):
        click.echo("  OK")
    else:
        if not _confirm("Connection failed. Continue anyway?", default=False):
            sys.exit(1)

    return {"url": url, "token": token, "playlist_filter": [], "playlist_exclude": []}


# ── Step 2 — Output ───────────────────────────────────────────────────────────

def wizard_output() -> dict:
    _header("Step 2 of 3 — Where should playlists be delivered?")

    click.echo(
        "\nRoon imports playlists from M3U8 files placed in a folder it watches.\n"
        "Intervallic can write those files:\n"
        "  [1]  Local path  — same machine, or a network share already mounted here\n"
        "  [2]  SFTP        — push files directly to the Roon host over SSH\n"
    )
    choice = click.prompt("Choose", type=click.Choice(["1", "2"]), default="1")

    out: dict = {"format": "m3u8", "overwrite": True}

    if choice == "1":
        directory = _prompt(
            "Local path to Roon's watched playlist folder",
            default=os.path.expanduser("~/Music/Playlists"),
        )
        out["directory"] = directory
    else:
        out["sftp"] = _wizard_sftp()

    return out


def _wizard_sftp() -> dict:
    click.echo()
    host = _prompt("Roon host (hostname or IP)")
    port = click.prompt("SSH port", default=22, type=int)
    username = _prompt("SSH username", default=os.getenv("USER", ""))
    remote_dir = _prompt(
        "Remote path to Roon's watched playlist folder",
        default="/home/" + (os.getenv("USER", "roon") + "/Music/Playlists"),
    )

    click.echo(
        "\nAuthentication — choose one:\n"
        "  [1]  SSH private key (recommended)\n"
        "  [2]  Password\n"
    )
    auth = click.prompt("Choose", type=click.Choice(["1", "2"]), default="1")

    sftp: dict = {
        "host": host,
        "port": port,
        "username": username,
        "remote_directory": remote_dir,
    }

    if auth == "1":
        default_key = os.path.expanduser("~/.ssh/id_rsa")
        key_path = _prompt("Path to private key file", default=default_key)
        sftp["key_path"] = key_path
    else:
        password = _prompt("SSH password", hide=True)
        sftp["password"] = password

    # Test
    click.echo("\nTesting SFTP connection…", nl=False)
    from .output import test_sftp_connection
    ok, msg = test_sftp_connection(
        SftpConfig(
            host=host,
            port=port,
            username=username,
            remote_directory=remote_dir,
            password=sftp.get("password"),
            key_path=sftp.get("key_path"),
        )
    )
    if ok:
        click.echo("  OK")
    else:
        click.echo(f"  Failed: {msg}", err=True)
        if not _confirm("Continue anyway?", default=False):
            sys.exit(1)

    return sftp


# ── Step 3 — Path mapping ─────────────────────────────────────────────────────

def wizard_path_mapping() -> list:
    _header("Step 3 of 3 — Path mapping")

    click.echo(
        "\nPlex embeds the file path it knows into each track (e.g. /data/music/…).\n"
        "If Roon mounts the same library at a different path (e.g. /mnt/nas/music/…)\n"
        "you need to tell Intervallic how to translate those paths.\n"
        "\nIf both apps use the exact same paths, skip this step.\n"
    )

    if not _confirm("Do you need to remap paths?", default=False):
        return []

    mappings = []
    while True:
        from_prefix = _prompt("  Plex path prefix (e.g. /data/music)")
        to_prefix = _prompt("  Roon path prefix (e.g. /mnt/nas/music)")
        mappings.append({"from": from_prefix, "to": to_prefix})
        if not _confirm("  Add another mapping?", default=False):
            break

    return mappings


# ── Entry point ───────────────────────────────────────────────────────────────

def run_wizard(output_path: str) -> None:
    click.echo(
        "\nWelcome to Intervallic setup.\n"
        "This wizard will create your config file in 3 short steps.\n"
        "Press Ctrl-C at any time to cancel without writing anything."
    )

    plex = wizard_plex()
    output = wizard_output()
    path_mapping = wizard_path_mapping()

    cfg = {
        "plex": plex,
        "output": output,
    }
    if path_mapping:
        cfg["path_mapping"] = path_mapping

    click.echo(f"\nWriting config to {output_path}…")

    if os.path.exists(output_path) and not _confirm(
        f"  {output_path} already exists. Overwrite?", default=False
    ):
        click.echo("Cancelled. No files written.")
        sys.exit(0)

    with open(output_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    click.echo(
        f"\nDone! Run a test with:\n"
        f"  intervallic --config {output_path} --dry-run\n"
        f"\nThen sync for real with:\n"
        f"  intervallic --config {output_path}\n"
    )
