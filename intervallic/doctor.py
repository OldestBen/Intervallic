"""End-to-end health check.

Answers the question "why isn't this working?" without the user having to
guess. Each check returns a verdict plus, when something is wrong, the exact
thing to do about it.

The important check is `check_path_mapping`. A playlist file can be written
perfectly and still be ignored by Roon, because the *paths inside it* are the
paths Plex knows rather than the paths Roon knows. That failure is silent —
Roon simply shows nothing — so we verify it explicitly by locating a real
track on the destination share and comparing it to what the mapping produces.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .config import Config

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


@dataclass
class Check:
    name: str
    status: str                       # ok | warn | fail | skip
    detail: str
    fix: Optional[str] = None         # what the user should do
    snippet: Optional[str] = None     # YAML they can paste


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)

    def add(self, *args, **kwargs) -> Check:
        check = Check(*args, **kwargs)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warned(self) -> List[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def healthy(self) -> bool:
        return not self.failed


# ── Individual checks ─────────────────────────────────────────────────────────

def check_plex(config: Config, report: Report):
    """Can we reach Plex, and does it have audio playlists?"""
    try:
        from plexapi.server import PlexServer
        server = PlexServer(config.plex.url, config.plex.token)
    except Exception as exc:
        report.add(
            "Plex connection", FAIL, f"Could not connect to {config.plex.url} — {exc}",
            fix="Check the URL and token in your config. Re-run `intervallic setup` "
                "to refresh the token if it has expired.",
        )
        return None

    report.add("Plex connection", OK, f"Connected to {server.friendlyName}")

    try:
        playlists = [p for p in server.playlists() if p.playlistType == "audio"]
    except Exception as exc:
        report.add("Plex playlists", FAIL, f"Could not list playlists — {exc}")
        return server

    if not playlists:
        report.add(
            "Plex playlists", WARN, "No audio playlists found on this server.",
            fix="Create a playlist in Plex, or check you are pointing at the right server.",
        )
    else:
        report.add("Plex playlists", OK, f"{len(playlists)} audio playlist(s) found")

    return server


def check_destination(config: Config, report: Report) -> bool:
    """Is the output destination reachable and writable?"""
    out = config.output

    if out.smb:
        from .output import test_smb_connection, _register_smb_session
        ok, message = test_smb_connection(out.smb)
        if not ok:
            report.add(
                "SMB share", FAIL, f"Cannot reach {out.smb.unc_directory} — {message}",
                fix="Check the server address, share name and credentials. Confirm the "
                    "share is reachable from this machine.",
            )
            return False
        report.add("SMB share", OK, f"Connected to //{out.smb.server}/{out.smb.share}")
        return _check_writable_smb(config, report)

    if out.sftp:
        from .output import test_sftp_connection
        ok, message = test_sftp_connection(out.sftp)
        if not ok:
            report.add(
                "SFTP connection", FAIL, f"Cannot reach {out.sftp.host} — {message}",
                fix="Check the host, username and key or password.",
            )
            return False
        report.add("SFTP connection", OK, f"Connected to {out.sftp.host}")
        return True

    from pathlib import Path
    directory = Path(out.directory or ".")
    if not directory.exists():
        report.add(
            "Output directory", WARN, f"{directory} does not exist yet.",
            fix="It will be created on the first sync.",
        )
        return True
    report.add("Output directory", OK, str(directory))
    return True


def _check_writable_smb(config: Config, report: Report) -> bool:
    """Actually write and delete a file, rather than assuming we can."""
    import smbclient
    from .output import _register_smb_session

    cfg = config.output.smb
    base = f"//{cfg.server}/{cfg.share}"
    if cfg.directory:
        base = f"{base}/{cfg.directory.strip('/')}"
    probe = f"{base}/.intervallic-write-test"

    try:
        _register_smb_session(cfg)
        smbclient.makedirs(base, exist_ok=True)
        with smbclient.open_file(probe, mode="w") as f:
            f.write("ok")
        smbclient.remove(probe)
    except Exception as exc:
        report.add(
            "Write permission", FAIL, f"Cannot write to {base} — {exc}",
            fix="The account needs write access to this share. Check the share's "
                "permissions on your NAS.",
        )
        return False

    report.add("Write permission", OK, f"Can write to {base}")
    return True


def check_path_mapping(config: Config, server, report: Report) -> None:
    """
    Verify that the paths written into playlists will mean something to Roon.

    We take a real track path from Plex and try to locate that same file on the
    destination share. That tells us how the Plex path lines up with the share,
    which is enough to confirm — or derive — the correct mapping.
    """
    if server is None:
        report.add("Path mapping", SKIP, "Skipped — no Plex connection.")
        return

    sample = _sample_track_path(server)
    if not sample:
        report.add("Path mapping", SKIP, "Skipped — no tracks found to test with.")
        return

    mapped = config.remap_path(sample)

    if not config.output.smb:
        # Without a share to inspect we can only report what the mapping does.
        if mapped == sample:
            report.add(
                "Path mapping", WARN,
                f"No mapping applies to {sample}",
                fix="If Roon mounts this library at a different path than Plex does, "
                    "add a path_mapping entry. If both see the same path, this is fine.",
            )
        else:
            report.add("Path mapping", OK, f"{sample}  →  {mapped}")
        return

    located = _locate_on_share(config, sample)

    if located is None:
        report.add(
            "Path mapping", WARN,
            f"Could not find this track on the share: {sample}",
            fix="The playlists may still be correct — this check only works when the "
                "music itself lives on the same share you are writing playlists to.",
        )
        return

    share_relative, plex_prefix = located

    if mapped == sample:
        report.add(
            "Path mapping", FAIL,
            "No mapping is configured, so playlists will contain Plex's paths. "
            "Roon will not recognise them.",
            fix="Add the mapping below. Replace ROON_MOUNT_PATH with the folder Roon "
                "shows for this library (Roon → Settings → Storage, or right-click a "
                "track → File Info).",
            snippet=_snippet(plex_prefix, "ROON_MOUNT_PATH"),
        )
        return

    # A mapping exists — check it is consistent with where the file actually sits.
    expected_tail = share_relative
    if mapped.replace("\\", "/").endswith(expected_tail):
        report.add(
            "Path mapping", OK,
            f"{sample}\n                 →  {mapped}",
        )
    else:
        report.add(
            "Path mapping", WARN,
            f"The mapped path does not line up with where the file sits on the share.\n"
            f"                 mapped:    {mapped}\n"
            f"                 on share:  {expected_tail}",
            fix="Check the `to:` value in your path_mapping. The part after Roon's "
                "mount point should match the path within the share.",
            snippet=_snippet(plex_prefix, "ROON_MOUNT_PATH"),
        )


def _snippet(plex_prefix: str, roon_prefix: str) -> str:
    return (
        "path_mapping:\n"
        f"  - from: \"{plex_prefix}\"\n"
        f"    to:   \"{roon_prefix}\""
    )


def _sample_track_path(server) -> Optional[str]:
    """One real file path from the first audio playlist that yields one."""
    try:
        playlists = [p for p in server.playlists() if p.playlistType == "audio"]
    except Exception:
        return None

    for playlist in playlists:
        try:
            items = playlist.items()
        except Exception:
            continue          # smart playlists cannot be enumerated
        for item in items:
            try:
                return item.media[0].parts[0].file
            except (IndexError, AttributeError):
                continue
    return None


def _locate_on_share(config: Config, plex_path: str) -> Optional[Tuple[str, str]]:
    """
    Find `plex_path` on the SMB share by testing progressively shorter tails.

    A Plex path of /shared/Music/Artist/Album/01.flac found on the share at
    Music/Artist/Album/01.flac tells us the share root corresponds to Plex's
    /shared, which is the mapping's `from:` prefix.

    Returns (path_within_share, plex_prefix) or None.
    """
    import smbclient
    from .output import _register_smb_session

    cfg = config.output.smb
    try:
        _register_smb_session(cfg)
    except Exception:
        return None

    parts = [p for p in plex_path.replace("\\", "/").split("/") if p]

    # Try the longest tail first so we match the most specific location.
    for start in range(len(parts)):
        tail = "/".join(parts[start:])
        candidate = f"//{cfg.server}/{cfg.share}/{tail}"
        try:
            smbclient.stat(candidate)
        except Exception:
            continue
        prefix = "/" + "/".join(parts[:start]) if start else "/"
        return tail, prefix.rstrip("/") or "/"

    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def run_diagnostics(config: Config) -> Report:
    report = Report()
    server = check_plex(config, report)
    reachable = check_destination(config, report)
    if reachable:
        check_path_mapping(config, server, report)
    else:
        report.add("Path mapping", SKIP, "Skipped — destination unreachable.")
    return report
