from __future__ import annotations

import socket
import threading

import pytest

from deploy.an2p import mooncen_loopback_redirect as redirect


def test_ipv6_loopback_listener_is_exclusive_and_redirects_to_reviewed_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        listener.bind(("::1", 5175))
    except OSError as exc:
        listener.close()
        pytest.skip(f"IPv6 loopback port 5175 is unavailable: {exc}")
    listener.listen(4)
    competing = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            competing.bind(("::1", 5175))
    finally:
        competing.close()

    monkeypatch.setattr(redirect.os, "geteuid", lambda: 1000)
    inherited = redirect.inherited_socket(listener.fileno())
    worker = threading.Thread(
        target=redirect.serve,
        args=(inherited,),
        kwargs={"maximum_connections": 1},
        daemon=True,
    )
    worker.start()
    with socket.create_connection(("::1", 5175), timeout=3) as client:
        client.sendall(
            b"GET /old-bookmark HTTP/1.1\r\n"
            b"Host: localhost:5175\r\nConnection: close\r\n\r\n"
        )
        response = client.recv(4096)
    worker.join(timeout=3)
    inherited.close()
    listener.close()

    assert not worker.is_alive()
    assert response.startswith(b"HTTP/1.1 308 Permanent Redirect\r\n")
    assert b"Location: http://127.0.0.1:5175/\r\n" in response
    assert b"Cache-Control: no-store\r\n" in response
