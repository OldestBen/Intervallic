"""Interactive setup wizard.

Guides the user through:
  1. Authenticating with Plex via browser OAuth (no password in terminal)
  2. Choosing local-directory or SFTP output
  3. Configuring path mapping if Plex and Roon see different mount points
  4. Writing config.yaml
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from typing import Optional

import click
import yaml

from .config import SftpConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prompt(msg: str, default: str = "") -> str:
    val = click.prompt(msg, default=default or None, show_default=bool(default))
    return val.strip() if isinstance(val, str) else val


def _confirm(msg: str, default: bool = True) -> bool:
    return click.confirm(msg, default=default)


def _header(msg: str) -> None:
    click.echo(f"\n{'─' * 60}")
    click.echo(f"  {msg}")
    click.echo(f"{'─' * 60}")


# ── Step 1 — Plex OAuth ───────────────────────────────────────────────────────

def _print_qr(url: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass  # qrcode unavailable or terminal too narrow — URL is still shown


def _oauth_login() -> tuple[str, str]:
    """
    Authenticate via Plex PIN OAuth — no password ever touches the terminal.

    Works headless: displays the auth URL as a QR code so you can scan it
    with a phone. The LXC polls plex.tv until sign-in completes.
    """
    from plexapi.myplex import MyPlexPinLogin

    try:
        pin_login = MyPlexPinLogin()
    except Exception as exc:
        click.echo(f"  Could not reach plex.tv: {exc}", err=True)
        sys.exit(1)

    auth_url = pin_login.authUrl()

    click.echo("\n  Scan the QR code with your phone, or open the URL in any browser:\n")
    _print_qr(auth_url)
    click.echo(f"\n  {auth_url}\n")

    # Try to open a local browser too — silently ignore failures (headless is fine)
    webbrowser.open(auth_url)

    click.echo("Waiting for you to complete sign-in", nl=False)
    poll_interval = 2   # seconds
    timeout = 300       # 5 minutes
    elapsed = 0
    token: Optional[str] = None

    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval
        click.echo(".", nl=False)

        try:
            pin_login.checkLogin()
            token = pin_login.token
            if token:
                break
        except Exception:
            pass  # not done yet

    click.echo()  # newline after dots

    if not token:
        click.echo("\n  Timed out waiting for authentication.", err=True)
        sys.exit(1)

    click.echo("  Authenticated successfully.")

    try:
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        resources = [r for r in account.resources() if "server" in r.provides]
    except Exception as exc:
        click.echo(f"  Could not fetch server list: {exc}", err=True)
        url = _prompt("Plex server URL", default="http://localhost:32400")
        return url, token

    if not resources:
        click.echo("  No Plex servers found on this account.")
        url = _prompt("Plex server URL", default="http://localhost:32400")
        return url, token

    if len(resources) == 1:
        url = _best_connection(resources[0])
        click.echo(f"  Server: {resources[0].name}  →  {url}")
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
    url = _best_connection(resources[idx])
    click.echo(f"  Using: {resources[idx].name}  →  {url}")
    if not _confirm("Correct?"):
        url = _prompt("Plex server URL", default=url)
    return url, token


def _best_connection(resource) -> str:
    # Prefer non-local (externally reachable) connections first,
    # fall back to first available, then localhost.
    for conn in resource.connections:
        if not conn.local:
            return conn.uri
    if resource.connections:
        return resource.connections[0].uri
    return "http://localhost:32400"


def _manual_token() -> tuple[str, str]:
    click.echo(
        "\nTo find your Plex token manually:\n"
        "  1. Open Plex Web in a browser and sign in\n"
        "  2. Browse to any media item → ⋮ menu → Get Info → View XML\n"
        "  3. The token appears in the URL as  ?X-Plex-Token=XXXXXXXXXXXX\n"
        "  4. Copy that value below\n"
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
        click.echo(f"  Connection test failed: {exc}", err=True)
        return False


def wizard_plex() -> dict:
    _header("Step 1 of 3 — Plex authentication")

    click.echo(
        "\n  [1]  Sign in via browser  (recommended — no password in terminal)\n"
        "  [2]  Paste token manually  (if running headless with no browser)\n"
    )
    choice = click.prompt("Choose", type=click.Choice(["1", "2"]), default="1")

    if choice == "1":
        url, token = _oauth_login()
    else:
        url, token = _manual_token()

    click.echo("\nTesting connection … ", nl=False)
    if _test_plex(url, token):
        click.echo("OK")
    else:
        if not _confirm("Continue anyway?", default=False):
            sys.exit(1)

    return {"url": url, "token": token, "playlist_filter": [], "playlist_exclude": []}


# ── Step 2 — Output ───────────────────────────────────────────────────────────

def wizard_output() -> dict:
    _header("Step 2 of 3 — Playlist destination")

    click.echo(
        "\nRoon imports playlists from M3U8 files placed in a folder it watches.\n"
        "\n"
        "  [1]  Local path  — same machine, or a share already mounted here\n"
        "  [2]  SFTP        — push files directly to the Roon host over SSH\n"
    )
    choice = click.prompt("Choose", type=click.Choice(["1", "2"]), default="1")

    out: dict = {"format": "m3u8", "overwrite": True}

    if choice == "1":
        out["directory"] = _prompt(
            "Path to Roon's watched playlist folder",
            default=os.path.expanduser("~/Music/Playlists"),
        )
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
        default=f"/home/{os.getenv('USER', 'roon')}/Music/Playlists",
    )

    click.echo(
        "\n  [1]  SSH key file  (recommended)\n"
        "  [2]  Password\n"
    )
    auth = click.prompt("Auth method", type=click.Choice(["1", "2"]), default="1")

    sftp: dict = {"host": host, "port": port, "username": username, "remote_directory": remote_dir}

    if auth == "1":
        sftp["key_path"] = _prompt("Path to private key", default=os.path.expanduser("~/.ssh/id_rsa"))
    else:
        sftp["password"] = click.prompt("SSH password", hide_input=True)

    click.echo("\nTesting SFTP connection … ", nl=False)
    from .output import test_sftp_connection
    ok, msg = test_sftp_connection(SftpConfig(
        host=host, port=port, username=username, remote_directory=remote_dir,
        password=sftp.get("password"), key_path=sftp.get("key_path"),
    ))
    if ok:
        click.echo("OK")
    else:
        click.echo(f"Failed: {msg}", err=True)
        if not _confirm("Continue anyway?", default=False):
            sys.exit(1)

    return sftp


# ── Step 3 — Path mapping ─────────────────────────────────────────────────────

def wizard_path_mapping() -> list:
    _header("Step 3 of 3 — Path mapping")

    click.echo(
        "\nPlex stores the file path it knows for each track (e.g. /data/music/…).\n"
        "If Roon mounts the same library at a different path (e.g. /mnt/nas/music/…)\n"
        "those paths need to be translated.\n"
        "\nSkip this if both Plex and Roon use identical paths.\n"
    )

    if not _confirm("Set up path remapping?", default=False):
        return []

    mappings = []
    while True:
        from_prefix = _prompt("  Plex path prefix (e.g. /data/music)")
        to_prefix   = _prompt("  Roon path prefix (e.g. /mnt/nas/music)")
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

    plex        = wizard_plex()
    output      = wizard_output()
    path_mapping = wizard_path_mapping()

    cfg: dict = {"plex": plex, "output": output}
    if path_mapping:
        cfg["path_mapping"] = path_mapping

    if os.path.exists(output_path) and not _confirm(
        f"\n{output_path} already exists. Overwrite?", default=False
    ):
        click.echo("Cancelled — no files written.")
        sys.exit(0)

    with open(output_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    click.echo(
        f"\nConfig written to {output_path}\n"
        f"\nTest it:       intervallic sync --dry-run\n"
        f"Run a sync:    intervallic sync\n"
    )
