#!/usr/bin/env python3
"""
Egress policy proxy — the agent container's only route to the internet.

Zero third-party dependencies (stdlib only). Runs as a sidecar; the agent
container is network-fenced so this proxy is its single reachable next hop.
The agent sets HTTPS_PROXY/HTTP_PROXY to this proxy.

Two traffic classes:

  1. TRUSTED_HOSTS — git/API/tooling endpoints that legitimately need any
     HTTP method (clone/push, PR/MR creation, Claude API, Go module fetch).
     HTTPS (CONNECT): blind TCP tunnel (no MITM).
     HTTP: forward with any method.

  2. Everything else — HTTPS: CONNECT denied (403). HTTP: GET/HEAD only (405
     for other methods). This is stricter than MITM-and-inspect: untrusted
     HTTPS hosts are unreachable entirely, not just read-only.

Start:  python3 policy.py
        (listens on 0.0.0.0:8080)

Env:
  EGRESS_PROFILE=offline-go   strips package/toolchain hosts from the trusted set
  PROXY_PORT=8080             listen port (default 8080)
"""

import http.client
import http.server
import ipaddress
import os
import select
import signal
import socket
import socketserver
import sys
from urllib.parse import urlparse

TRUSTED_HOSTS = {
    "api.anthropic.com",
    # Vertex AI (Claude via GCP)
    "oauth2.googleapis.com",
    "aiplatform.googleapis.com",
    # GitHub
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "uploads.github.com",
    # GitLab
    "gitlab.com",
    "registry.gitlab.com",
    # Go modules / checksum db / registries
    "proxy.golang.org",
    "sum.golang.org",
    "storage.googleapis.com",
    "google.golang.org",
    "golang.org",
    "goproxy.io",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "static.crates.io",
    "index.crates.io",
}

_TRUSTED_SUFFIXES = ("-aiplatform.googleapis.com",)

_PACKAGE_HOSTS = {
    "proxy.golang.org",
    "sum.golang.org",
    "storage.googleapis.com",
    "google.golang.org",
    "golang.org",
    "goproxy.io",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "static.crates.io",
    "index.crates.io",
}

if os.environ.get("EGRESS_PROFILE") == "offline-go":
    TRUSTED_HOSTS -= _PACKAGE_HOSTS

RESEARCH_METHODS = {"GET", "HEAD"}


def _is_private_host(host):
    """Return True if host resolves to a private/loopback/link-local address."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            addr = ipaddress.ip_address(socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)[0][4][0])
        except (socket.gaierror, IndexError, ValueError):
            return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _is_trusted(host):
    host = host.lower().split(":")[0]
    if host in TRUSTED_HOSTS:
        return True
    return any(host.endswith(s) for s in _TRUSTED_SUFFIXES)


def _tunnel(client_sock, remote_sock):
    """Bidirectional TCP relay between two sockets."""
    socks = [client_sock, remote_sock]
    try:
        while True:
            readable, _, errs = select.select(socks, [], socks, 30)
            if errs:
                break
            for s in readable:
                data = s.recv(65536)
                if not data:
                    return
                target = remote_sock if s is client_sock else client_sock
                target.sendall(data)
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    server_version = "agent-egress-proxy/1.0"

    def do_CONNECT(self):
        host_port = self.path
        host = host_port.split(":")[0]
        try:
            port = int(host_port.split(":")[1]) if ":" in host_port else 443
        except (ValueError, IndexError):
            self.send_error(400, f"malformed CONNECT target: {host_port}")
            return

        if _is_private_host(host):
            self.send_error(403, f"egress denied: CONNECT to private/internal address {host}")
            return

        if not _is_trusted(host):
            self.send_error(403, f"egress denied: CONNECT to untrusted host {host}")
            return

        try:
            remote = socket.create_connection((host, port), timeout=10)
        except Exception as e:
            self.send_error(502, f"cannot reach {host}:{port}: {e}")
            return

        self.send_response(200, "Connection established")
        self.end_headers()

        _tunnel(self.connection, remote)

    def _forward_request(self):
        parsed = urlparse(self.path)
        host = parsed.hostname or ""
        port = parsed.port or 80
        method = self.command

        if _is_private_host(host):
            self.send_error(403, f"egress denied: request to private/internal address {host}")
            return

        if not _is_trusted(host) and method not in RESEARCH_METHODS:
            self.send_error(
                405,
                f"egress denied: {method} not permitted to untrusted host {host}",
            )
            return

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error(400, "invalid Content-Length header")
            return
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "proxy-authorization")
        }

        try:
            conn = http.client.HTTPConnection(host, port, timeout=30)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding",):
                    self.send_header(k, v)
            self.end_headers()

            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            conn.close()
        except Exception as e:
            self.send_error(502, f"proxy error: {e}")

    do_GET = _forward_request
    do_HEAD = _forward_request
    do_POST = _forward_request
    do_PUT = _forward_request
    do_DELETE = _forward_request
    do_PATCH = _forward_request
    do_OPTIONS = _forward_request

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {self.address_string()} {fmt % args}\n")
        sys.stderr.flush()


class ThreadedProxy(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(os.environ.get("PROXY_PORT", "8080"))
    server = ThreadedProxy(("0.0.0.0", port), ProxyHandler)
    print(f"[proxy] listening on 0.0.0.0:{port}", flush=True)
    print(f"[proxy] trusted hosts: {len(TRUSTED_HOSTS)}", flush=True)
    profile = os.environ.get("EGRESS_PROFILE", "default")
    print(f"[proxy] egress profile: {profile}", flush=True)

    def _shutdown(*_):
        print("[proxy] shutting down", flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
