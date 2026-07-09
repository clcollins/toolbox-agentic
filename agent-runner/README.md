# Hardened, ephemeral Claude Code agent runner (Go dev)

A single container image you can launch **on-demand and N-at-a-time in parallel**, on
your host with Podman or on your kubeadm cluster, to let Claude Code work autonomously
across GitHub/GitLab repos — clone, push, open PRs/MRs, watch CI, run the Go toolchain,
fetch modules — while being **as segregated from everything else as a networked
container can be**: non-root, read-only rootfs, all capabilities dropped, SELinux/seccomp
on, no host mounts, no Kubernetes API access, and a single allow-listed egress path.

Auth is 12‑factor: **nothing is baked in and no host directory is mounted.** Scoped
tokens arrive as env / Secrets; the entrypoint wires them into `gh`, `glab`, and git,
clones the repos, and only then launches Claude.

*Built for Fedora 44 host / kubeadm homelab, July 2026. Verify pinned versions before building.*

## File map

```
Containerfile              UBI 9 image: Go 1.26.5 + gh + glab + claude-code + python, non-root
bootstrap.py               entrypoint: preflight -> wire auth -> clone -> orient -> exec claude
bin/agent-clone            deterministic helpers (pre-approved in settings.json so no prompts,
bin/agent-open-pr            and so Claude spends no tokens re-deriving rote steps)
bin/agent-open-mr
bin/agent-ci-watch
claude/settings.json       permission allow/deny, inner sandbox, gofmt hook
claude/CLAUDE.md           operating instructions baked into the agent
egress-proxy/policy.py     mitmproxy addon: host allow-list (all methods) + GET-only research
k8s/job.yaml               ephemeral Job, hardened securityContext, self-destructs
k8s/networkpolicy.yaml     default-deny egress except DNS + the proxy
k8s/secret.example.yaml    per-run scoped tokens
k8s/job-offline.patch.yaml overlay that switches a run to air-gapped (offline-go) mode
make-offline-cache.sh      ADMIN one-shot: bake a module+toolchain cache image for offline runs
run-podman.sh              host runner; internal network makes the proxy the only way out
```

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  agent pod/container (uid 1001, ro-rootfs,   │
   scoped        │  cap-drop ALL, seccomp, SELinux, no host fs, │
   tokens ──────▶│  no k8s API token)                          │
   (env/Secret)  │    bootstrap.py → git/gh/glab → clone →      │
                 │    claude --dangerously-skip-permissions     │
                 │        │ HTTPS_PROXY (only route out)         │
                 └────────┼─────────────────────────────────────┘
                          ▼
                 ┌──────────────────────┐   allow-listed hosts (all methods):
                 │  egress policy proxy  │──▶ github/gitlab/anthropic/go-proxy/registries
                 │  (policy.py)          │   everything else: GET/HEAD only (TLS-terminated)
                 └──────────────────────┘
```

The container never has a direct route to the internet. On **Kubernetes** a
`NetworkPolicy` permits egress only to DNS and the proxy. On **Podman**, `run-podman.sh`
puts the agent on a `--internal` network (no gateway) and dual-homes the proxy on that
network *and* the default bridge — so the proxy is physically the only next hop.

## Isolation properties (contrast with a Toolbx container, which is the inverse)

| Property | This runner | Toolbx |
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

## What you tweak to *allow* each capability (deny-all baseline → open only these)

Start from "nothing works," then open exactly what each function needs:

| Capability | Token / scope | Egress hosts to allow | Other |
|---|---|---|---|
| **GitHub clone/push** | fine-grained PAT or App token: **Contents RW**, Metadata R | `github.com`, `codeload.github.com`, `objects.githubusercontent.com` | `gh auth setup-git` (bootstrap does it) |
| **GitHub PRs** | + **Pull requests RW** | `api.github.com`, `uploads.github.com` | `agent-open-pr` |
| **Watch GitHub CI** | + **Actions: Read** | `api.github.com` | `agent-ci-watch github` |
| **GitLab clone/push** | PAT/group token: **write_repository** | `gitlab.com` (or your host) | glab credential helper (bootstrap) |
| **GitLab MRs / CI** | + **api** | `gitlab.com` | `agent-open-mr`, `agent-ci-watch gitlab` |
| **Go build/test/vet** | — | — | Go 1.26.5 bundled; `/tmp` + `GOMODCACHE` exec-allowed; writable `GOCACHE`/`GOMODCACHE` |
| **Per-repo Go version** | — | `proxy.golang.org`, `sum.golang.org` | `GOTOOLCHAIN=auto` auto-fetches each repo's required toolchain (same hosts as module fetch) |
| **Go module fetch** | — (public) | `proxy.golang.org`, `sum.golang.org` | private modules: set `GOPRIVATE` → resolved via git creds to the hosts already allowed above |
| **Web research** | — | any host, but **GET/HEAD only** (or set a research allow-list) | enforced by `policy.py` |
| **Claude itself** | `ANTHROPIC_API_KEY` (Console) | `api.anthropic.com` | `DISABLE_UPDATES=1` avoids needing `storage.googleapis.com` |

And the **hardening values you must relax just enough** for it to run at all:
`readOnlyRootFilesystem: true` requires writable **emptyDir/tmpfs** at `/home/agent`
(config + caches), `/workspace` (repos), `/tmp` (build/link), and the Go cache dirs live
under `/home/agent`. Everything else stays locked. seccomp `RuntimeDefault` is fine for the
Go toolchain. If you enable Claude Code's **inner** bubblewrap sandbox in an unprivileged
container, set `enableWeakerNestedSandbox: true` (already in `settings.json`) — the
container is the real boundary; the inner sandbox is defense-in-depth.

## Why GET-only is enforced at the proxy, not in Claude

A permission rule inside Claude can be sidestepped by a `bash` `curl -X POST`. The only
place a method restriction is a *hard* boundary is the network. `policy.py` TLS-terminates
non-allow-listed (research) traffic and rejects anything but GET/HEAD, while passing
trusted git/API hosts straight through (they legitimately need POST for push and PR/MR).
Trust the proxy's CA in the image (`SSL_CERT_FILE` / `NODE_EXTRA_CA_CERTS`) so intercepted
research TLS still validates.

## Multiple Go versions across repos

Your repos won't all target the same Go. The image bundles Go 1.26.5 but sets
**`GOTOOLCHAIN=auto`**, so when Claude runs `go build`/`go test` inside a repo whose
`go.mod` declares (say) `go 1.24` or `toolchain go1.27rc1`, the `go` command transparently
downloads and runs *that* exact toolchain. Toolchains are fetched as the `golang.org/toolchain`
module from `proxy.golang.org` and cryptographically verified via `sum.golang.org` — both
already on the egress allow-list — so no new holes are opened, and they're official signed
Go releases, not arbitrary binaries. Downloaded toolchains cache in `GOMODCACHE` (a writable
volume with `exec` allowed), which is why the home volume is sized at 6Gi.

`bootstrap.py` reads every repo's `go.mod` and prints a **per-repo Go-version table** into
`WORKSPACE.md` before Claude starts, so mismatches are visible immediately. Two knobs:

- `AGENT_WARM_TOOLCHAINS=1` — pre-download each repo's toolchain during bootstrap (zero
  model tokens; surfaces "this repo needs Go 1.27" before Claude spends a turn discovering it).
- **Security-hardening variant:** set `GOTOOLCHAIN=go1.26.5+auto` instead of `auto` to enforce
  a *minimum* vetted toolchain — Go will upgrade when a repo requires newer, but never run a
  version *older* than 1.26.5 (avoids silently building with an old Go that has known CVEs).

## Working several repos per run

Multiple clones per run is the normal case. List them all in `AGENT_REPOS`
(whitespace/newline separated, each `host/owner/repo[@ref]`), or point `AGENT_CONTROL_REPO`
at a repo containing a `repos.txt` manifest. `bootstrap.py` clones them in parallel
(deduped), each into `/workspace/<owner__repo>/`, with credentials wired for both GitHub
and GitLab in the same run. The `agent-*` helpers operate on the current directory, so
Claude `cd`s into a repo, works, and opens that repo's PR/MR — one run can touch several
repos and open several PRs/MRs.

If the repos are **interdependent** (you're editing a library and its consumer together),
set `AGENT_GO_WORK=1` and bootstrap runs `go work init` across all cloned modules so
cross-repo changes resolve locally without `replace` directives. It's **opt-in** because a
workspace forces every `go` command into workspace mode, which can mask real version or
dependency mismatches for repos that are actually independent — leave it off for unrelated
repos (disable per-command with `GOWORK=off`). Note that mixing wildly different Go versions
in one workspace can conflict; bootstrap warns and continues (repos still build individually)
rather than failing the run.

## Multi-arch (your host is amd64, your Pi cluster is arm64)

The Containerfile is arch-aware (`TARGETARCH`), and `GOTOOLCHAIN=auto` fetches
arch-matched toolchains. Build a manifest list so one tag runs both places:
`podman build --platform linux/amd64,linux/arm64 --manifest agent-runner:go .`
(Supply both `GO_SHA256_AMD64` and `GO_SHA256_ARM64`; confirm the Claude Code dnf repo and
glab both publish arm64 — they do as of this writing.)

## Deterministic bootstrapping = fewer tokens

Model tokens are spent only on the actual work, not the plumbing:

- **Pre-flight in `bootstrap.py`** (zero tokens): wire auth, clone all repos in parallel,
  optionally warm the module cache, and write `WORKSPACE.md` so Claude starts oriented.
- **`bin/agent-*` helpers**, pre-approved in `settings.json`, collapse multi-step
  operations (push + open PR + attribution footer; poll CI to green) into one command, so
  Claude issues a single call instead of composing and reasoning through each step.
- **A `gofmt` PostToolUse hook** formats Go files automatically on edit — no model turn.
- These map cleanly onto your existing skills (`ci-watch`, `pr-qualify`, `copilot-review`);
  bake them into `claude/` or pull them at bootstrap via `AGENT_CONTROL_REPO`.

Note: MCP tools still cost tokens per call/result — the real savings come from pre-work +
batched deterministic commands + hooks, not from wrapping everything in MCP.

## Kernel / stronger isolation options

- The container already runs with dropped caps, `no-new-privileges`, seccomp, SELinux, and
  its own namespaces. For a **separate kernel** (the only defense against a container/kernel
  escape), set `runtimeClassName: gvisor` (gVisor) or a Kata Containers RuntimeClass in
  `job.yaml`. Go workloads run fine under gVisor; Kata gives a full microVM boundary.

## Build & run

```bash
# 1) fill in the two REPLACE_ hashes in the Containerfile:
#    - go1.26.5.linux-amd64.tar.gz sha256  (from https://go.dev/dl/)
#    - confirm glab release version/asset  (https://gitlab.com/gitlab-org/cli/-/releases)
podman build -t agent-runner:go .

# 2a) host, one run (proxy + agent are wired for you):
export ANTHROPIC_API_KEY=... GH_TOKEN=... GITLAB_TOKEN=...
AGENT_REPOS="github.com/clcollins/srepd" \
AGENT_TASK="Implement issue #197; open a PR; watch CI to green." \
  ./run-podman.sh

# 2b) parallel on Kubernetes (each Job independent + self-destructing):
kubectl create ns agents
kubectl -n agents apply -f k8s/networkpolicy.yaml
# deploy the egress proxy (label app=agent-egress-proxy) from egress-proxy/policy.py
# per run: create a scoped Secret + a uniquely-named Job:
kubectl -n agents create secret generic agent-tokens-run1 \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=GH_TOKEN="$GH_TOKEN" --from-literal=GITLAB_TOKEN="$GITLAB_TOKEN"
sed 's/EXAMPLE/run1/' k8s/job.yaml | kubectl -n agents apply -f -
```

Run 1 or 50; they share no state. Scope a **separate token per run** for blast-radius
containment and clean audit/revocation.

## Invocation types: `online` (default) vs `offline-go` (air-gapped)

The runner takes an `AGENT_MODE`:

| | `online` (default) | `offline-go` (air-gapped) |
|---|---|---|
| Go toolchains | fetched on demand (`GOTOOLCHAIN=auto`) via `proxy.golang.org` | pre-baked in the image; **no** toolchain network |
| Module deps | fetched from `proxy.golang.org` + `sum.golang.org` | served from the pre-seeded module cache; `GOPROXY=off`, `GOSUMDB=off` |
| Egress allow-list | git + Anthropic + Go proxy + registries | git + Anthropic **only** (proxy strips package/toolchain hosts via `EGRESS_PROFILE=offline-go`) |
| Missing dep/version | downloaded transparently | **fails loudly** — surfaces gaps in your provisioning |
| Use when | day-to-day, evolving deps | reproducible/audited runs, restricted networks, supply-chain lockdown |

"Air-gapped" here means **no package or toolchain fetching** — git push/PR and the Claude
API are still reachable (that's the point of the agent). For a fully network-free run you'd
also pre-clone the repos and route Claude through an on-prem provider (Bedrock/Vertex); the
same `offline-go` plumbing applies, you just tighten the remaining two allow-listed hosts.

**How it works (three moving parts):**

1. **Provision the cache.** Toolchains alone bake deterministically at build time:
   ```bash
   podman build -t agent-runner:go \
     --build-arg GO_PREBAKE_TOOLCHAINS="go1.24.3 go1.25.7 go1.26.5" .
   ```
   For third-party **module deps** too, run the admin one-shot (needs an online host +
   your tokens once); it clones the repos, downloads every dep, and layers the populated
   cache into a derived image at `/opt/go-cache`:
   ```bash
   GH_TOKEN=... GITLAB_TOKEN=... \
   REPOS="github.com/clcollins/srepd github.com/clcollins/mnemo" \
     ./make-offline-cache.sh          # -> localhost/agent-runner:go-offline
   ```
2. **Seed at startup.** `bootstrap.py` copies `/opt/go-cache` (or `AGENT_GOCACHE_SRC`)
   into the writable `GOMODCACHE`, then sets `GOPROXY=off GOSUMDB=off`. Anything not in
   the cache errors instead of silently reaching out — that's the guarantee.
3. **Tighten egress.** The policy proxy runs with `EGRESS_PROFILE=offline-go`, which drops
   `proxy.golang.org`/`sum.golang.org`/registries from the trusted set, so even a stray
   `go get` or `curl` to a package host is refused at the network boundary.

**Run it:**

```bash
# Podman (offline):
IMAGE=localhost/agent-runner:go-offline AGENT_MODE=offline-go \
AGENT_REPOS="github.com/clcollins/srepd" \
AGENT_TASK="Fix the failing test; open a PR." \
  ./run-podman.sh

# Kubernetes (offline): build the image, set the proxy Deployment's env
# EGRESS_PROFILE=offline-go, then overlay the Job:
kubectl -n agents patch job/agent-run-run1 --patch-file k8s/job-offline.patch.yaml
```

Trade-off to keep in mind: `offline-go` makes runs reproducible and supply-chain-tight, but
you own cache freshness — when a repo bumps a dependency or its `go`/`toolchain` line, you
must re-provision the cache (re-run `make-offline-cache.sh` / add the version to
`GO_PREBAKE_TOOLCHAINS`) or that run will fail closed. That fail-closed behavior is the
feature, not a bug: nothing enters the build that you didn't vet.

## Verify before building (this ecosystem moves weekly)

- **Go**: 1.26.5 is current stable (2026‑07‑07). Pull the real `.tar.gz` sha256 from go.dev.
- **Claude Code**: installed from the signed dnf repo; confirm the GPG fingerprint
  `31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE`. UBI 9 is glibc, so the standard
  Linux binary applies (no musl handling needed).
- **glab**: confirm the current release tag/asset names; 1.47.0 (2026‑04‑28) was current.
- **`settings.json` `sandbox`/`hooks` schema** and the `CLAUDE_TOOL_FILE_PATH` hook var:
  confirm against the current docs; Claude Code's sandbox schema is still evolving.
- **mitmproxy** CA: the proxy writes its CA on first start; `run-podman.sh` copies it for
  the agent to trust. In K8s, publish it as the `agent-egress-ca` ConfigMap.

## Sources & attribution

- Claude Code install / signed dnf repo / GPG fingerprint / env flags — https://code.claude.com/docs/en/setup
- Claude Code sandboxing model (`allowUnsandboxedCommands`, bubblewrap, network proxy) — https://code.claude.com/docs/en/sandboxing ; https://www.anthropic.com/engineering/claude-code-sandboxing
- GitLab CLI non-interactive auth (`GITLAB_TOKEN`) + `glab auth git-credential` helper; scopes `api`,`write_repository` — https://docs.gitlab.com/cli/ ; https://docs.gitlab.com/cli/auth/
- GitHub CLI as git credential helper (`gh auth setup-git`) — https://cli.github.com
- Go current release (1.26.5) + downloads — https://go.dev/doc/devel/release ; https://go.dev/dl/
- RHEL UBI 9 base image — registry.access.redhat.com/ubi9/ubi
- Fedora 44 (host parity: Go 1.26, kernel 6.19) — https://fedoramagazine.org/announcing-fedora-linux-44/

🤖 Generated with [Claude Code](https://claude.com/claude-code) — commit trailer per your convention:
`Co-Authored-By: Claude <Opus 4.8> <noreply@anthropic.com>`
