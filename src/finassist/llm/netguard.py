"""Network guard (ARCH §14.4, A9). A Python audit hook (which cannot be removed once
installed) checks every DNS lookup and socket connection:

- strict (local / off modes): only loopback destinations are allowed.
- cloud: lookups only for the allowed host names, connections only to loopback or to
  addresses that a lookup of an allowed host returned.

A blocked attempt raises PermissionError inside the code that tried it.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import threading
from typing import Any

__all__ = ["blocked_attempts", "install", "installed_mode"]

_state: dict[str, Any] = {"mode": None, "hosts": frozenset(), "ips": set(), "blocked": []}
_lock = threading.Lock()
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _is_loopback(host: Any) -> bool:
    if host is None:
        return False
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    h = str(host).strip("[]").lower()
    if h in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


def _block(what: str) -> None:
    _state["blocked"].append(what)
    raise PermissionError(f"network access blocked by Coinleaf ({_state['mode']} mode): {what}")


def _hook(event: str, args: tuple[Any, ...]) -> None:
    mode = _state["mode"]
    if mode is None:
        return
    if event == "socket.getaddrinfo":
        host = args[0]
        if host is None or _is_loopback(host):
            return
        name = host.decode() if isinstance(host, bytes) else str(host)
        if mode == "cloud" and name.lower() in _state["hosts"]:
            return
        _block(f"lookup of {name}")
    elif event == "socket.connect":
        sock, address = args[0], args[1]
        if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", None):
            return
        host = address[0] if isinstance(address, tuple) and address else address
        if _is_loopback(host):
            return
        if mode == "cloud" and str(host) in _state["ips"]:
            return
        _block(f"connection to {host}")


_real_getaddrinfo = socket.getaddrinfo


def _recording_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    result = _real_getaddrinfo(host, *args, **kwargs)
    name = host.decode() if isinstance(host, bytes) else str(host)
    if _state["mode"] == "cloud" and name.lower() in _state["hosts"]:
        with _lock:
            for _family, _type, _proto, _canon, sockaddr in result:
                _state["ips"].add(str(sockaddr[0]))
    return result


def install(mode: str, allowed_hosts: list[str] | None = None) -> None:
    """Install (or tighten) the guard. Modes: 'strict' or 'cloud'. Once strict, stays strict."""
    if mode not in ("strict", "cloud"):
        raise ValueError("mode must be strict or cloud")
    with _lock:
        if _state["mode"] is None:
            sys.addaudithook(_hook)
            socket.getaddrinfo = _recording_getaddrinfo
        if _state["mode"] == "strict" and mode == "cloud":
            raise PermissionError("network guard is already strict for this process")
        _state["mode"] = mode
        _state["hosts"] = frozenset(h.lower() for h in (allowed_hosts or []))


def installed_mode() -> str | None:
    mode: str | None = _state["mode"]
    return mode


def blocked_attempts() -> list[str]:
    return list(_state["blocked"])
