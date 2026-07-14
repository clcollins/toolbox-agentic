#!/usr/bin/env bash
# Run one hardened agent on the host with Podman.
#
# Network model: both the proxy and agent run on a per-run podman network.
# The agent routes all traffic through HTTPS_PROXY/HTTP_PROXY -> proxy ->
# policy.py, which allow-lists hosts and denies CONNECT to untrusted hosts.
#
# On rootful podman you can use `--internal` networks for hard network
# isolation; rootless podman's DNS resolver doesn't work across internal +
# external networks, so we use a standard bridge and rely on the proxy env
# vars to enforce the egress path.
#
# Required env on the host (ONE of the following auth methods):
#   Direct API:  ANTHROPIC_API_KEY
#   Vertex AI:   CLAUDE_CODE_USE_VERTEX=1 + VERTEXAI_PROJECT + VERTEXAI_LOCATION
#                + GCP Application Default Credentials (~/.config/gcloud/)
# Also: GH_TOKEN, GITLAB_TOKEN (for push/PR/MR).
# Prefer `podman secret` over -e in production; -e is shown here for clarity.
set -euo pipefail

IMAGE="${IMAGE:-localhost/agent-runner:go}"
PROXY_IMAGE="${PROXY_IMAGE:-localhost/agent-egress-proxy:latest}"
# Invocation type: "online" (default) or "offline-go" (air-gapped Go builds — deps and
# toolchains come from the image's pre-baked cache; the proxy denies package/toolchain hosts).
AGENT_MODE="${AGENT_MODE:-online}"
EGRESS_PROFILE=""; [[ "$AGENT_MODE" == "offline-go" ]] && EGRESS_PROFILE="offline-go"
NET="agent-net-$$"
PROXY="agent-proxy-$$"
AGENT="agent-$$"
# Disk-backed, per-run ephemeral volumes for the big writable dirs. These hold the
# Go module cache + any DOWNLOADED TOOLCHAINS (one per distinct go.mod version across
# your repos, ~350-500MB extracted each) + all cloned repos. They must NOT be RAM-backed
# tmpfs: several toolchains + several repos would blow the --memory limit and OOM-kill.
HOMEVOL="agent-home-$$"     # /home/agent  -> GOMODCACHE, GOCACHE, .claude
WORKVOL="agent-work-$$"     # /workspace   -> all cloned repos

cleanup() { podman rm -f "$AGENT" "$PROXY" >/dev/null 2>&1 || true
            podman network rm "$NET" >/dev/null 2>&1 || true
            podman volume rm "$HOMEVOL" "$WORKVOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT

podman volume create "$HOMEVOL" >/dev/null
podman volume create "$WORKVOL" >/dev/null

# Validate that at least one auth method is configured
if [[ -z "${ANTHROPIC_API_KEY:-}" ]] && [[ "${CLAUDE_CODE_USE_VERTEX:-}" != "1" ]]; then
  echo "ERROR: no Claude auth configured." >&2
  echo "  Set ANTHROPIC_API_KEY for direct API access, or" >&2
  echo "  Set CLAUDE_CODE_USE_VERTEX=1 with VERTEXAI_PROJECT for Vertex AI." >&2
  exit 1
fi

# Validate required agent inputs
if [[ -z "${AGENT_REPOS:-}" ]] && [[ -z "${AGENT_CONTROL_REPO:-}" ]]; then
  echo "ERROR: no repositories specified." >&2
  echo "  Set AGENT_REPOS='host/owner/repo[@ref] ...' (space-separated), or" >&2
  echo "  Set AGENT_CONTROL_REPO to a git URL containing a repos.txt manifest." >&2
  exit 1
fi
if [[ -z "${AGENT_TASK:-}" ]] && [[ -z "${AGENT_TASK_FILE:-}" ]]; then
  echo "ERROR: no task specified." >&2
  echo "  Set AGENT_TASK='your prompt', or" >&2
  echo "  Set AGENT_TASK_FILE to a file containing the prompt." >&2
  exit 1
fi

# Vertex AI: read ADC credentials into an env var so entrypoint.py can write them
# to disk inside the container's writable home volume. Bind-mounting host files
# fails under SELinux (container_t cannot read user_home_t/user_tmp_t) and the
# :z/:Z relabel flags do not work reliably from toolbx via flatpak-spawn.
GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
ADC_JSON=""
if [[ "${CLAUDE_CODE_USE_VERTEX:-}" == "1" ]]; then
  if [[ ! -f "$GCLOUD_ADC" ]]; then
    echo "ERROR: Vertex AI auth requires ADC at $GCLOUD_ADC (run: gcloud auth application-default login)" >&2
    exit 1
  fi
  ADC_JSON="$(cat "$GCLOUD_ADC")"
fi

# 1) per-run network for the proxy + agent
podman network create "$NET" >/dev/null

# 2) egress policy proxy
podman run -d --name "$PROXY" \
  --network "$NET" \
  --cap-drop=ALL --security-opt no-new-privileges \
  -e EGRESS_PROFILE="$EGRESS_PROFILE" \
  "$PROXY_IMAGE" >/dev/null
sleep 1
PROXY_IP="$(podman inspect "$PROXY" \
  --format "{{ (index .NetworkSettings.Networks \"$NET\").IPAddress }}")"

if [[ -z "$PROXY_IP" ]]; then
  echo "ERROR: could not determine proxy IP address" >&2
  podman logs "$PROXY" >&2
  exit 1
fi

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
  -e HTTPS_PROXY="http://${PROXY_IP}:8080" \
  -e HTTP_PROXY="http://${PROXY_IP}:8080" \
  -e NO_PROXY="localhost,127.0.0.1" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  -e CLAUDE_CODE_USE_VERTEX="${CLAUDE_CODE_USE_VERTEX:-}" \
  -e ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-${VERTEXAI_PROJECT:-}}" \
  -e VERTEXAI_PROJECT="${VERTEXAI_PROJECT:-}" \
  -e VERTEXAI_LOCATION="${VERTEXAI_LOCATION:-global}" \
  -e CLOUD_ML_REGION="${CLOUD_ML_REGION:-${VERTEXAI_LOCATION:-global}}" \
  -e GOOGLE_APPLICATION_CREDENTIALS_JSON="${ADC_JSON}" \
  -e GH_TOKEN \
  -e GITLAB_TOKEN \
  -e AGENT_MODE="$AGENT_MODE" \
  -e AGENT_GOCACHE_SRC="${AGENT_GOCACHE_SRC:-/opt/go-cache}" \
  ${GOPRIVATE:+-e GOPRIVATE="$GOPRIVATE"} \
  -e AGENT_REPOS="${AGENT_REPOS:-}" \
  -e AGENT_TASK="${AGENT_TASK:-}" \
  ${AGENT_TASK_FILE:+-e AGENT_TASK_FILE="$AGENT_TASK_FILE"} \
  ${AGENT_CONTROL_REPO:+-e AGENT_CONTROL_REPO="$AGENT_CONTROL_REPO"} \
  ${AGENT_WARM_TOOLCHAINS:+-e AGENT_WARM_TOOLCHAINS="$AGENT_WARM_TOOLCHAINS"} \
  ${AGENT_WARM_MODCACHE:+-e AGENT_WARM_MODCACHE="$AGENT_WARM_MODCACHE"} \
  ${AGENT_GO_WORK:+-e AGENT_GO_WORK="$AGENT_GO_WORK"} \
  ${AGENT_INTERACTIVE:+-e AGENT_INTERACTIVE="$AGENT_INTERACTIVE"} \
  ${AGENT_CACHE_ONLY:+-e AGENT_CACHE_ONLY="$AGENT_CACHE_ONLY"} \
  "$IMAGE"
