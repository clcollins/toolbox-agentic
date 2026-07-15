# Hardened Claude Code Agent Runner (Go)

A single container image that runs Claude Code autonomously across GitHub and GitLab
repositories — clone, push, open PRs/MRs, watch CI, run the Go toolchain — while being
maximally isolated: non-root, read-only rootfs, all capabilities dropped, SELinux and
seccomp enforcing, no host mounts, and egress restricted to an allow-listed policy proxy.

Auth is 12-factor: nothing is baked in. Scoped tokens arrive as environment variables,
the entrypoint wires them into `gh`, `glab`, and git, clones the repos, and only then
launches Claude. See [SECURITY.md](SECURITY.md) for the full security model.

## Quickstart

```bash
# 1. Build both images (agent-runner + egress-proxy)
make image-build-all

# 2. Run — Option A: Anthropic API key
ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
GH_TOKEN="$GH_TOKEN" \
AGENT_REPOS="github.com/your-org/your-repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  make run

# 2. Run — Option B: Vertex AI (GCP)
CLAUDE_CODE_USE_VERTEX=1 \
VERTEXAI_PROJECT="$VERTEXAI_PROJECT" \
VERTEXAI_LOCATION="$VERTEXAI_LOCATION" \
ANTHROPIC_VERTEX_PROJECT_ID="$ANTHROPIC_VERTEX_PROJECT_ID" \
CLOUD_ML_REGION="$CLOUD_ML_REGION" \
GH_TOKEN="$GH_TOKEN" \
AGENT_REPOS="github.com/your-org/your-repo" \
AGENT_TASK="Summarize the repo and propose next steps." \
  make run
```

Vertex AI requires Application Default Credentials on the host. Run
`gcloud auth application-default login` first — `scripts/run-podman.sh` reads the ADC file
and injects it into the container automatically.

For interactive mode (chat with Claude instead of fire-and-forget), use
`make run-interactive`.

All environment variables can also be set in a `.env` file at the repo root (the
Makefile `-include`s it automatically).

## Makefile Targets

Run `make help` for the full list. Key targets:

| Target | Description |
|---|---|
| `make image-build-all` | Build both agent-runner and egress-proxy images |
| `make run` | Run agent headless (requires `AGENT_TASK`, `AGENT_REPOS`, and auth env vars) |
| `make run-interactive` | Run agent interactively (chat with Claude) |
| `make run-offline` | Run agent in air-gapped `offline-go` mode |
| `make run-preflight` | Print config summary with redacted secrets, then exit |
| `make run-debug` | Drop into a bash shell inside the agent container |
| `make build-offline-cache` | Bake module + toolchain cache for offline runs |
| `make test` | Run all checks in a containerized CI environment |
| `make test-all` | Run all checks + build both images |
| `make clean` | Remove dangling agent volumes and networks |

By default the Makefile uses `podman`. Set `CONTAINER_SUBSYS=docker` to use Docker
instead.

## Debugging

`make run-preflight` runs all validation checks inside the container and prints a
config summary with redacted secrets, then exits. Use it to verify credentials,
writable paths, and binaries before spending tokens on a real run.

`make run-debug` drops you into a bash shell inside the hardened container with all
volumes and the egress proxy running. From there you can inspect the environment,
test credential helpers (`gh auth status`), and verify the baked config.

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  agent container (uid 1001, ro-rootfs,       │
   scoped        │  cap-drop ALL, seccomp, SELinux, no host fs, │
   tokens ──────▶│  no k8s API token)                          │
   (env/Secret)  │    entrypoint.py → git/gh/glab → clone →     │
                 │    claude --dangerously-skip-permissions     │
                 │        │ HTTPS_PROXY (only route out)         │
                 └────────┼─────────────────────────────────────┘
                          ▼
                 ┌──────────────────────┐   allow-listed hosts (all methods):
                 │  egress policy proxy  │──▶ github/gitlab/anthropic/go-proxy/registries
                 │  (policy.py)          │   everything else: CONNECT denied; HTTP GET/HEAD only
                 └──────────────────────┘
```

On **Kubernetes**, a `NetworkPolicy` permits egress only to DNS and the proxy (hard
enforcement). On **Podman**, `scripts/run-podman.sh` sets `HTTPS_PROXY`/`HTTP_PROXY` so all
standard HTTP clients route through the proxy. Rootful podman can use `--internal`
networks for hard isolation; rootless podman uses a standard bridge with proxy env vars.

## Environment Variables

### Authentication (one required)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Direct Anthropic API key |
| `CLAUDE_CODE_USE_VERTEX` | Set to `1` for Vertex AI auth |
| `VERTEXAI_PROJECT` | GCP project with Vertex Claude access |
| `VERTEXAI_LOCATION` | Vertex region (e.g. `global`, `us-east5`) |
| `ANTHROPIC_VERTEX_PROJECT_ID` | Same GCP project (required by Claude Code) |
| `CLOUD_ML_REGION` | Same as `VERTEXAI_LOCATION` (some SDKs read this) |

### Repositories

| Variable | Description |
|---|---|
| `AGENT_REPOS` | Space-separated list of `host/owner/repo[@ref]` specs |
| `AGENT_CONTROL_REPO` | Git URL of a control repo with `repos.txt` manifest |
| `GOPRIVATE` | Go private module pattern (e.g. `github.com/your-org/*`) |

### Task

| Variable | Description |
|---|---|
| `AGENT_TASK` | Task prompt for Claude (required unless `AGENT_TASK_FILE` is set) |
| `AGENT_TASK_FILE` | Path to a file containing the task prompt |

### Git Auth (optional, enables push/PR/MR)

| Variable | Description |
|---|---|
| `GH_TOKEN` | GitHub fine-grained PAT (Contents RW, Pull requests RW, Actions R) |
| `GITLAB_TOKEN` | GitLab PAT or group token (`write_repository`, `api` scopes) |
| `GITLAB_HOST` | Self-managed GitLab host (default: `gitlab.com`) |
| `GIT_AUTHOR_NAME` | Commit author name (default: `agent-bot`) |
| `GIT_AUTHOR_EMAIL` | Commit author email (default: `agent-bot@localhost`) |

### Behavior

| Variable | Description |
|---|---|
| `AGENT_MODE` | `online` (default) or `offline-go` (air-gapped) |
| `AGENT_INTERACTIVE` | Set to `1` for interactive Claude session |
| `AGENT_WARM_TOOLCHAINS` | Set to `1` to pre-download Go toolchains at bootstrap |
| `AGENT_WARM_MODCACHE` | Set to `1` to run `go mod download` per module at bootstrap |
| `AGENT_GO_WORK` | Set to `1` to create `go.work` spanning all cloned modules |
| `AGENT_CACHE_ONLY` | Set to `1` to warm caches and exit (no Claude launch) |

## Multiple Repos Per Run

List repos in `AGENT_REPOS` (space-separated, each `host/owner/repo[@ref]`):

```bash
AGENT_REPOS="github.com/org/repo-a github.com/org/repo-b@v2.0 gitlab.com/group/project"
```

Or point `AGENT_CONTROL_REPO` at a repo containing a `repos.txt` manifest (one spec
per line, `#` comments allowed). The control repo can also contain a `.claude/` directory
that overlays onto the agent's Claude config.

Repos are cloned in parallel into `/workspace/<host__owner__repo>/`. The `agent-*`
helpers operate on the current directory, so Claude `cd`s into a repo, works, and opens
that repo's PR/MR — one run can touch several repos.

For interdependent repos, set `AGENT_GO_WORK=1` — bootstrap creates a `go.work` so
cross-repo changes resolve locally without `replace` directives.

## Go Version Handling

The image bundles Go 1.26.5 but sets `GOTOOLCHAIN=auto`. When Claude runs `go build` or
`go test` inside a repo whose `go.mod` requires a different version, the `go` command
transparently downloads and runs that exact toolchain from `proxy.golang.org`, verified
via `sum.golang.org` (both on the egress allow-list).

`entrypoint.py` reads every repo's `go.mod` and writes a per-repo Go-version table into
`WORKSPACE.md` before Claude starts.

Set `AGENT_WARM_TOOLCHAINS=1` to pre-download each repo's toolchain during bootstrap
(zero model tokens; surfaces version issues early).

## Air-Gapped Mode (offline-go)

| | `online` (default) | `offline-go` |
|---|---|---|
| Go toolchains | Fetched on demand via `proxy.golang.org` | Pre-baked in the image |
| Module deps | Fetched from `proxy.golang.org` + `sum.golang.org` | Pre-seeded cache; `GOPROXY=off` |
| Egress allow-list | Git + Anthropic + Go proxy + registries | Git + Anthropic only |
| Missing dep | Downloaded transparently | Fails loudly |

**Provision the cache:**

```bash
# Toolchains only (at build time):
GO_PREBAKE_TOOLCHAINS="go1.24.3 go1.25.7 go1.26.5" make image-build

# Toolchains + module deps (one-shot admin tool):
GH_TOKEN="$GH_TOKEN" \
REPOS="github.com/org/repo-a github.com/org/repo-b" \
  make build-offline-cache    # -> localhost/agent-runner:go-offline
```

**Run offline:**

```bash
AGENT_REPOS="github.com/org/repo-a" \
AGENT_TASK="Fix the failing test; open a PR." \
  make run-offline
```

## Kubernetes Deployment

```bash
# Create namespace and network policy
kubectl create ns agents
kubectl -n agents apply -f k8s/networkpolicy.yaml

# Deploy the egress proxy (label: app=agent-egress-proxy, port 8080)
# (bring your own Deployment/Service for egress-proxy/policy.py)

# Create a scoped Secret per run
kubectl -n agents create secret generic agent-tokens-run1 \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=GH_TOKEN="$GH_TOKEN"

# Launch the Job (self-destructs after ttlSecondsAfterFinished)
sed 's/EXAMPLE/run1/' k8s/job.yaml | kubectl -n agents apply -f -
```

See `k8s/secret.example.yaml` for the full Secret template including Vertex AI fields.
See `k8s/job-offline.patch.yaml` for the air-gapped overlay.

Each Job is independent and shares no state. Scope a separate token per run for
blast-radius containment and clean audit/revocation.

## Multi-Arch

The Containerfile is arch-aware (`TARGETARCH`). Build a manifest list for mixed clusters:

```bash
make image-build-multi-all
```

Supply both `GO_SHA256_AMD64` and `GO_SHA256_ARM64` build args with the correct checksums
from [go.dev/dl](https://go.dev/dl/).

## Deterministic Helpers

Pre-approved in `settings.json` so Claude runs them without permission prompts:

| Command | Description |
|---|---|
| `agent-clone <host/owner/repo[@ref]>` | Clone a repo with credentials wired |
| `agent-open-pr "title" ["body"]` | Push branch + open GitHub PR with attribution |
| `agent-open-mr "title" ["body"]` | Push branch + open GitLab MR with attribution |
| `agent-ci-watch [github\|gitlab]` | Poll CI to completion for the current branch |

A `gofmt` PostToolUse hook automatically formats `.go` files on edit — no model turn
needed.

## File Map

```
Makefile                        build, run, test, and CI targets (run `make help`)
Containerfile                   UBI 9 image: Go + gh + glab + claude-code + python, non-root
entrypoint.py                   entrypoint: preflight -> wire auth -> clone -> orient -> exec claude
bin/agent-clone                 deterministic helpers (pre-approved in settings.json)
bin/agent-open-pr
bin/agent-open-mr
bin/agent-ci-watch
ci/Containerfile                CI test container image
ci/scripts/                     CI validation scripts
agent-config/settings.json      permission allow/deny, gofmt hook, env overrides
agent-config/CLAUDE.md          operating instructions baked into the agent
egress-proxy/policy.py          stdlib-only egress proxy: host allow-list + method enforcement
egress-proxy/Containerfile      proxy image (UBI 9 minimal + python3)
k8s/job.yaml                    ephemeral Job, hardened securityContext, self-destructs
k8s/networkpolicy.yaml          default-deny egress except DNS + the proxy
k8s/secret.example.yaml         per-run scoped tokens template
k8s/job-offline.patch.yaml      overlay for air-gapped (offline-go) mode
scripts/run-podman.sh           host runner: per-run network, proxy, hardened agent container
scripts/make-offline-cache.sh   admin tool: bake module+toolchain cache for offline runs
```

## Verify Before Building

Pinned versions move fast. Before building, confirm:

- **Go**: check [go.dev/dl](https://go.dev/dl/) for the latest stable and update the
  `GO_VERSION` and `GO_SHA256_*` args in the Containerfile
- **Claude Code**: installed from the
  [signed dnf repo](https://code.claude.com/docs/en/setup); confirm GPG fingerprint
  `31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE`
- **glab**: confirm the current release at
  [gitlab.com/gitlab-org/cli/-/releases](https://gitlab.com/gitlab-org/cli/-/releases)
  and update `GLAB_VERSION` in the Containerfile
