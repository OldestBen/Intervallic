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
    if default:
        val = click.prompt(msg, default=default, show_default=True)
    else:
        val = click.prompt(msg, default="", show_default=False)
    return val.strip() if isinstance(val, str) else val


def _confirm(msg: str, default: bool = True) -> bool:
    return click.confirm(msg, default=default)


def _header(msg: str) -> None:
    bar = click.style("─" * 60, fg="cyan", dim=True)
    click.echo(f"\n{bar}")
    click.echo(f"  {click.style(msg, bold=True)}")
    click.echo(bar)


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


class _WizardBack(Exception):
    """Raised by an output-method sub-wizard to signal the user wants to go back."""


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

    default_choice = "1"
    while True:
        click.echo(
            "\nRoon on Linux mounts SMB shares via the kernel CIFS driver — those\n"
            "mount points are accessible over SSH. Intervallic only writes M3U8\n"
            "files and never touches Roon's database.\n"
            "\n"
            "  [1]  SSH to Roon host  — detects mounted SMB shares automatically\n"
            "                           (recommended for Linux / Proxmox LXC)\n"
            "  [2]  SMB credentials   — connect directly to the NAS (cross-platform)\n"
            "  [3]  Local path        — Roon is on this machine or share is mounted here\n"
        )
        choice = click.prompt("Choose", type=click.Choice(["1", "2", "3"]), default=default_choice)

        out: dict = {"format": "m3u8", "overwrite": True}

        try:
            if choice == "1":
                out["sftp"] = _wizard_sftp()
            elif choice == "2":
                out["smb"] = _wizard_smb()
            else:
                out["directory"] = _prompt(
                    "Path to Roon's watched playlist folder",
                    default=os.path.expanduser("~/Music/Playlists"),
                )
            return out
        except _WizardBack:
            click.echo("\n  Going back to output method selection …")
            default_choice = choice
            continue


def _wizard_smb() -> dict:
    """Configure direct SMB write to the NAS share that Roon watches."""
    click.echo(
        "\n  Enter the details of the SMB share Roon is watching.\n"
        "  This is the same share you configured in Roon → Settings → Storage.\n"
    )
    server = _prompt("NAS / SMB server (hostname or IP)")
    share  = _prompt("Share name (e.g. music, Media, homes)")

    click.echo("\n  Subfolder within the share where M3U8 files should go.")
    click.echo("  Leave blank to place files at the share root.")
    directory = _prompt("Subfolder (e.g. Playlists)", default="Playlists")

    username = _prompt("SMB username", default=os.getenv("USER", ""))
    password = click.prompt("SMB password", hide_input=True, default="", show_default=False)
    domain   = _prompt("Domain (leave blank for workgroup/local)", default="")

    smb: dict = {
        "server":    server,
        "share":     share,
        "directory": directory,
        "username":  username,
    }
    if password:
        smb["password"] = password
    if domain:
        smb["domain"] = domain

    click.echo("\nTesting SMB connection … ", nl=False)
    from .output import test_smb_connection
    from .config import SmbConfig
    ok, msg = test_smb_connection(SmbConfig(
        server=server, share=share, directory=directory,
        username=username,
        password=password or None,
        domain=domain,
    ))
    if ok:
        click.echo("OK")
    else:
        click.echo(f"Failed: {msg}", err=True)
        if not _confirm("Continue anyway?", default=False):
            raise _WizardBack()

    return smb


def _wizard_sftp() -> dict:
    """
    SSH into the Roon host and detect which CIFS mount points Roon has
    created for its SMB storage connections. Write M3U8 files there via SFTP.
    No SMB credentials required — just SSH access to the Roon host.
    """
    click.echo()
    click.echo("  Searching for Roon Core on the network … ", nl=False)
    from .roon_discovery import discover_roon_core
    core = discover_roon_core(timeout=6)
    click.echo(f"found at {core}" if core else "not found (cross-VLAN — enter IP manually)")

    # Seed defaults so retries remember what was entered previously
    default_host     = core or ""
    default_port     = 22
    default_username = "root"
    default_auth     = "2"   # password is more common for first-time users
    default_key      = os.path.expanduser("~/.ssh/id_rsa")

    from .roon_discovery import find_remote_playlist_paths, ScanDiagnostics
    from .output import test_sftp_connection

    while True:
        host     = _prompt("Roon host (hostname or IP)", default=default_host)
        ssh_port = click.prompt("SSH port", default=default_port, type=int)
        username = _prompt("SSH username", default=default_username)

        click.echo("\n  [1]  SSH key file  (recommended — no password stored)\n  [2]  Password\n")
        auth = click.prompt("Auth method", type=click.Choice(["1", "2"]), default=default_auth)

        sftp: dict = {"host": host, "port": ssh_port, "username": username}

        if auth == "1":
            if not os.path.exists(default_key):
                click.echo(f"\n  No key found at {default_key}.")
                if _confirm("  Generate one and install it on the Roon host now?"):
                    _generate_and_install_key(host, ssh_port, username, default_key)
            sftp["key_path"] = _prompt("Path to private key", default=default_key)
        else:
            sftp["password"] = click.prompt("SSH password", hide_input=True)

        # Scan /proc/mounts on the Roon host for CIFS entries — those are the
        # SMB shares Roon mounted via mount.cifs when you added them in Settings → Storage
        click.echo(
            "\n  Scanning Roon host for SMB shares (Roon mounts these via kernel CIFS) … ",
            nl=False,
        )
        diag = ScanDiagnostics()
        candidates = find_remote_playlist_paths(
            host=host, port=ssh_port, username=username,
            password=sftp.get("password"), key_path=sftp.get("key_path"),
            diag=diag,
        )

        if not diag.ssh_ok:
            click.echo("failed.\n")
            click.echo(f"  Error: {diag.ssh_error}\n")
            click.echo("  Options:")
            click.echo("    [1]  Retry with different credentials")
            click.echo("    [2]  Enter the remote path manually and continue")
            click.echo("    [3]  Go back and choose a different output method")
            choice = click.prompt("  Choose", type=click.Choice(["1", "2", "3"]), default="1")
            if choice == "1":
                # Keep host/port/username as defaults, loop again
                default_host     = host
                default_port     = ssh_port
                default_username = username
                default_auth     = auth
                continue
            elif choice == "2":
                remote_dir = _prompt("Remote path to place M3U8 files")
                sftp["remote_directory"] = remote_dir
                return sftp
            else:
                raise _WizardBack()

        if candidates:
            # Filter out the home fallback from the top of the list if better options exist
            real_candidates = [c for c in candidates if not c.startswith(("/root", "/home"))]
            display = real_candidates[:6] if real_candidates else candidates[:6]
            click.echo("found.\n")
            click.echo("  These are the paths Roon has access to — pick where to put playlists:\n")
            for i, p in enumerate(display):
                click.echo(f"    [{i + 1}]  {p}")
            click.echo(f"    [{len(display) + 1}]  Enter manually")
            idx = click.prompt("  Choose", type=click.IntRange(1, len(display) + 1), default=1)
            remote_dir = display[idx - 1] if idx <= len(display) else _prompt("Remote path")
        else:
            click.echo("none found.\n")
            click.echo(
                "  SSH connected but no mounts or audio files were found.\n"
                "  Possible reasons:\n"
                "   • SMB storage not yet added in Roon Settings → Storage\n"
                "   • Proxmox bind-mount not yet configured on the host\n"
                "   • Music is on a path not searched (/opt, /volume1, etc.)\n"
            )
            click.echo("  Scan diagnostics:\n")
            for line in diag.report().splitlines():
                click.echo(f"    {line}")
            click.echo()
            remote_dir = _prompt("Remote path to place M3U8 files")

        sftp["remote_directory"] = remote_dir

        click.echo("\nTesting SFTP connection … ", nl=False)
        ok, msg = test_sftp_connection(SftpConfig(
            host=host, port=ssh_port, username=username,
            remote_directory=remote_dir,
            password=sftp.get("password"), key_path=sftp.get("key_path"),
        ))
        if ok:
            click.echo("OK")
            return sftp

        click.echo(f"Failed: {msg}\n")
        click.echo("  Options:")
        click.echo("    [1]  Retry with different credentials")
        click.echo("    [2]  Continue anyway (path may not exist yet)")
        click.echo("    [3]  Go back and choose a different output method")
        choice = click.prompt("  Choose", type=click.Choice(["1", "2", "3"]), default="1")
        if choice == "2":
            return sftp
        elif choice == "3":
            raise _WizardBack()
        # choice == "1": loop again, keep defaults
        default_host     = host
        default_port     = ssh_port
        default_username = username
        default_auth     = auth


def _generate_and_install_key(host: str, port: int, username: str, key_path: str) -> None:
    """Generate an SSH keypair and install the public key on the Roon host."""
    import subprocess
    pub_path = key_path + ".pub"
    try:
        click.echo(f"  Generating key at {key_path} … ", nl=False)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path],
            check=True, capture_output=True,
        )
        click.echo("done.")
        password = click.prompt(
            f"  SSH password for {username}@{host} (to install the key)",
            hide_input=True,
        )
        with open(pub_path) as f:
            pubkey = f.read().strip()
        import paramiko
        from .ssh_util import _open_ssh
        client = _open_ssh(host, port, username, password=password)
        client.exec_command(
            f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"echo '{pubkey}' >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys"
        )
        client.close()
        click.echo("  Public key installed. You won't need a password again.")
    except Exception as exc:
        click.echo(f"  Could not install key automatically: {exc}")
        click.echo(f"  You can install it manually: ssh-copy-id -i {pub_path} {username}@{host}")


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
        f"\n  {click.style('Intervallic', fg='cyan', bold=True)}  setup\n\n"
        "  This wizard will create your config file in 3 short steps.\n"
        f"  Press {click.style('Ctrl-C', bold=True)} at any time to cancel without writing anything."
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
        f"\n  {click.style('✓', fg='green', bold=True)}  Config written to "
        f"{click.style(output_path, bold=True)}\n\n"
        f"  Test your setup:   {click.style('intervallic sync --dry-run', bold=True)}\n"
        f"  Run a full sync:   {click.style('intervallic sync', bold=True)}\n"
    )
