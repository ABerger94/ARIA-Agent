"""Tests are offline. Local HTTP/WebSocket fixtures remain available."""
import ipaddress
import socket

import pytest


def _local(address):
    if not isinstance(address, tuple):
        return True  # Unix-domain sockets never leave this computer.
    host = address[0]
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _install_offline(monkeypatch):
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def guarded_connect(sock, address):
        if not _local(address):
            raise OSError('External network disabled in tests; inject a local fixture')
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        if not _local(address):
            raise OSError('External network disabled in tests; inject a local fixture')
        return connect_ex(sock, address)
    def guarded_lookup(host, port, *args, **kwargs):
        if host is not None and not _local((host, port)):
            raise OSError('External DNS disabled in tests; inject a local fixture')
        return getaddrinfo(host, port, *args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_lookup)


_SESSION_PATCH = pytest.MonkeyPatch()


def pytest_sessionstart(session):
    _install_offline(_SESSION_PATCH)


def pytest_sessionfinish(session, exitstatus):
    _SESSION_PATCH.undo()


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    _install_offline(monkeypatch)
