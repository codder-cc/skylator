"""
LAN scanner — discovers running Skylator translation servers.

Primary method: mDNS via zeroconf (service type: _skylator._tcp.local.)
Fallback:       TCP port scan of 192.168.x.x/24 on port 8765

Returns list of ServerInfo dicts:
    {
        "host":       str,   # IP or hostname
        "port":       int,
        "name":       str,   # mDNS service name or "direct"
        "platform":   str,   # "darwin" | "win32" | "linux"
        "model_name": str,
        "url":        str,   # "http://host:port"
    }
"""
from __future__ import annotations
import logging
import socket
import threading

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
SCAN_TIMEOUT = 1.0   # seconds per host for TCP fallback
MDNS_WAIT    = 3.0   # seconds to listen for mDNS responses


class LanScanner:
    """
    Discovers Skylator servers on the local network.

    Usage:
        scanner = LanScanner()
        servers = scanner.scan()            # blocking: mDNS first, then TCP fallback
        servers = scanner.scan_mdns_only()  # mDNS only
        servers = scanner.scan_tcp_only()   # TCP port scan fallback
    """

    def __init__(self, port: int = DEFAULT_PORT, mdns_enabled: bool = True):
        self._port         = port
        self._mdns_enabled = mdns_enabled

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(self) -> list[dict]:
        """
        Full scan: mDNS first, then TCP fallback if nothing found.
        Returns deduplicated list of ServerInfo dicts.
        """
        servers: list[dict] = []

        if self._mdns_enabled:
            try:
                servers = self.scan_mdns_only()
                log.info("mDNS scan found %d server(s)", len(servers))
            except ImportError:
                log.warning("zeroconf not installed — falling back to TCP scan")
            except Exception as exc:
                log.warning("mDNS scan failed (%s) — falling back to TCP scan", exc)

        if not servers:
            servers = self.scan_tcp_only()
            log.info("TCP scan found %d server(s)", len(servers))

        return servers

    def scan_mdns_only(self) -> list[dict]:
        """
        Browse for _skylator._tcp.local. services.
        Requires: pip install zeroconf
        """
        import time
        from zeroconf import ServiceBrowser, Zeroconf

        found: list[dict] = []

        class _Listener:
            def add_service(self, zc, type_, name):
                try:
                    info = zc.get_service_info(type_, name)
                    if info is None:
                        return
                    host = (
                        socket.inet_ntoa(info.addresses[0])
                        if info.addresses
                        else info.server
                    )
                    port  = info.port
                    props = {
                        k.decode(): v.decode()
                        for k, v in info.properties.items()
                        if isinstance(k, bytes) and isinstance(v, bytes)
                    }
                    found.append({
                        "host":       host,
                        "port":       port,
                        "name":       name,
                        "platform":   props.get("platform", "unknown"),
                        "model_name": props.get("model", "unknown"),
                        "url":        f"http://{host}:{port}",
                    })
                    log.debug("mDNS found: %s at %s:%s", name, host, port)
                except Exception as exc:
                    log.debug("mDNS listener error: %s", exc)

            def remove_service(self, *_): pass
            def update_service(self, *_): pass

        zc = Zeroconf()
        ServiceBrowser(zc, "_skylator._tcp.local.", _Listener())
        time.sleep(MDNS_WAIT)
        zc.close()
        return found

    def scan_tcp_only(self) -> list[dict]:
        """
        Scan all 192.168.x.x/24 subnets for the configured port.
        Uses threading for speed (up to 254 threads per subnet).
        """
        subnets = self._get_local_subnets()
        if not subnets:
            log.warning("TCP scan: could not determine local subnet(s)")
            return []

        found: list[dict] = []
        lock  = threading.Lock()

        def _probe(ip: str) -> None:
            try:
                with socket.create_connection((ip, self._port), timeout=SCAN_TIMEOUT):
                    info = self._fetch_info(ip, self._port)
                    with lock:
                        found.append({
                            "host":       ip,
                            "port":       self._port,
                            "name":       "direct",
                            "platform":   info.get("platform", "unknown"),
                            "model_name": info.get("model", "unknown"),
                            "url":        f"http://{ip}:{self._port}",
                        })
            except (OSError, TimeoutError):
                pass

        threads: list[threading.Thread] = []
        for subnet in subnets:
            for i in range(1, 255):
                ip = f"{subnet}.{i}"
                t  = threading.Thread(target=_probe, args=(ip,), daemon=True)
                threads.append(t)
                t.start()

        for t in threads:
            t.join(timeout=SCAN_TIMEOUT + 0.5)

        return found

    # ── Internals ─────────────────────────────────────────────────────────────

    def _get_local_subnets(self) -> list[str]:
        """Return list of /24 subnet prefixes like ["192.168.1", "192.168.0"]."""
        subnets: set[str] = set()
        try:
            hostname = socket.gethostname()
            ips      = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for item in ips:
                ip = item[4][0]
                if ip.startswith("192.168."):
                    parts = ip.split(".")
                    subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}")
        except Exception as exc:
            log.debug("Could not get local subnets: %s", exc)
        return list(subnets)

    def _fetch_info(self, host: str, port: int) -> dict:
        """Quick /info fetch to confirm and identify a server."""
        import requests
        try:
            r = requests.get(f"http://{host}:{port}/info", timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}
