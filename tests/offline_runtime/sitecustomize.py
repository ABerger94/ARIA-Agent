"""Loaded only by the test runner's explicit PYTHONPATH, including child Python processes."""
import ipaddress
import socket

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_lookup = socket.getaddrinfo


def _local(host):
    if host in (None, 'localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guarded_connect(sock, address):
    if isinstance(address, tuple) and not _local(address[0]):
        raise OSError('External network disabled before test imports; inject a local fixture')
    return _connect(sock, address)


def _guarded_connect_ex(sock, address):
    if isinstance(address, tuple) and not _local(address[0]):
        raise OSError('External network disabled before test imports; inject a local fixture')
    return _connect_ex(sock, address)


def _guarded_lookup(host, port, *args, **kwargs):
    if not _local(host):
        raise OSError('External DNS disabled before test imports; inject a local fixture')
    return _lookup(host, port, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.getaddrinfo = _guarded_lookup


# Refuse at the HTTP API boundary as well as at the socket boundary, before
# request bodies can enter a real transport. Individual tests inject their
# own fake opener or use a loopback fixture.
import urllib.request
from urllib.parse import urlsplit

_urlopen = urllib.request.urlopen
_opener_open = urllib.request.OpenerDirector.open


def _check_url(value):
    url = value.full_url if isinstance(value, urllib.request.Request) else str(value)
    parts = urlsplit(url)
    if parts.scheme in ('http', 'https') and not _local(parts.hostname):
        raise OSError('External HTTP disabled at the test transport boundary')


def _offline_urlopen(url, *args, **kwargs):
    _check_url(url)
    return _urlopen(url, *args, **kwargs)


def _offline_opener_open(self, url, *args, **kwargs):
    _check_url(url)
    return _opener_open(self, url, *args, **kwargs)


urllib.request.urlopen = _offline_urlopen
urllib.request.OpenerDirector.open = _offline_opener_open
