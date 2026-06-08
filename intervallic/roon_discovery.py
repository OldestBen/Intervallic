"""
Roon Core auto-discovery and remote path detection.

Key facts about Roon playlist import (verified against Roon docs):
  - M3U8 files must be placed INSIDE a folder Roon is already watching
    for music. There is no separate playlist import folder.
  - On Linux, Roon mounts SMB shares via kernel CIFS (requires mount.cifs).
    Those mount points show up in /proc/mounts and are accessible via SSH.
  - In Proxmox LXC setups, the host may mount the NAS and bind-mount it
    into the LXC — these appear as non-CIFS types in /proc/mounts.
  - Roon's watched folder config is in a proprietary database — not
    directly readable. We infer watched folders from mounts and audio files.

Roon data directories by install type:
  - Easy installer:  /var/roon/RoonServer
  - Manual tar.gz:   ~/.RoonServer
  - DietPi:          /mnt/dietpi_userdata/roonserver
  - Custom:          $ROON_DATAROOT/RoonServer
"""
from __future__ import annotations

from typing import Optional


# ── Core discovery via SOOD UDP broadcast ─────────────────────────────────────

def discover_roon_core(timeout: int = 6) -> Optional[str]:
    """
    Broadcast a SOOD query and return the IP of the first Roon Core found,
    or None. Only works on the same VLAN/broadcast domain.
    """
    try:
        from roonapi import RoonDiscovery
    except ImportError:
        return None

    import threading
    result: dict = {}

    def _run():
        disc = RoonDiscovery()
        host, _ = disc.first()
        if host:
            result["host"] = host

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result.get("host")


# ── Remote diagnostics + path detection ──────────────────────────────────────

class ScanDiagnostics:
    """Collects what happened during the remote scan for user-facing output."""
    def __init__(self):
        self.ssh_ok = False
        self.ssh_error: Optional[str] = None
        self.raw_mounts: list[str] = []          # full /proc/mounts
        self.cifs_mounts: list[str] = []
        self.nfs_mounts: list[str] = []
        self.bind_mounts: list[str] = []
        self.audio_dirs: list[str] = []
        self.existing_playlists: list[str] = []
        self.commands_run: list[tuple[str, list[str]]] = []  # (cmd, output)

    def report(self) -> str:
        lines = []
        if not self.ssh_ok:
            lines.append(f"SSH connection failed: {self.ssh_error}")
            return "\n".join(lines)

        lines.append("SSH connection: OK")
        lines.append("")

        if self.raw_mounts:
            lines.append(f"/proc/mounts ({len(self.raw_mounts)} entries):")
            for m in self.raw_mounts:
                lines.append(f"  {m}")
        else:
            lines.append("/proc/mounts: empty or unreadable")
        lines.append("")

        lines.append(f"CIFS/SMB mounts found:  {self.cifs_mounts or 'none'}")
        lines.append(f"NFS mounts found:       {self.nfs_mounts or 'none'}")
        lines.append(f"Bind mounts found:      {self.bind_mounts or 'none'}")
        lines.append(f"Dirs with audio files:  {self.audio_dirs or 'none'}")
        lines.append(f"Existing .m3u8 dirs:    {self.existing_playlists or 'none'}")

        return "\n".join(lines)


def _home_for_user(sftp, username: str) -> str:
    if username == "root":
        return "/root"
    try:
        with sftp.open("/etc/passwd") as f:
            for line in f.read().decode(errors="replace").splitlines():
                parts = line.split(":")
                if len(parts) >= 6 and parts[0] == username:
                    return parts[5]
    except Exception:
        pass
    return f"/home/{username}"


def find_remote_playlist_paths(
    host: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    diag: Optional[ScanDiagnostics] = None,
) -> list[str]:
    """
    SSH into host and return a ranked list of candidate directories.

    Strategy:
      1. CIFS/SMB kernel mounts  (Roon's own mounts via mount.cifs)
      2. NFS mounts              (NAS connected via NFS instead of SMB)
      3. Bind mounts under /mnt  (Proxmox host mounts → bind into LXC)
      4. Any directory containing audio files under common paths
      5. Existing .m3u8 locations (confirms where imports already live)
      6. DietPi standard path
      7. User home fallback

    Pass a ScanDiagnostics instance to collect debug information.
    Returns [] on SSH failure — caller handles fallback.
    """
    if diag is None:
        diag = ScanDiagnostics()

    try:
        import paramiko
    except ImportError:
        diag.ssh_error = "paramiko not installed"
        return []

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kw: dict = dict(hostname=host, port=port, username=username or None)
    if key_path:
        connect_kw["key_filename"] = key_path
    if password:
        connect_kw["password"] = password

    def run(cmd: str, timeout: int = 15) -> list[str]:
        try:
            _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            out = [l.strip() for l in stdout.read().decode(errors="replace").splitlines() if l.strip()]
            diag.commands_run.append((cmd, out))
            return out
        except Exception as e:
            diag.commands_run.append((cmd, [f"ERROR: {e}"]))
            return []

    candidates: list[str] = []

    try:
        client.connect(**connect_kw, timeout=10)
        diag.ssh_ok = True

        sftp = client.open_sftp()
        home = _home_for_user(sftp, username)
        sftp.close()

        # ── Read full /proc/mounts for diagnostics ────────────────────────────
        diag.raw_mounts = run("cat /proc/mounts")

        # ── 1. CIFS/SMB kernel mounts ─────────────────────────────────────────
        # Roon on Linux uses mount.cifs — these appear as type cifs or smb3
        cifs_mounts = run(
            "awk '$3==\"cifs\" || $3==\"smb3\" || $3==\"smb2\" || $3==\"smb\" {print $2}'"
            " /proc/mounts"
        )
        diag.cifs_mounts = cifs_mounts

        # ── 2. NFS mounts ─────────────────────────────────────────────────────
        nfs_mounts = run(
            "awk '$3==\"nfs\" || $3==\"nfs4\" {print $2}' /proc/mounts"
        )
        diag.nfs_mounts = nfs_mounts

        # ── 3. Bind mounts under /mnt (Proxmox host → LXC passthrough) ────────
        # In Proxmox, the host mounts the NAS and bind-mounts a subdir into the
        # LXC. These show up as the underlying FS type (ext4, btrfs, etc.), not
        # as cifs — we catch them by looking for mounts under /mnt with the
        # source being a non-rootfs path.
        bind_mounts = run(
            "awk 'NR>1 && $2 ~ /^\\/mnt/ && $3 != \"cifs\" && $3 != \"tmpfs\""
            " && $3 != \"sysfs\" && $3 != \"proc\" {print $2}' /proc/mounts"
        )
        diag.bind_mounts = bind_mounts

        # Combine all remote mount candidates
        all_mounts = list(dict.fromkeys(cifs_mounts + nfs_mounts + bind_mounts))

        # ── 4. Verify mounts contain audio files ──────────────────────────────
        confirmed: list[str] = []
        for mount in all_mounts:
            has_audio = run(
                f"find {mount!r} -maxdepth 5 "
                r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.alac' "
                r"-o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.wav' "
                r"-o -iname '*.dsf' -o -iname '*.dff' \) "
                "-print -quit 2>/dev/null",
                timeout=15,
            )
            if has_audio:
                confirmed.append(mount)

        # If mounts exist but none have audio yet, still suggest them
        # (Roon may still be scanning, or library is on a sub-path)
        if all_mounts and not confirmed:
            confirmed = all_mounts

        # ── 5. Existing .m3u8 dirs (best signal — already working) ────────────
        for mount in confirmed:
            existing = run(
                f"find {mount!r} -maxdepth 5 "
                r"\( -iname '*.m3u8' -o -iname '*.m3u' \) "
                r"-printf '%h\n' 2>/dev/null | sort -u | head -5",
                timeout=10,
            )
            for d in existing:
                if d not in candidates:
                    candidates.append(d)
            diag.existing_playlists.extend(existing)

        # Suggest <mount>/Playlists for each confirmed mount
        for mount in confirmed:
            s = mount.rstrip("/") + "/Playlists"
            if s not in candidates:
                candidates.append(s)

        # ── 6. Broad audio file search (fallback if no recognised mounts) ─────
        if not confirmed:
            audio_dirs = run(
                r"find /mnt /media /data /srv /storage -maxdepth 6 "
                r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.alac' "
                r"-o -iname '*.dsf' \) "
                r"-printf '%h\n' 2>/dev/null | sort -u | head -10",
                timeout=20,
            )
            diag.audio_dirs = audio_dirs
            for d in audio_dirs:
                s = d.rstrip("/") + "/Playlists"
                if s not in candidates:
                    candidates.append(s)

        # ── 7. DietPi ─────────────────────────────────────────────────────────
        if run("test -d /mnt/dietpi_userdata/Music && echo y"):
            s = "/mnt/dietpi_userdata/Music/Playlists"
            if s not in candidates:
                candidates.append(s)

        # ── 8. Home fallback ──────────────────────────────────────────────────
        fallback = home.rstrip("/") + "/Music/Playlists"
        if fallback not in candidates:
            candidates.append(fallback)

    except Exception as e:
        diag.ssh_ok = False
        diag.ssh_error = str(e)

    finally:
        client.close()

    return candidates
