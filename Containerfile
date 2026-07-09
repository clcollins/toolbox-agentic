# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Hardened, ephemeral Claude Code agent runner for Go development.
# Base: RHEL UBI 9 (glibc 2.34 -> Claude Code glibc native binary works).
#
# MULTI-ARCH: your ThinkPad host is amd64, your kubeadm nodes (Pi 4B/5) are arm64.
# Build a manifest list so the same tag runs both places:
#   podman build --platform linux/amd64,linux/arm64 --manifest agent-runner:go .
#
# MULTI-VERSION Go: GOTOOLCHAIN=auto (below) makes `go` fetch whatever version each
# repo's go.mod/toolchain line requires, on demand, from proxy.golang.org (allow-listed),
# verified via sum.golang.org. Downloaded toolchains cache in GOMODCACHE (writable vol).
#
# Verify pinned versions/hashes before building — this space moves weekly.
#   Go:          https://go.dev/dl/            (1.26.5 = latest stable, 2026-07-07)
#   Claude Code: https://code.claude.com/docs/en/setup  (signed dnf repo; multi-arch)
#   glab:        https://gitlab.com/gitlab-org/cli/-/releases
#   gh:          https://cli.github.com/packages/rpm/gh-cli.repo
# ---------------------------------------------------------------------------

FROM registry.access.redhat.com/ubi9/ubi:9.6 AS build

ARG TARGETARCH                      # populated by buildkit: amd64 | arm64
ARG GO_VERSION=1.26.5
# per-arch checksums from https://go.dev/dl/ (both required for multi-arch builds)
ARG GO_SHA256_AMD64=5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053
ARG GO_SHA256_ARM64=fe4789e92b1f33358680864bbe8704289e7bb5fc207d80623c308935bd696d49
ARG GLAB_VERSION=1.107.0

RUN set -eux; \
    dnf -y install --setopt=install_weak_deps=False --nodocs \
        curl-minimal ca-certificates tar gzip git; \
    case "${TARGETARCH}" in \
      amd64) GO_SHA256="${GO_SHA256_AMD64}";; \
      arm64) GO_SHA256="${GO_SHA256_ARM64}";; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1;; \
    esac; \
    # Go (pinned + per-arch sha256)
    curl -fsSLo /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${TARGETARCH}.tar.gz"; \
    echo "${GO_SHA256}  /tmp/go.tgz" | sha256sum -c -; \
    tar -C /usr/local -xzf /tmp/go.tgz; rm -f /tmp/go.tgz; \
    # glab (GitLab CLI) release binary + checksum, arch-aware
    curl -fsSLo /tmp/glab.tgz \
      "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_${TARGETARCH}.tar.gz"; \
    curl -fsSLo /tmp/glab.sums \
      "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/checksums.txt"; \
    ( cd /tmp && grep "linux_${TARGETARCH}.tar.gz" glab.sums | sha256sum -c - ); \
    tar -C /tmp -xzf /tmp/glab.tgz; install -m0755 /tmp/bin/glab /usr/local/bin/glab; \
    rm -rf /tmp/glab*

# ---------------------------------------------------------------------------
FROM registry.access.redhat.com/ubi9/ubi:9.6

LABEL org.opencontainers.image.title="claude-agent-runner" \
      org.opencontainers.image.description="Hardened ephemeral Claude Code Go agent (multi-arch, multi-Go)" \
      org.opencontainers.image.base.name="registry.access.redhat.com/ubi9/ubi:9.6"

# --- add signed third-party repos (GitHub CLI + Claude Code) ---
RUN set -eux; \
    dnf -y install --nodocs curl-minimal 'dnf-command(config-manager)'; \
    dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo; \
    printf '%s\n' \
      '[claude-code]' \
      'name=Claude Code' \
      'baseurl=https://downloads.claude.ai/claude-code/rpm/stable' \
      'enabled=1' \
      'gpgcheck=1' \
      'gpgkey=https://downloads.claude.ai/keys/claude-code.asc' \
      > /etc/yum.repos.d/claude-code.repo

# --- runtime deps (gpgcheck verifies the Claude Code signing key on install) ---
RUN set -eux; \
    dnf -y install --setopt=install_weak_deps=False --nodocs \
        git openssh-clients ca-certificates \
        gh claude-code \
        python3 python3-pip \
        jq ripgrep make gcc findutils which tar gzip diffutils; \
    dnf clean all; rm -rf /var/cache/dnf

# --- Go toolchain + glab from the build stage ---
COPY --from=build /usr/local/go /usr/local/go
COPY --from=build /usr/local/bin/glab /usr/local/bin/glab

# --- non-root user (autonomy + --dangerously-skip-permissions require non-root) ---
RUN set -eux; \
    groupadd -g 1001 agent; \
    useradd -u 1001 -g 1001 -m -d /home/agent -s /bin/bash agent; \
    mkdir -p /workspace /opt/agent && chown -R 1001:1001 /workspace /home/agent /opt/agent

# --- OPTIONAL: prebake Go toolchains for the air-gapped (offline-go) invocation ---
# Space-separated versions your repos pin, e.g. "go1.24.3 go1.25.7 go1.26.5".
# Baked into /opt/go-cache (a GOMODCACHE-shaped dir NOT masked by the runtime volume);
# bootstrap.py seeds it into the writable GOMODCACHE when AGENT_MODE=offline-go.
# Empty by default so online builds pay no size cost.
ARG GO_PREBAKE_TOOLCHAINS=""
RUN set -eux; \
    if [ -n "${GO_PREBAKE_TOOLCHAINS}" ]; then \
      mkdir -p /opt/go-cache; \
      for v in ${GO_PREBAKE_TOOLCHAINS}; do \
        echo "prebaking Go toolchain ${v}"; \
        GOMODCACHE=/opt/go-cache GOTOOLCHAIN="${v}" GOPATH=/tmp/gp \
          /usr/local/go/bin/go version; \
      done; \
      chown -R 1001:1001 /opt/go-cache; rm -rf /tmp/gp; \
    fi

# --- baked-in agent assets (config, skills, deterministic helper scripts) ---
COPY --chown=1001:1001 claude/  /opt/agent/claude-config/
COPY --chown=0:0       bin/     /usr/local/bin/
COPY --chown=1001:1001 bootstrap.py /opt/agent/bootstrap.py
RUN chmod 0755 /usr/local/bin/agent-* /opt/agent/bootstrap.py

# --- environment ---
ENV PATH="/usr/local/go/bin:/home/agent/.local/bin:/usr/local/bin:${PATH}" \
    # AUTO = per-repo Go version resolution (download+verify via proxy/sumdb).
    # Use "go1.26.5+auto" instead if you want a hard *minimum* vetted toolchain
    # (never runs a Go older than 1.26.5, still upgrades when a repo requires newer).
    GOTOOLCHAIN=auto \
    GOCACHE=/home/agent/.cache/go-build \
    GOMODCACHE=/home/agent/go/pkg/mod \
    HOME=/home/agent \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_UPDATES=1 \
    CLAUDE_CONFIG_DIR=/home/agent/.claude

USER 1001
WORKDIR /workspace

ENTRYPOINT ["/usr/bin/python3", "/opt/agent/bootstrap.py"]
