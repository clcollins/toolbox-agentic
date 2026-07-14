# Security Model

This document describes the security architecture of the hardened Claude Code agent
runner. The goal is defense-in-depth: every layer assumes the layer above it might be
compromised.

## Threat Model

The agent runs Claude Code with `--dangerously-skip-permissions`, giving it unrestricted
tool access inside the container. This is the right call because the container and network
boundaries are the real security perimeter — not Claude's permission system, which can be
bypassed by a `bash curl` command. The threats we defend against:

| Threat | Defense |
|---|---|
| Claude executes malicious code | Container isolation: non-root, read-only rootfs, dropped caps, seccomp, SELinux |
| Credential exfiltration via network | Egress proxy: only allow-listed hosts reachable, SSRF to private IPs blocked |
| Credential exfiltration via filesystem | No host mounts, credential paths denied in settings.json, ADC scrubbed from env after materialization |
| Host/container escape | cap-drop ALL, no-new-privileges, seccomp RuntimeDefault, SELinux enforcing, no privileged operations |
| Kubernetes API access | `automountServiceAccountToken: false`, NetworkPolicy blocks all egress except DNS + proxy |
| Lateral movement | Per-run scoped tokens, ephemeral containers, self-destructing Jobs |

## Container Hardening

Every container runs with maximum restriction:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1001
  runAsGroup: 1001
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: [ALL]
  seccompProfile:
    type: RuntimeDefault
```

**Non-root (uid 1001)**: The `agent` user has no elevated privileges. The image creates
this user explicitly — there is no root password, no sudo, no setuid binaries.

**Read-only rootfs**: Only three paths are writable, all on ephemeral storage:
- `/home/agent` — Go caches, Claude config (emptyDir / named volume)
- `/workspace` — cloned repos (emptyDir / named volume)
- `/tmp` — build artifacts (tmpfs, memory-backed, 1Gi limit)

**Capabilities**: All Linux capabilities are dropped. The agent needs none of them.

**seccomp**: RuntimeDefault profile blocks dangerous syscalls (ptrace, mount, reboot,
etc.) while allowing normal Go/Python/git operations.

**SELinux**: The container runs with `container_t` SELinux type. On Podman,
`--security-opt label=type:container_t` is set explicitly. This prevents the container
from accessing host files even if a mount somehow occurred — MAC enforcement is
independent of Unix permissions.

**No host mounts**: Zero bind-mounts from the host filesystem. All inputs (credentials,
task, repo list) arrive as environment variables. All writable storage is on ephemeral
volumes that are destroyed when the container exits.

**Resource limits** (Podman defaults in `scripts/run-podman.sh`):
- Memory: 8Gi (no swap)
- CPUs: 4
- PIDs: 512
- Ephemeral storage: sized per volume (home 6Gi, workspace 10Gi, tmp 1Gi)

## Network Isolation

### Egress Proxy

All outbound traffic routes through a stdlib-only HTTP proxy (`egress-proxy/policy.py`)
that enforces two traffic classes:

**Trusted hosts** (GitHub, GitLab, Anthropic, Go proxy, package registries):
- HTTPS: CONNECT tunnel allowed (blind TCP relay, no MITM)
- HTTP: all methods forwarded

**Everything else**:
- HTTPS: CONNECT denied (403) — untrusted HTTPS hosts are completely unreachable
- HTTP: GET and HEAD only (405 for POST, PUT, DELETE, etc.)

This is stricter than MITM-and-inspect: untrusted HTTPS hosts are blocked entirely, not
just read-only.

### SSRF Protection

The proxy blocks requests to private and internal IP addresses (RFC 1918, RFC 6598,
loopback, link-local) in both CONNECT and HTTP forwarding paths. This prevents the
agent from scanning internal services, cloud metadata endpoints (169.254.169.254), or
the Kubernetes API.

### Input Validation

The proxy validates all client input before processing:
- Malformed CONNECT targets (non-numeric port) return 400
- Malformed Content-Length headers return 400
- These prevent thread crashes from unexpected input

### Kubernetes NetworkPolicy

`k8s/networkpolicy.yaml` implements default-deny egress for agent pods. Only two
destinations are permitted:
- DNS (port 53) for name resolution
- The egress proxy pod (port 8080, matched by label `app: agent-egress-proxy`)

All other egress is blocked at the CNI level — this is hard enforcement that cannot be
bypassed by ignoring proxy environment variables.

### Podman Network Model

On Podman, `scripts/run-podman.sh` creates a per-run network and sets `HTTPS_PROXY`/`HTTP_PROXY`
environment variables. Standard HTTP clients (git, curl, Go toolchain, Claude Code)
respect these and route through the proxy. Rootful podman can additionally use
`--internal` networks to remove the default gateway entirely; rootless podman uses a
standard bridge because DNS resolution doesn't work across internal and external networks
in rootless mode.

## Credential Handling

### 12-Factor Auth

No credentials are baked into the container image. All authentication arrives at runtime
via environment variables (Podman) or Kubernetes Secrets:

- `ANTHROPIC_API_KEY` or Vertex AI env vars — Claude auth
- `GH_TOKEN` — GitHub fine-grained PAT
- `GITLAB_TOKEN` — GitLab PAT or group token

`entrypoint.py` wires these into the appropriate credential helpers (`gh auth setup-git`,
`glab auth git-credential`) before cloning, so git operations use scoped tokens without
the tokens appearing in clone URLs or git config.

### Scoped Tokens

Each run should use a **separate, minimally-scoped token** for blast-radius containment:
- GitHub: fine-grained PAT with Contents RW, Pull requests RW, Actions R — scoped to
  the specific repositories the agent will work on
- GitLab: project/group token with `write_repository` and `api` scopes
- Anthropic: standard API key or Vertex AI project credentials

If a token is compromised, only one run's repositories are affected, and the token can be
revoked without impacting other runs.

### ADC Credential Injection (Vertex AI)

For Vertex AI authentication, GCP Application Default Credentials must reach the
container. Bind-mounting host files fails under SELinux because the container process
(`container_t`) cannot read host files (`user_home_t` / `user_tmp_t`).

The solution: `scripts/run-podman.sh` reads the ADC JSON with `cat` and passes it as the
`GOOGLE_APPLICATION_CREDENTIALS_JSON` environment variable. Inside the container,
`entrypoint.py`'s `materialize_adc()` writes it to the writable home volume (which has
`container_file_t` SELinux context), sets `GOOGLE_APPLICATION_CREDENTIALS` to point at
the written file, and **deletes the env var** to limit the exposure window.

On Kubernetes, the same env var can be populated from a Secret, or ADC can be mounted as
a Secret volume directly (K8s Secrets get correct SELinux contexts automatically).

### Credential Path Deny-List

`claude/settings.json` denies Claude read and write access to credential directories:

```json
"deny": [
  "Read(~/.ssh/**)", "Read(~/.aws/**)",
  "Read(~/.config/gh/**)", "Read(~/.config/glab-cli/**)",
  "Read(**/.env)",
  "Edit(~/.ssh/**)", "Edit(~/.aws/**)",
  "Edit(~/.config/gh/**)", "Edit(~/.config/glab-cli/**)",
  "Edit(**/.env)"
]
```

This is defense-in-depth — the credentials at these paths are injected by `entrypoint.py`
for git operations, but Claude should never read or modify them directly.

## Claude Code Permissions

### --dangerously-skip-permissions

Claude Code runs with `--dangerously-skip-permissions`, which disables the interactive
permission prompt. This sounds alarming, but it is the correct choice in this context:

1. The container **is** the sandbox. Every dangerous operation (network access, file
   writes, process execution) is already constrained by the container, seccomp, SELinux,
   and the egress proxy.
2. Permission prompts don't work in headless mode — there's no human to approve them.
3. The `settings.json` allow-list still governs which commands run without prompts in
   interactive mode, and the deny-list still blocks credential file access.

### settings.json Allow-List

Pre-approved commands that Claude can run without prompts:

```
agent-clone, agent-open-pr, agent-open-mr, agent-ci-watch
go, gofmt, git, gh, glab, make, jq, rg, grep, find
ls, cat, head, tail, wc, diff, sort, uniq
mkdir, cp, mv, touch, chmod, tar, gzip
```

### Hooks

A `gofmt` PostToolUse hook automatically formats `.go` files whenever Claude edits one —
no model turn needed, consistent formatting enforced.

### Disabled Features

```json
"env": {
  "DISABLE_AUTOUPDATER": "1",
  "DISABLE_UPDATES": "1",
  "DISABLE_TELEMETRY": "1"
}
```

Auto-updates are disabled (the image pins a specific version). Telemetry is disabled
(the container should not phone home beyond the allow-listed hosts).

## Comparison: Agent Runner vs Toolbx Container

| Property | Agent Runner | Toolbx |
|---|---|---|
| User | non-root uid 1001 | root → your host UID |
| Rootfs | `--read-only` + tmpfs/emptyDir | host `/` writable at `/run/host` |
| Host filesystem | none mounted | `/`, `$HOME`, `/dev` bind-mounted |
| Capabilities | `--cap-drop=ALL` | `--privileged` |
| SELinux | enforcing (`container_t`) | `label=disable` |
| seccomp | on (RuntimeDefault) | host/relaxed |
| Namespaces | own net/pid/ipc | host net/pid/ipc |
| K8s API | `automountServiceAccountToken: false` | n/a |
| Egress | single proxy, host+method policy | host network, unrestricted |
| Lifetime | ephemeral, self-destructs | persistent |

## Capability Matrix

What each feature requires to work:

| Capability | Token Scope | Egress Hosts | Config |
|---|---|---|---|
| GitHub clone/push | Contents RW, Metadata R | `github.com`, `codeload.github.com`, `objects.githubusercontent.com` | `gh auth setup-git` (bootstrap does it) |
| GitHub PRs | + Pull requests RW | `api.github.com`, `uploads.github.com` | `agent-open-pr` |
| Watch GitHub CI | + Actions R | `api.github.com` | `agent-ci-watch github` |
| GitLab clone/push | `write_repository` | `gitlab.com` (or your host) | glab credential helper (bootstrap) |
| GitLab MRs / CI | + `api` | `gitlab.com` | `agent-open-mr`, `agent-ci-watch gitlab` |
| Go build/test/vet | — | — | Go bundled; `/tmp` + GOMODCACHE exec-allowed |
| Per-repo Go version | — | `proxy.golang.org`, `sum.golang.org` | `GOTOOLCHAIN=auto` |
| Go module fetch | — (public) | `proxy.golang.org`, `sum.golang.org` | Private: set `GOPRIVATE` |
| Web research | — | any host (GET/HEAD only) | Enforced by proxy |
| Claude API | `ANTHROPIC_API_KEY` | `api.anthropic.com` | — |
| Vertex AI | GCP ADC + project vars | `oauth2.googleapis.com`, `*-aiplatform.googleapis.com` | ADC materialized by bootstrap |

## Stronger Isolation Options

The container already runs with dropped caps, no-new-privileges, seccomp, SELinux, and
its own namespaces. For additional isolation:

**gVisor**: Set `runtimeClassName: gvisor` in `job.yaml` for a separate application
kernel. Go workloads run fine under gVisor. This is the only defense against a container
kernel escape — the agent's syscalls hit gVisor's Sentry, not the host kernel.

**Kata Containers**: Set a Kata RuntimeClass for full microVM isolation. Each agent gets
its own lightweight VM with a separate kernel and memory space.

**Rootful Podman with --internal networks**: On rootful podman, `podman network create
--internal` removes the default gateway entirely, making the proxy physically the only
next hop (not just the configured one). This is stronger than proxy env vars, which a
process can choose to ignore.
