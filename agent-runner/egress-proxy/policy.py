"""
Egress policy for the agent's *only* route to the internet.

Run as a sidecar / separate pod; the agent container is network-fenced so this
proxy is its single reachable next hop (K8s: NetworkPolicy egress -> proxy only;
podman: nftables in the netns, see run-podman.sh). The agent sets
HTTPS_PROXY/HTTP_PROXY to this proxy.

Two classes of traffic:

  1. TRUSTED_HOSTS  — git/API/tooling endpoints that legitimately need POST/PUT/etc
     (clone/push, PR/MR creation, Claude API, Go module fetch, package registries).
     These are TLS-passthrough (not intercepted) and allowed for any method, but ONLY
     for hosts on the list. Everything else in this class is denied.

  2. RESEARCH       — arbitrary web reads for WebFetch. TLS is terminated so the
     method can be inspected, and only GET/HEAD is permitted. POST/PUT/DELETE/PATCH
     are rejected. (Install this proxy's CA in the agent image trust store, or scope
     research to https only via a known research allow-list.)

Start:  mitmdump -s policy.py --mode regular --listen-port 8080 \
          --set block_global=false --set connection_strategy=lazy
Generate/trust CA:  mitmproxy writes ~/.mitmproxy/mitmproxy-ca-cert.pem — copy it into
the agent image and `update-ca-trust`, OR mount it and set NODE_EXTRA_CA_CERTS/SSL_CERT_FILE.
"""
from mitmproxy import http
import os

# Hosts allowed ALL methods (push, PR/MR, API). Tighten to your repos/instances.
TRUSTED_HOSTS = {
    "api.anthropic.com",
    "github.com", "api.github.com", "codeload.github.com",
    "objects.githubusercontent.com", "uploads.github.com",
    "gitlab.com", "registry.gitlab.com",
    # Go modules / checksum db / registries
    "proxy.golang.org", "sum.golang.org", "goproxy.io",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
    "static.crates.io", "index.crates.io",
}

# Package/toolchain endpoints removed when EGRESS_PROFILE=offline-go (air-gapped Go):
# deps + toolchains must come from the pre-baked/seeded cache, not the network.
_PACKAGE_HOSTS = {
    "proxy.golang.org", "sum.golang.org", "goproxy.io",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
    "static.crates.io", "index.crates.io",
}
if os.environ.get("EGRESS_PROFILE") == "offline-go":
    TRUSTED_HOSTS -= _PACKAGE_HOSTS  # only git hosts + Anthropic remain reachable

# Methods permitted for RESEARCH (non-trusted) hosts.
RESEARCH_METHODS = {"GET", "HEAD"}

# Optional: restrict research to specific domains too (empty = any host, GET-only).
RESEARCH_ALLOW = set()  # e.g. {"pkg.go.dev", "raw.githubusercontent.com", "docs.gitlab.com"}


def _host(flow: http.HTTPFlow) -> str:
    return (flow.request.pretty_host or "").lower()


def http_connect(flow: http.HTTPFlow):
    """CONNECT (HTTPS tunnel). Passthrough is only for trusted hosts.

    For non-trusted hosts we do NOT reject here (we still want to inspect the
    inner request method), so let it proceed to TLS interception via `request`.
    """
    host = _host(flow)
    if host in TRUSTED_HOSTS:
        # mark for TLS passthrough so we don't MITM trusted endpoints
        flow.metadata["passthrough"] = True


def request(flow: http.HTTPFlow):
    host = _host(flow)
    method = flow.request.method.upper()

    if host in TRUSTED_HOSTS:
        return  # trusted endpoint: allow any method

    # RESEARCH class (intercepted): enforce GET/HEAD and optional host allow-list.
    if RESEARCH_ALLOW and host not in RESEARCH_ALLOW:
        flow.response = http.Response.make(403, b"egress denied: host not allow-listed\n")
        return
    if method not in RESEARCH_METHODS:
        flow.response = http.Response.make(
            405, f"egress denied: {method} not permitted (research is GET-only)\n".encode()
        )
        return
    # else: GET/HEAD to an allowed research host -> permit
