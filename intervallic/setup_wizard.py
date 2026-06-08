"""Interactive setup wizard.

Guides the user through:
  1. Authenticating with Plex via browser OAuth (no password in terminal)
  2. Choosing local-directory or SFTP output
  3. Configuring path mapping if Plex and Roon see different mount points
  4. Writing config.yaml

Verified against plexapi source:
  - MyPlexPinLogin must be initialised with oauth=True
  - OAuth URL method is oauthUrl(), not authUrl()
  - checkLogin() works when run() has NOT been called (manual poll loop)
  - MyPlexResource.provides is a string; filter with "server" in provides
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import urlencode

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


def _local_ip() -> str:
    """Best-effort LAN IP — what other devices on the network reach us by."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _print_qr(url: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass  # qrcode not available or terminal too narrow — URL already shown


# ── Plex OAuth ────────────────────────────────────────────────────────────────

def _new_pin_login():
    """Return a MyPlexPinLogin configured for OAuth. Exits on failure."""
    from plexapi.myplex import MyPlexPinLogin
    try:
        return MyPlexPinLogin(oauth=True)
    except Exception as exc:
        click.echo(f"  Could not reach plex.tv: {exc}", err=True)
        sys.exit(1)


def _poll_for_token(pin_login, timeout: int = 300) -> str:
    """
    Poll plex.tv until the user completes OAuth sign-in.

    checkLogin() returns True once the user has signed in, and sets
    pin_login.token. We must NOT call run() before this — checkLogin()
    only polls directly when no background thread is running.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pin_login.checkLogin():
                return pin_login.token
        except Exception:
            pass
        time.sleep(2)
        click.echo(".", nl=False)

    click.echo()
    click.echo("  Timed out waiting for authentication.", err=True)
    sys.exit(1)


# ── Option 1: local callback server ──────────────────────────────────────────

_HTML_REDIRECT = """\
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={url}">
<title>Redirecting…</title></head>
<body><p>Redirecting to Plex…</p></body></html>"""

_HTML_OK = """\
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Authenticated</title>
<style>body{{font-family:sans-serif;text-align:center;margin-top:4rem}}
h1{{color:#e5a00d}}</style></head>
<body><h1>&#10003; Signed in</h1><p>You can close this tab.</p></body></html>"""

_HTML_ERR = """\
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Error</title></head>
<body><h1>Authentication failed</h1><p>{msg}</p></body></html>"""


def _run_callback_server(
    port: int,
    pin_login,
    result: dict,
    stop: threading.Event,
) -> None:
    local_ip = _local_ip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: str) -> None:
            encoded = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path in ("/", ""):
                # Build the Plex OAuth URL with our /callback as the forwardUrl
                oauth_url = pin_login.oauthUrl(
                    forwardUrl=f"http://{local_ip}:{port}/callback"
                )
                self._send(200, _HTML_REDIRECT.format(url=oauth_url))

            elif self.path.startswith("/callback"):
                # Plex redirected back here after sign-in — poll for the token
                token = None
                for _ in range(20):
                    try:
                        if pin_login.checkLogin():
                            token = pin_login.token
                            break
                    except Exception:
                        pass
                    time.sleep(1)

                if token:
                    result["token"] = token
                    self._send(200, _HTML_OK)
                    stop.set()
                else:
                    self._send(500, _HTML_ERR.format(msg="Could not retrieve token — please try again."))

            else:
                self.send_response(404)
                self.end_headers()

    srv = HTTPServer(("0.0.0.0", port), Handler)
    srv.timeout = 1
    while not stop.is_set():
        srv.handle_request()
    srv.server_close()


def _oauth_local_server(port: int) -> str:
    """
    Spin up a tiny HTTP server. User visits http://LXC-IP:PORT from any browser.
    Server redirects them through Plex OAuth and captures the token automatically.
    """
    pin_login = _new_pin_login()
    result: dict = {}
    stop = threading.Event()

    t = threading.Thread(
        target=_run_callback_server,
        args=(port, pin_login, result, stop),
        daemon=True,
    )
    t.start()

    local_url = f"http://{_local_ip()}:{port}"
    click.echo(f"\n  Open this URL from any browser on your network:\n")
    click.echo(f"      {local_url}\n")
    _print_qr(local_url)
    click.echo("\n  Waiting for sign-in", nl=False)

    stop.wait(timeout=300)
    click.echo()

    if not result.get("token"):
        click.echo("  Timed out or authentication failed.", err=True)
        sys.exit(1)

    click.echo("  Signed in successfully.")
    return result["token"]


# ── Option 2: direct plex.tv link + polling ───────────────────────────────────

def _oauth_direct_link() -> str:
    """Show the plex.tv OAuth URL (and QR code). Poll until sign-in completes."""
    pin_login = _new_pin_login()
    auth_url = pin_login.oauthUrl()

    click.echo(f"\n  Open this URL in any browser and sign in:\n")
    click.echo(f"      {auth_url}\n")
    _print_qr(auth_url)
    webbrowser.open(auth_url)   # no-op on headless, fine to attempt

    click.echo("\n  Waiting for sign-in", nl=False)
    token = _poll_for_token(pin_login)
    click.echo()
    click.echo("  Signed in successfully.")
    return token


# ── Option 3: paste token manually ───────────────────────────────────────────

def _manual_token() -> tuple[str, str]:
    click.echo(
        "\n  To find your Plex token:\n"
        "  1. Open Plex Web and sign in\n"
        "  2. Browse to any item → ⋮ menu → Get Info → View XML\n"
        "  3. Copy the value of  X-Plex-Token=  from the URL bar\n"
    )
    url   = _prompt("Plex server URL", default="http://localhost:32400")
    token = _prompt("Plex token (X-Plex-Token)")
    return url, token


# ── Server picker (shared by options 1 + 2) ───────────────────────────────────

def _pick_server(token: str) -> str:
    """Fetch server list from plex.tv and let user choose. Returns URL."""
    try:
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        # provides is a string e.g. "server" or "server,sync-target"
        servers = [r for r in account.resources() if "server" in r.provides]
    except Exception as exc:
        click.echo(f"  Could not fetch server list: {exc}", err=True)
        return _prompt("Plex server URL", default="http://localhost:32400")

    if not servers:
        click.echo("  No servers found on this account.")
        return _prompt("Plex server URL", default="http://localhost:32400")

    if len(servers) == 1:
        url = _best_connection(servers[0])
        click.echo(f"  Server: {servers[0].name}  →  {url}")
        if not _confirm("Use this server?"):
            url = _prompt("Plex server URL", default=url)
        return url

    click.echo("\n  Your Plex servers:")
    for i, s in enumerate(servers):
        click.echo(f"    [{i + 1}]  {s.name}")
    idx = click.prompt("  Choose", type=click.IntRange(1, len(servers)), default=1) - 1
    url = _best_connection(servers[idx])
    click.echo(f"  Using: {servers[idx].name}  →  {url}")
    if not _confirm("Correct?"):
        url = _prompt("Plex server URL", default=url)
    return url


def _best_connection(resource) -> str:
    """Prefer non-local (externally reachable) connections, then first listed."""
    for conn in resource.connections:
        if not conn.local:
            return conn.uri
    if resource.connections:
        return resource.connections[0].uri
    return "http://localhost:32400"


def _test_plex(url: str, token: str) -> bool:
    try:
        from plexapi.server import PlexServer
        PlexServer(url, token)
        return True
    except Exception as exc:
        click.echo(f"  Connection test failed: {exc}", err=True)
        return False


# ── Wizard step 1 ─────────────────────────────────────────────────────────────

def wizard_plex() -> dict:
    _header("Step 1 of 3 — Plex authentication")

    click.echo(
        "\n  [1]  Local auth server  (recommended for headless)\n"
        "       Starts a web server on this machine; visit it from any\n"
        "       browser or phone on your network to sign in.\n"
        "\n"
        "  [2]  Direct plex.tv link\n"
        "       Open the printed URL (or scan the QR code) in any browser.\n"
        "\n"
        "  [3]  Paste token manually\n"
        "       Copy your X-Plex-Token from Plex Web.\n"
    )
    choice = click.prompt("Choose", type=click.Choice(["1", "2", "3"]), default="1")

    if choice == "1":
        port = click.prompt("  Port for auth server", default=9876, type=int)
        try:
            probe = socket.socket()
            probe.bind(("0.0.0.0", port))
            probe.close()
        except OSError:
            click.echo(f"  Port {port} is already in use — switching to option 2.")
            choice = "2"

    if choice == "1":
        token = _oauth_local_server(port)
        url = _pick_server(token)
    elif choice == "2":
        token = _oauth_direct_link()
        url = _pick_server(token)
    else:
        url, token = _manual_token()

    click.echo("\nTesting connection … ", nl=False)
    if _test_plex(url, token):
        click.echo("OK")
    else:
        if not _confirm("Continue anyway?", default=False):
            sys.exit(1)

    return {"url": url, "token": token, "playlist_filter": [], "playlist_exclude": []}


# ── Wizard step 2: output ─────────────────────────────────────────────────────

def wizard_output() -> dict:
    _header("Step 2 of 3 — Roon playlist destination")

    click.echo(
        "\nRoon imports playlists from M3U8 files placed in a folder it watches.\n"
        "Intervallic only writes files there — it never modifies Roon's database\n"
        "or deletes anything.\n"
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

    # Try auto-discovery first, but don't block on it
    click.echo("  Searching for Roon Core on the network … ", nl=False)
    from .roon_discovery import discover_roon_core
    core = discover_roon_core(timeout=6)
    if core:
        click.echo(f"found at {core[0]}")
    else:
        click.echo("not found (cross-VLAN or timed out)")

    host     = _prompt("Roon host (hostname or IP)", default=core[0] if core else "")
    ssh_port = click.prompt("SSH port", default=22, type=int)
    username = _prompt("SSH username", default=os.getenv("USER", ""))

    click.echo("\n  [1]  SSH key file  (recommended)\n  [2]  Password\n")
    auth = click.prompt("Auth method", type=click.Choice(["1", "2"]), default="1")

    sftp: dict = {"host": host, "port": ssh_port, "username": username}

    if auth == "1":
        sftp["key_path"] = _prompt(
            "Path to private key", default=os.path.expanduser("~/.ssh/id_rsa")
        )
    else:
        sftp["password"] = click.prompt("SSH password", hide_input=True)

    # SSH scan for candidate paths — works regardless of how the host was found
    click.echo("\n  Scanning Roon host for SMB mounts and music library paths … ", nl=False)
    from .roon_discovery import find_remote_playlist_paths
    candidates = find_remote_playlist_paths(
        host=host, port=ssh_port, username=username,
        password=sftp.get("password"), key_path=sftp.get("key_path"),
    )

    if candidates:
        click.echo(f"done.\n")
        click.echo(
            "  Roon imports M3U8 files from inside its watched music folders.\n"
            "  Paths from SMB/CIFS mounts are listed first — those are most likely correct.\n"
        )
        shown = candidates[:6]
        for i, p in enumerate(shown):
            click.echo(f"    [{i + 1}]  {p}")
        click.echo(f"    [{len(shown) + 1}]  Enter manually")
        idx = click.prompt("  Choose", type=click.IntRange(1, len(shown) + 1), default=1)
        remote_dir = shown[idx - 1] if idx <= len(shown) else _prompt("Remote path")
    else:
        click.echo("could not scan (will enter manually).")
        click.echo(
            "\n  Note: Roon imports M3U8 files from inside its watched music folders.\n"
            "  For SMB/NAS setups, that is the mount point on the Roon host\n"
            "  (e.g. /mnt/music/Playlists), NOT a local folder on this machine.\n"
        )
        remote_dir = _prompt("Remote path to place M3U8 files")

    sftp["remote_directory"] = remote_dir

    click.echo("\nTesting SFTP connection … ", nl=False)
    from .output import test_sftp_connection
    ok, msg = test_sftp_connection(SftpConfig(
        host=host, port=ssh_port, username=username,
        remote_directory=remote_dir,
        password=sftp.get("password"), key_path=sftp.get("key_path"),
    ))
    if ok:
        click.echo("OK")
    else:
        click.echo(f"Failed: {msg}", err=True)
        if not _confirm("Continue anyway?", default=False):
            sys.exit(1)

    return sftp


# ── Wizard step 3: path mapping ───────────────────────────────────────────────

def wizard_path_mapping() -> list:
    _header("Step 3 of 3 — Path mapping")

    click.echo(
        "\nPlex stores the path it knows for each track (e.g. /data/music/…).\n"
        "If Roon mounts the same library at a different path (e.g. /mnt/nas/music/…)\n"
        "those paths need to be translated.\n"
        "\nSkip if both apps use identical paths.\n"
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

    plex         = wizard_plex()
    output       = wizard_output()
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
        f"\n  Test:   intervallic sync --dry-run\n"
        f"  Sync:   intervallic sync\n"
    )
