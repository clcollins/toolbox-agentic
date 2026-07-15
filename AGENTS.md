# AGENTS.md

## Project Overview

Hardened, ephemeral Claude Code agent runner for Go development. A single container
image that runs Claude Code autonomously across GitHub and GitLab repositories — clone,
push, open PRs/MRs, watch CI, run the Go toolchain — while being maximally isolated
from the host.

## Architecture

The repo produces one container image plus supporting manifests for Podman and
Kubernetes deployment.

```
Containerfile              UBI 9 multi-arch image (amd64/arm64)
entrypoint.py              Python entrypoint (runs before Claude, zero model tokens)
bin/                        Deterministic shell helpers (pre-approved in settings.json)
ci/                         CI test container and validation scripts
agent-config/               Baked-in Claude Code config (settings.json, CLAUDE.md)
egress-proxy/               stdlib-only egress policy proxy for host+method allow-listing
k8s/                        Kubernetes Job, NetworkPolicy, Secret template
scripts/run-podman.sh       Host runner with per-run network and proxy enforcement
scripts/make-offline-cache.sh  Admin tool: bake module+toolchain cache for offline runs
```

## Languages and Tools

- **Container image**: RHEL UBI 9 base, Containerfile (Podman, not Docker)
- **Entrypoint**: Python 3 (`entrypoint.py`) — no external dependencies
- **Helper scripts**: Bash (`bin/agent-*`)
- **Proxy policy**: Python 3 (`egress-proxy/policy.py`) — stdlib-only egress proxy
- **Build system**: GNU Make + Podman multi-arch builds (see `make help`)
- **Target workloads**: Go development (Go 1.26.5 bundled, GOTOOLCHAIN=auto)
- **Orchestration**: Kubernetes (kubeadm) and Podman on Fedora

## Key Conventions

- **Podman, not Docker** — all container commands use `podman`/`buildah`
- **Non-root** — container runs as uid 1001 (`agent` user)
- **Read-only rootfs** — writable state on ephemeral disk-backed volumes only
- **12-factor auth** — nothing baked in; all credentials via env vars or K8s Secrets
- **No host mounts** — repos are cloned into ephemeral volumes at runtime
- **Security-first** — cap-drop ALL, no-new-privileges, seccomp, SELinux enforcing

## Claude Config: Repo vs. Baked Image

This repo has two separate sets of Claude instructions for two different audiences:

**Repo-level** (for developers working on toolbox-agentic itself):
- `CLAUDE.md` — imports `@AGENTS.md`
- `AGENTS.md` — this file; project overview, conventions, file descriptions

**Baked into the container image** (for the agent running inside the container
against target repos):
- `agent-config/CLAUDE.md` — imports `@AGENTS.md`
- `agent-config/AGENTS.md` — runtime instructions: workspace paths, push/PR
  policy, helper usage, Go development, commit conventions, security boundaries

The `agent-config/` files are copied into the image at build time and seeded into
`~/.claude/` by `entrypoint.py` before Claude launches. The `@AGENTS.md` import in
each `CLAUDE.md` resolves to the `AGENTS.md` in the same directory.

## File Descriptions

### `Containerfile`
Multi-stage UBI 9 build. Installs Go (upstream tarball, per-arch checksums), gh (RPM
repo), glab (release binary, checksum-verified), claude-code (signed dnf repo), Python,
and build tools. Creates non-root user, optionally pre-bakes Go toolchains.

### `entrypoint.py`
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
commands. Pre-approved in `agent-config/settings.json` so Claude runs them without prompts:
- `agent-clone` — clone with wired credentials
- `agent-open-pr` — push + open GitHub PR with attribution footer
- `agent-open-mr` — push + open GitLab MR with attribution footer
- `agent-ci-watch` — poll CI to completion (GitHub Actions or GitLab pipeline)

### `agent-config/settings.json`
Claude Code permission config: allow-list for agent-* helpers and Go/git tooling,
deny-list for credential paths, PostToolUse gofmt hook, env vars to disable
auto-updates and telemetry.

### `agent-config/AGENTS.md`
Operating instructions baked into the agent: workspace orientation, push/PR policy,
helper usage, Go development, commit trailer convention (`Co-Authored-By`), security
boundaries.

### `agent-config/CLAUDE.md`
Imports `@AGENTS.md` so Claude Code loads the agent-config AGENTS.md at runtime.

### `egress-proxy/policy.py`
Stdlib-only HTTP proxy implementing two traffic classes:
- Trusted hosts (GitHub, GitLab, Anthropic, Go proxy): all methods allowed (CONNECT tunnel)
- Everything else: CONNECT denied; plain HTTP GET/HEAD only
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

### `scripts/run-podman.sh`
Host runner script. Creates a per-run Podman network, starts the proxy, and runs the
agent with HTTPS_PROXY/HTTP_PROXY pointing at the proxy. Cleanup trap removes all
resources on exit.

### `scripts/make-offline-cache.sh`
Admin tool for building the offline Go cache image. Runs the agent in cache-only mode,
then layers the populated GOMODCACHE into a derived `:go-offline` image.

## Environment Variables

Claude auth (one of):
- `ANTHROPIC_API_KEY` — direct API key
- `CLAUDE_CODE_USE_VERTEX=1` + `VERTEXAI_PROJECT` + `VERTEXAI_LOCATION` + GCP ADC — Vertex AI

Required for agent runs:
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

Verification is fully containerized — `make test` runs identically on a developer
laptop and in GitHub Actions. No test relies on host-installed tools or CI runner
preinstalled software.

- **`make test`** (alias: `make ci-all`) — single entry point for all validation
- **`make ci-build`** — builds the CI test container (`ci/Containerfile`)
- **`make ci-checks`** — runs inside the CI container (called by `ci-all`)
- **`make test-all`** — validation + full image builds
- **`make image-build-all`** — proves both Containerfiles build

Individual check targets (all run inside the CI container):

| Target | What it checks |
|---|---|
| `lint-python` | `py_compile` + `ruff check` + `ruff format --check` |
| `lint-shell` | `shellcheck` + `bash -n` on all shell scripts |
| `lint-container` | `hadolint` on both Containerfiles |
| `lint-json` | `jq` validation of `agent-config/settings.json` |
| `lint-yaml` | `yamllint` on `k8s/` manifests |
| `validate-containerfile` | Base image tag/registry validation |
| `test-python` | pytest unit tests (`entrypoint.py`, `policy.py`) |
| `test-integration` | pytest integration tests (real proxy server) |
| `test-shell` | bats tests for `bin/agent-*` scripts |
| `test-security` | Security contract and cross-file consistency tests |
| `docs-check` | Verifies `docs/plans/` exists |

### Adding new tests

All CI tests MUST be containerized. When adding a new check:
1. Add required tools to `ci/Containerfile` (pinned versions)
2. Add a `lint-*`, `validate-*`, or `test-*` Makefile target
3. Add the target to the `ci-checks` dependency list
4. The GitHub Actions workflow picks it up automatically (it calls `make ci-checks`)

Never add a test that runs directly on the CI runner or requires host-installed
tools. If `make test` passes locally, it passes in GitHub Actions. Any divergence
is a bug.

## Phase 2 (Planned, Not Implemented)

Read-only shared knowledge store via mnemo MCP server (Go, SQLite + sqlite-vec,
Ollama embeddings). Agents only read; an external control plane updates live.
See README.md for the stub.
