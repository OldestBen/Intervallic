"""Interactive setup wizard.

Guides the user through:
  1. Authenticating with Plex via browser OAuth (no password in terminal)
  2. Choosing local-directory or SFTP output
  3. Configuring path mapping if Plex and Roon see different mount points
  4. Writing config.yaml
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
from urllib.parse import parse_qs, urlparse

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
    """Best-guess LAN IP — what other devices on the network would use to reach us."""
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
        pass


# ── Plex OAuth via local callback server ──────────────────────────────────────

_CALLBACK_HTML_OK = """\
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Intervallic — authenticated</title>
<style>body{{font-family:sans-serif;text-align:center;margin-top:4rem;color:#222}}
h1{{color:#e5a00d}}p{{font-size:1.1rem}}</style></head><body>
<h1>&#10003; Authenticated</h1>
<p>You can close this tab and return to the terminal.</p>
</body></html>"""

_CALLBACK_HTML_ERR = """\
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Intervallic — error</title></head><body>
<h1>Something went wrong</h1><p>{msg}</p>
</body></html>"""


def _run_callback_server(port: int, pin_login, result: dict, stop_event: threading.Event) -> None:
    """Tiny HTTP server that handles the Plex OAuth redirect and polls for the token."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silence access log

        def do_GET(self):
            parsed = urlparse(self.path)

            # Landing page — user visits http://LXC-IP:PORT/
            if parsed.path in ("/", ""):
                auth_url = pin_login.authUrl(
                    forwardUrl=f"http://{_local_ip()}:{port}/callback"
                )
                self.send_response(302)
                self.send_header("Location", auth_url)
                self.end_headers()
                return

            # Plex redirects here after the user signs in
            if parsed.path == "/callback":
                # Poll for up to 15 s — the redirect arrives almost immediately
                token = None
                for _ in range(15):
                    try:
                        pin_login.checkLogin()
                        token = pin_login.token
                        if token:
                            break
                    except Exception:
                        pass
                    time.sleep(1)

                if token:
                    result["token"] = token
                    body = _CALLBACK_HTML_OK.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    stop_event.set()
                else:
                    msg = "Could not retrieve token. Please try again."
                    body = _CALLBACK_HTML_ERR.format(msg=msg).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                return

            self.send_response(404)
            self.end_headers()

    server = HTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 1  # check stop_event every second
    while not stop_event.is_set():
        server.handle_request()
    server.server_close()


def _oauth_via_local_server(port: int = 9876) -> tuple[str, str]:
    """
    Spin up a local HTTP server on port 9876.
    User visits http://LXC-IP:9876 from any browser on the network.
    Server redirects them through Plex OAuth and captures the token.
    No QR code, no copy-paste — just one URL.
    """
    from plexapi.myplex import MyPlexPinLogin

    try:
        pin_login = MyPlexPinLogin()
    except Exception as exc:
        click.echo(f"  Could not reach plex.tv: {exc}", err=True)
        sys.exit(1)

    result: dict = {}
    stop_event = threading.Event()

    server_thread = threading.Thread(
        target=_run_callback_server,
        args=(port, pin_login, result, stop_event),
        daemon=True,
    )
    server_thread.start()

    local_url = f"http://{_local_ip()}:{port}"
    click.echo(f"\n  Open this URL in any browser on your network:\n")
    click.echo(f"      {local_url}\n")
    _print_qr(local_url)
    click.echo("\n  Waiting for authentication", nl=False)

    timeout = 300  # 5 minutes
    elapsed = 0
    while not stop_event.is_set() and elapsed < timeout:
        time.sleep(2)
        elapsed += 2
        click.echo(".", nl=False)

    click.echo()

    if not result.get("token"):
        click.echo("  Timed out or authentication failed.", err=True)
        sys.exit(1)

    token = result["token"]
    click.echo("  Authenticated successfully.")
    return token


def _oauth_polling(port: int = 9876) -> tuple[str, str]:
    """
    Fallback: display plex.tv auth URL + QR code and poll for completion.
    Used when the local callback server can't bind (port in use, etc.).
    """
    from plexapi.myplex import MyPlexPinLogin

    try:
        pin_login = MyPlexPinLogin()
    except Exception as exc:
        click.echo(f"  Could not reach plex.tv: {exc}", err=True)
        sys.exit(1)

    auth_url = pin_login.authUrl()
    click.echo(f"\n  Open this URL in any browser and sign in:\n\n      {auth_url}\n")
    _print_qr(auth_url)
    click.echo("\n  Waiting for authentication", nl=False)

    timeout, elapsed = 300, 0
    token = None
    while elapsed < timeout:
        time.sleep(2)
        elapsed += 2
        click.echo(".", nl=False)
        try:
            pin_login.checkLogin()
            token = pin_login.token
            if token:
                break
        except Exception:
            pass

    click.echo()
    if not token:
        click.echo("  Timed out.", err=True)
        sys.exit(1)

    click.echo("  Authenticated successfully.")
    return token


def _resolve_servers(token: str) -> str:
    """Fetch server list and let user pick one. Returns URL."""
    try:
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        resources = [r for r in account.resources() if "server" in r.provides]
    except Exception:
        resources = []

    if not resources:
        click.echo("  No servers found on this account.")
        return _prompt("Plex server URL", default="http://localhost:32400")

    if len(resources) == 1:
        url = _best_connection(resources[0])
        click.echo(f"  Server: {resources[0].name}  →  {url}")
        if not _confirm("Use this server?"):
            url = _prompt("Plex server URL", default=url)
        return url

    click.echo("\n  Your Plex servers:")
    for i, res in enumerate(resources):
        click.echo(f"    [{i + 1}]  {res.name}")
    idx = click.prompt("  Choose", type=click.IntRange(1, len(resources)), default=1) - 1
    url = _best_connection(resources[idx])
    click.echo(f"  Using: {resources[idx].name}  →  {url}")
    if not _confirm("Correct?"):
        url = _prompt("Plex server URL", default=url)
    return url


def _best_connection(resource) -> str:
    for conn in resource.connections:
        if not conn.local:
            return conn.uri
    if resource.connections:
        return resource.connections[0].uri
    return "http://localhost:32400"


def _manual_token() -> tuple[str, str]:
    click.echo(
        "\nTo find your Plex token manually:\n"
        "  1. Open Plex Web and sign in\n"
        "  2. Browse to any item → ⋮ → Get Info → View XML\n"
        "  3. Copy the value of  X-Plex-Token=  from the URL\n"
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
        "\n  [1]  Local auth server  — visit http://THIS-IP:9876 from any browser\n"
        "                            on your network; token captured automatically\n"
        "  [2]  plex.tv link       — open the plex.tv URL in any browser and sign in\n"
        "  [3]  Paste token        — manually copy a token from Plex Web\n"
    )
    choice = click.prompt("Choose", type=click.Choice(["1", "2", "3"]), default="1")

    url: Optional[str] = None
    token: Optional[str] = None

    if choice == "1":
        port = click.prompt("  Port for local auth server", default=9876, type=int)
        try:
            # Check port is available before starting thread
            s = socket.socket()
            s.bind(("0.0.0.0", port))
            s.close()
        except OSError:
            click.echo(f"  Port {port} is in use — falling back to direct plex.tv link.")
            token = _oauth_polling()
        else:
            token = _oauth_via_local_server(port)
        url = _resolve_servers(token)

    elif choice == "2":
        token = _oauth_polling()
        url = _resolve_servers(token)

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
        f"\nTest it:     intervallic sync --dry-run\n"
        f"Run a sync:  intervallic sync\n"
    )
