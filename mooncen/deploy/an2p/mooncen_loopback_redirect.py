#!/usr/bin/env python3
"""Reserve IPv6 loopback Ops origin and redirect it to the reviewed IPv4 origin."""

from __future__ import annotations

import os
import socket
import sys


MAXIMUM_REQUEST_BYTES = 16 * 1024
RESPONSE = (
    b"HTTP/1.1 308 Permanent Redirect\r\n"
    b"Location: http://127.0.0.1:5175/\r\n"
    b"Cache-Control: no-store\r\n"
    b"Connection: close\r\n"
    b"Content-Length: 0\r\n\r\n"
)


class RedirectError(RuntimeError):
    """Raised when the fixed systemd socket is not the IPv6 loopback origin."""


def inherited_socket(descriptor: int = 3) -> socket.socket:
    if os.geteuid() == 0:
        raise RedirectError("IPv6 loopback redirect must run as the isolated API account")
    try:
        listener = socket.fromfd(descriptor, socket.AF_INET6, socket.SOCK_STREAM)
        family = listener.family
        address = listener.getsockname()
    except OSError as exc:
        raise RedirectError("systemd IPv6 listener is unavailable") from exc
    if family != socket.AF_INET6 or address[0] != "::1" or address[1] != 5175:
        listener.close()
        raise RedirectError("systemd IPv6 listener identity is invalid")
    listener.settimeout(60)
    return listener


def serve(listener: socket.socket, *, maximum_connections: int | None = None) -> None:
    handled = 0
    while maximum_connections is None or handled < maximum_connections:
        try:
            connection, peer = listener.accept()
        except TimeoutError:
            continue
        with connection:
            handled += 1
            if peer[0] != "::1":
                continue
            connection.settimeout(3)
            request = bytearray()
            try:
                while len(request) < MAXIMUM_REQUEST_BYTES:
                    chunk = connection.recv(
                        min(4096, MAXIMUM_REQUEST_BYTES - len(request))
                    )
                    if not chunk:
                        break
                    request.extend(chunk)
                    if b"\r\n\r\n" in request:
                        break
                if request:
                    connection.sendall(RESPONSE)
            except OSError:
                continue


def main() -> int:
    listener = inherited_socket()
    try:
        serve(listener)
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RedirectError) as exc:
        print(f"an2p IPv6 loopback redirect: {exc}", file=sys.stderr)
        raise SystemExit(78) from None
