#!/usr/bin/env bash
# Run one hardened agent on the host with Podman.
#
# Network model (enforces "the proxy is the only way out" without host nftables):
#   * agent-internal  = `podman network create --internal`  -> NO external gateway.
#   * The proxy container is attached to BOTH agent-internal AND the default
#     (external) network, so it can reach the internet; the agent is on
#     agent-internal ONLY and can reach nothing except the proxy.
#   * The agent talks to the internet solely via HTTPS_PROXY -> proxy -> policy.py,
#     which allow-lists hosts and forces GET-only for research traffic.
#
# Required env on the host: ANTHROPIC_API_KEY, GH_TOKEN, GITLAB_TOKEN.
# Prefer `podman secret` over -e in production; -e is shown here for clarity.
set -euo pipefail

IMAGE="${IMAGE:-localhost/agent-runner:go}"
PROXY_IMAGE="${PROXY_IMAGE:-docker.io/mitmproxy/mitmproxy:latest}"
# Invocation type: "online" (default) or "offline-go" (air-gapped Go builds — deps and
# toolchains come from the image's pre-baked cache; the proxy denies package/toolchain hosts).
AGENT_MODE="${AGENT_MODE:-online}"
EGRESS_PROFILE=""; [[ "$AGENT_MODE" == "offline-go" ]] && EGRESS_PROFILE="offline-go"
NET="agent-internal-$$"
PROXY="agent-proxy-$$"
AGENT="agent-$$"
# Disk-backed, per-run ephemeral volumes for the big writable dirs. These hold the
# Go module cache + any DOWNLOADED TOOLCHAINS (one per distinct go.mod version across
# your repos, ~350-500MB extracted each) + all cloned repos. They must NOT be RAM-backed
# tmpfs: several toolchains + several repos would blow the --memory limit and OOM-kill.
HOMEVOL="agent-home-$$"     # /home/agent  -> GOMODCACHE, GOCACHE, .claude
WORKVOL="agent-work-$$"     # /workspace   -> all cloned repos
CA_DIR="$(mktemp -d)"

cleanup() { podman rm -f "$AGENT" "$PROXY" >/dev/null 2>&1 || true
            podman network rm "$NET" >/dev/null 2>&1 || true
            podman volume rm "$HOMEVOL" "$WORKVOL" >/dev/null 2>&1 || true
            rm -rf "$CA_DIR"; }
trap cleanup EXIT

podman volume create "$HOMEVOL" >/dev/null
podman volume create "$WORKVOL" >/dev/null

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"

# 1) internal (no-egress) network for the agent
podman network create --internal "$NET" >/dev/null

# 2) egress policy proxy: on internal net (for the agent) + default net (for the internet)
podman run -d --name "$PROXY" \
  --network "$NET" \
  --cap-drop=ALL --security-opt no-new-privileges \
  -e EGRESS_PROFILE="$EGRESS_PROFILE" \
  -v "$PWD/egress-proxy/policy.py:/policy.py:ro,Z" \
  -v "$CA_DIR:/home/mitmproxy/.mitmproxy:Z" \
  "$PROXY_IMAGE" \
  mitmdump -s /policy.py --mode regular --listen-host 0.0.0.0 --listen-port 8080 \
           --set connection_strategy=lazy >/dev/null
podman network connect podman "$PROXY"           # give the proxy real egress
sleep 3                                            # let mitmproxy write its CA
cp "$CA_DIR/mitmproxy-ca-cert.pem" "$CA_DIR/proxy-ca.pem" 2>/dev/null || true
PROXY_IP="$(podman inspect "$PROXY" \
  --format "{{ (index .NetworkSettings.Networks \"$NET\").IPAddress }}")"

# 3) the agent — maximally fenced, non-root, read-only rootfs, no host mounts.
# NOT exec'd, so the cleanup trap fires (removes proxy, network, volumes) on exit.
podman run --rm --name "$AGENT" \
  --network "$NET" \
  --user 1001:1001 --userns keep-id \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --security-opt label=type:container_t \
  --read-only \
  -v "$HOMEVOL:/home/agent" \
  -v "$WORKVOL:/workspace" \
  --tmpfs /tmp:rw,size=1g \
  --memory 8g --memory-swap 8g --pids-limit 512 --cpus 4 \
  -v "$CA_DIR/proxy-ca.pem:/etc/agent/ca/proxy-ca.pem:ro,Z" \
  -e HTTPS_PROXY="http://${PROXY_IP}:8080" \
  -e HTTP_PROXY="http://${PROXY_IP}:8080" \
  -e NO_PROXY="localhost,127.0.0.1" \
  -e SSL_CERT_FILE="/etc/agent/ca/proxy-ca.pem" \
  -e NODE_EXTRA_CA_CERTS="/etc/agent/ca/proxy-ca.pem" \
  -e ANTHROPIC_API_KEY \
  -e GH_TOKEN \
  -e GITLAB_TOKEN \
  -e AGENT_MODE="$AGENT_MODE" \
  -e AGENT_GOCACHE_SRC="${AGENT_GOCACHE_SRC:-/opt/go-cache}" \
  -e GOPRIVATE="${GOPRIVATE:-github.com/clcollins/*}" \
  -e AGENT_REPOS="${AGENT_REPOS:-github.com/clcollins/srepd}" \
  -e AGENT_TASK="${AGENT_TASK:-Summarize the repo and propose next steps.}" \
  "$IMAGE"
