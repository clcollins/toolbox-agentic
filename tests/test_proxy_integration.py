"""Integration tests for the egress proxy — starts a real server and sends requests."""

import http.client
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def proxy_url():
    """Start the real proxy on a random port and yield its URL."""
    spec = importlib.util.spec_from_file_location("policy_integ", REPO_ROOT / "egress-proxy" / "policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    server = mod.ThreadedProxy(("127.0.0.1", 0), mod.ProxyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    for _ in range(50):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.1)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)

    yield f"127.0.0.1:{port}"
    server.shutdown()


def _connect_request(proxy_host, proxy_port, target):
    """Send a raw CONNECT request and return the HTTP status code."""
    s = socket.create_connection((proxy_host, int(proxy_port)), timeout=5)
    try:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
        status_line = response.split(b"\r\n")[0].decode()
        return int(status_line.split()[1])
    finally:
        s.close()


@pytest.mark.integration
class TestConnectTunneling:
    def test_trusted_host_allowed(self, proxy_url):
        host, port = proxy_url.split(":")
        status = _connect_request(host, port, "github.com:443")
        assert status == 200

    def test_untrusted_host_denied(self, proxy_url):
        host, port = proxy_url.split(":")
        status = _connect_request(host, port, "evil.example.com:443")
        assert status == 403

    def test_private_ip_denied(self, proxy_url):
        host, port = proxy_url.split(":")
        status = _connect_request(host, port, "10.0.0.1:443")
        assert status == 403

    def test_metadata_endpoint_denied(self, proxy_url):
        host, port = proxy_url.split(":")
        status = _connect_request(host, port, "169.254.169.254:80")
        assert status == 403

    def test_malformed_port_returns_400(self, proxy_url):
        host, port = proxy_url.split(":")
        status = _connect_request(host, port, "example.com:abc")
        assert status == 400


@pytest.mark.integration
class TestHttpForwarding:
    def test_post_to_untrusted_denied(self, proxy_url):
        host, port = proxy_url.split(":")
        conn = http.client.HTTPConnection(host, int(port))
        conn.request("POST", "http://untrusted.example.com/api", body=b"data")
        resp = conn.getresponse()
        assert resp.status == 405
        conn.close()

    def test_get_to_private_ip_denied(self, proxy_url):
        host, port = proxy_url.split(":")
        conn = http.client.HTTPConnection(host, int(port))
        conn.request("GET", "http://10.0.0.1/secret")
        resp = conn.getresponse()
        assert resp.status == 403
        conn.close()

    def test_malformed_content_length(self, proxy_url):
        host, port = proxy_url.split(":")
        conn = http.client.HTTPConnection(host, int(port))
        conn.request("GET", "http://example.com/", headers={"Content-Length": "abc"})
        resp = conn.getresponse()
        assert resp.status == 400
        conn.close()
