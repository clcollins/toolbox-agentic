# AGENTS.md

## Project Overview

Hardened, ephemeral Claude Code agent runner for Go development. A single container
image that runs Claude Code autonomously across GitHub and GitLab repositories — clone,
push, open PRs/MRs, watch CI, run the Go toolchain — while being maximally isolated
from the host.

## Architecture

The project lives under `agent-runner/` and produces one container image plus supporting
manifests for Podman and Kubernetes deployment.

```
agent-runner/
  Containerfile              UBI 9 multi-arch image (amd64/arm64)
  bootstrap.py               Python entrypoint (runs before Claude, zero model tokens)
  bin/                        Deterministic shell helpers (pre-approved in settings.json)
  claude/                     Baked-in Claude Code config (settings.json, CLAUDE.md)
  egress-proxy/               mitmproxy policy addon for host+method allow-listing
  k8s/                        Kubernetes Job, NetworkPolicy, Secret template
  run-podman.sh               Host runner with internal-network proxy enforcement
  make-offline-cache.sh       Admin tool: bake module+toolchain cache for offline runs
```

## Languages and Tools

- **Container image**: RHEL UBI 9 base, Containerfile (Podman, not Docker)
- **Entrypoint**: Python 3 (`bootstrap.py`) — no external dependencies
- **Helper scripts**: Bash (`bin/agent-*`)
- **Proxy policy**: Python 3 (`egress-proxy/policy.py`) — mitmproxy addon
- **Build system**: Podman multi-arch builds; no Makefile (image-only project)
- **Target workloads**: Go development (Go 1.26.5 bundled, GOTOOLCHAIN=auto)
- **Orchestration**: Kubernetes (kubeadm) and Podman on Fedora

## Key Conventions

- **Podman, not Docker** — all container commands use `podman`/`buildah`
- **Non-root** — container runs as uid 1001 (`agent` user)
- **Read-only rootfs** — writable state on ephemeral disk-backed volumes only
- **12-factor auth** — nothing baked in; all credentials via env vars or K8s Secrets
- **No host mounts** — repos are cloned into ephemeral volumes at runtime
- **Security-first** — cap-drop ALL, no-new-privileges, seccomp, SELinux enforcing

## File Descriptions

### `Containerfile`
Multi-stage UBI 9 build. Installs Go (upstream tarball, per-arch checksums), gh (RPM
repo), glab (release binary, checksum-verified), claude-code (signed dnf repo), Python,
and build tools. Creates non-root user, optionally pre-bakes Go toolchains.

### `bootstrap.py`
Python entrypoint that runs before Claude launches (zero model tokens):
1. Preflight validation of required env vars
2. Seeds Claude config from baked path into runtime volume
3. Wires git identity and credential helpers (gh, glab)
4. Clones repos in parallel from AGENT_REPOS / AGENT_CONTROL_REPO
5. Detects per-repo Go versions, optionally warms toolchains/module cache
6. Writes WORKSPACE.md orientation document
7. `exec`s Claude Code

### `bin/agent-*`
Deterministic helper scripts that collapse multi-step operations into single
commands. Pre-approved in `claude/settings.json` so Claude runs them without prompts:
- `agent-clone` — clone with wired credentials
- `agent-open-pr` — push + open GitHub PR with attribution footer
- `agent-open-mr` — push + open GitLab MR with attribution footer
- `agent-ci-watch` — poll CI to completion (GitHub Actions or GitLab pipeline)

### `claude/settings.json`
Claude Code permission config: allow-list for agent-* helpers and Go/git tooling,
deny-list for credential paths, PostToolUse gofmt hook, env vars to disable
auto-updates and telemetry.

### `claude/CLAUDE.md`
Operating instructions baked into the agent: workspace orientation, helper usage,
commit trailer convention (`Co-Authored-By`), security boundaries.

### `egress-proxy/policy.py`
mitmproxy addon implementing two traffic classes:
- Trusted hosts (GitHub, GitLab, Anthropic, Go proxy): all methods allowed (TLS passthrough)
- Everything else: GET/HEAD only (TLS-terminated, method-inspected)
Supports offline-go profile that strips package/toolchain hosts from trusted set.

### `k8s/job.yaml`
Hardened Kubernetes Job template with full securityContext (readOnlyRootFilesystem,
runAsNonRoot, capabilities drop ALL, seccomp RuntimeDefault), disk-backed emptyDir
volumes, ttlSecondsAfterFinished for self-destruction.

### `k8s/networkpolicy.yaml`
Default-deny egress NetworkPolicy. Agent pods can reach only cluster DNS and the
egress proxy pod.

### `k8s/secret.example.yaml`
Template for per-run scoped tokens (ANTHROPIC_API_KEY, GH_TOKEN, GITLAB_TOKEN).

### `k8s/job-offline.patch.yaml`
Kustomize patch overlay to switch a Job to air-gapped (offline-go) mode.

### `run-podman.sh`
Host runner script. Creates an internal Podman network (no gateway), dual-homes the
proxy container, and runs the agent on the internal network only. Cleanup trap removes
all resources on exit.

### `make-offline-cache.sh`
Admin tool for building the offline Go cache image. Runs the agent in cache-only mode,
then layers the populated GOMODCACHE into a derived `:go-offline` image.

## Environment Variables

Required for agent runs:
- `ANTHROPIC_API_KEY` — Claude API key
- `AGENT_TASK` or `AGENT_TASK_FILE` — task prompt
- `AGENT_REPOS` or `AGENT_CONTROL_REPO` — repos to clone

Auth (optional, enables push/PR/MR):
- `GH_TOKEN` — GitHub fine-grained PAT
- `GITLAB_TOKEN` — GitLab token
- `GITLAB_HOST` — self-managed GitLab host (default: gitlab.com)

Behavior:
- `AGENT_MODE` — `online` (default) or `offline-go`
- `AGENT_WARM_TOOLCHAINS` — pre-download Go toolchains during bootstrap
- `AGENT_WARM_MODCACHE` — pre-download Go module dependencies
- `AGENT_GO_WORK` — create go.work spanning all modules
- `AGENT_INTERACTIVE` — run Claude interactively instead of headless
- `AGENT_CACHE_ONLY` — warm caches then exit (no Claude launch)

## Testing

No automated test suite — this is an infrastructure/packaging project. Verification:
1. `python3 -m py_compile agent-runner/bootstrap.py`
2. `bash -n` on all shell scripts in `agent-runner/bin/`
3. JSON validation of `agent-runner/claude/settings.json`
4. YAML validation of all `agent-runner/k8s/*.yaml`
5. Container build: `podman build -t agent-runner:go agent-runner/`

## Phase 2 (Planned, Not Implemented)

Read-only shared knowledge store via mnemo MCP server (Go, SQLite + sqlite-vec,
Ollama embeddings). Agents only read; an external control plane updates live.
See README.md for the stub.
