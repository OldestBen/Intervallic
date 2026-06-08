"""
Shared SSH connection helper.

Uses auth_none() to discover what methods the server actually supports,
then tries only those — no guessing, no trying rejected methods.
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
    Return an authenticated paramiko SSHClient.
    Raises on failure with a clear message.
    """
    import paramiko

    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    transport.start_client(timeout=timeout)

    # Ask the server which auth methods it supports
    allowed: list[str] = []
    try:
        transport.auth_none(username)
        return _wrap(transport)   # server accepted no-auth (unusual but valid)
    except paramiko.BadAuthenticationType as e:
        allowed = list(e.allowed_types)
    except paramiko.AuthenticationException:
        allowed = []  # server didn't tell us; try everything

    if key_path:
        if "publickey" in allowed or not allowed:
            _auth_key(transport, username, key_path)
        else:
            raise paramiko.AuthenticationException(
                f"Server does not accept key auth. Allowed: {allowed}"
            )
        return _wrap(transport)

    if password is not None:
        if "password" in allowed or not allowed:
            try:
                transport.auth_password(username, password)
                return _wrap(transport)
            except paramiko.AuthenticationException:
                # Password was rejected — don't silently try other methods
                raise paramiko.AuthenticationException(
                    "Password authentication failed. Check the password."
                )

        if "keyboard-interactive" in allowed:
            transport.auth_interactive(
                username,
                lambda title, instr, fields: [password] * len(fields),
            )
            return _wrap(transport)

        raise paramiko.AuthenticationException(
            f"No supported auth method available. Server allows: {allowed}"
        )

    raise ValueError("Provide either key_path or password")


def _auth_key(transport, username: str, key_path: str) -> None:
    import paramiko

    last_exc: Optional[Exception] = None
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            key = cls.from_private_key_file(key_path)
            transport.auth_publickey(username, key)
            return
        except (paramiko.AuthenticationException, paramiko.SSHException):
            raise
        except Exception as e:
            last_exc = e

    raise last_exc or Exception(f"Could not load key: {key_path}")


def _wrap(transport) -> object:
    import paramiko
    client = paramiko.SSHClient()
    client._transport = transport  # noqa: SLF001
    return client
