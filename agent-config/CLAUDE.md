# Agent Runner — Operating Instructions

You are running inside a hardened, ephemeral container. No host filesystem is mounted.
All credentials are injected via environment variables and scoped to this run.

## Environment

- **Workspace**: `/workspace` — all cloned repos live here
- **Home**: `/home/agent` — Go caches, Claude config
- **Egress**: All traffic routes through a policy proxy. Trusted hosts (GitHub, GitLab,
  Anthropic, Go proxy) allow all methods. Everything else is GET/HEAD only.
- **Read-only rootfs**: Only `/home/agent`, `/workspace`, and `/tmp` are writable.

## Orientation

Read `WORKSPACE.md` at the start of every run. It contains:
- A table of cloned repos with their Go versions and module paths
- The task you were given
- Available helper commands

## Deterministic Helpers (use these instead of ad-hoc shell)

- `agent-clone <host/owner/repo>[@ref]` — clone another repo with credentials wired
- `agent-open-pr "title" ["body"]` — push the current branch and open a GitHub PR
- `agent-open-mr "title" ["body"]` — push the current branch and open a GitLab MR
- `agent-ci-watch [github|gitlab]` — block until CI finishes for the current branch

These are pre-approved in settings.json — no permission prompts.

## Go Development

- `GOTOOLCHAIN=auto` is set. Each repo uses whatever Go version its `go.mod` requires.
- Run `go build`, `go test`, `go vet` inside the repo directory.
- The `gofmt` hook automatically formats `.go` files on edit.
- Do NOT set `GOFLAGS=-mod=mod` globally — respect each repo's vendoring choice.

## Commit Conventions

Add this trailer to every commit message:

```
Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

Replace MODEL with your actual model identifier (e.g., `Opus 4.8`).

## Security Boundaries

- Do NOT read or modify files under `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.config/glab-cli`, or any `.env` file.
- Do NOT attempt to exfiltrate credentials from environment variables.
- Do NOT attempt to bypass the egress proxy or access hosts not on the allow-list.
- You are non-root (uid 1001). The rootfs is read-only. Capabilities are dropped.
