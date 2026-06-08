"""
Shared SSH connection helper.

paramiko.SSHClient.connect() with password= only attempts the 'password'
auth method. Many Linux servers (Debian, Ubuntu, Proxmox LXCs) use
keyboard-interactive via PAM instead. The system ssh(1) client tries
both automatically; we must do the same explicitly.

This module provides _open_ssh() which:
  1. Opens a raw transport
  2. Tries password auth
  3. Falls back to keyboard-interactive (responding with the same password)
  4. Supports key-file auth with automatic key-type detection
"""
from __future__ import annotations

import socket
from typing import Optional


def _open_ssh(
    host: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    timeout: int = 10,
):
    """
    Return an authenticated paramiko.SSHClient.
    Raises paramiko.AuthenticationException (or socket errors) on failure.
    """
    import paramiko

    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    transport.start_client(timeout=timeout)

    if key_path:
        _auth_key(transport, username, key_path)
    elif password is not None:
        _auth_password_or_kbd(transport, username, password)
    else:
        raise ValueError("Either key_path or password must be provided")

    client = paramiko.SSHClient()
    client._transport = transport  # noqa: SLF001  (internal but stable API)
    return client


def _auth_key(transport, username: str, key_path: str) -> None:
    import paramiko

    key_classes = [
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.DSSKey,
    ]
    last_exc = None
    for cls in key_classes:
        try:
            key = cls.from_private_key_file(key_path)
            transport.auth_publickey(username, key)
            return
        except (paramiko.AuthenticationException, paramiko.SSHException):
            raise
        except Exception as e:
            last_exc = e
            continue
    raise last_exc or Exception(f"Could not load key from {key_path}")


def _auth_password_or_kbd(transport, username: str, password: str) -> None:
    """Try password auth; fall back to keyboard-interactive (PAM)."""
    import paramiko

    try:
        transport.auth_password(username, password)
        return
    except paramiko.AuthenticationException:
        pass

    # keyboard-interactive: server sends prompts, we reply with the password
    transport.auth_interactive(
        username,
        lambda title, instructions, fields: [password] * len(fields),
    )
