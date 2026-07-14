#!/usr/bin/env bash
# make-offline-cache.sh  —  ADMIN tool (run on a trusted, ONLINE host).
#
# Toolchains alone can be baked with the Containerfile's GO_PREBAKE_TOOLCHAINS arg.
# Third-party *module deps* additionally need each repo's go.sum resolved, which needs
# the repos + network. This one-shot does that in a throwaway ONLINE container, snapshots
# the resulting GOMODCACHE, and layers it into a derived image the offline runs use.
#
# The result: `agent-runner:go-offline` — same hardened image, plus a /opt/go-cache
# containing every dependency + toolchain your listed repos need. Offline runs then need
# NO package/toolchain network at all.
#
# Usage:
#   GH_TOKEN=... GITLAB_TOKEN=... \
#   REPOS="github.com/your-org/repo-a github.com/your-org/repo-b" \
#   ./make-offline-cache.sh
set -euo pipefail

BASE_IMAGE="${BASE_IMAGE:-localhost/agent-runner:go}"
OUT_IMAGE="${OUT_IMAGE:-localhost/agent-runner:go-offline}"
REPOS="${REPOS:?set REPOS='host/owner/repo ...'}"
CVOL="offline-cache-build-$$"

cleanup() { podman rm -f "cachebuild-$$" >/dev/null 2>&1 || true
            podman volume rm "$CVOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT

podman volume create "$CVOL" >/dev/null

# 1) ONLINE builder: clone repos + warm toolchains + download all module deps into the
#    volume. AGENT_CACHE_ONLY makes bootstrap.py stop after warming (no Claude, no token).
podman run --rm --name "cachebuild-$$" \
  -e GH_TOKEN -e GITLAB_TOKEN \
  -e AGENT_MODE=online \
  -e AGENT_CACHE_ONLY=1 \
  -e AGENT_WARM_TOOLCHAINS=1 -e AGENT_WARM_MODCACHE=1 \
  -e AGENT_REPOS="$REPOS" \
  -e GOMODCACHE=/cache \
  -v "$CVOL:/cache" \
  "$BASE_IMAGE"

# 2) layer the populated cache into a derived, still-hardened image at /opt/go-cache
podman run --rm -v "$CVOL:/cache:ro" "$BASE_IMAGE" true   # ensure vol readable
ctr="$(buildah from "$BASE_IMAGE")"
buildah copy --chown 1001:1001 "$ctr" "$(podman volume inspect "$CVOL" -f '{{.Mountpoint}}')" /opt/go-cache
buildah commit "$ctr" "$OUT_IMAGE"
buildah rm "$ctr" >/dev/null

echo "built $OUT_IMAGE  (run offline with IMAGE=$OUT_IMAGE AGENT_MODE=offline-go ./run-podman.sh)"
