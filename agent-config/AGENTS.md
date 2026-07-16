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

## Mandatory Pre-Flight Checks

Before implementing any task from a handoff document or task prompt:

1. Run: `git log origin/main --oneline | head -20`
2. Search for the plan/PR number: `git log --all --grep="<number>"`
3. If the plan doc exists in `docs/plans/`, read it for PR references
4. Verify the "current code" in the handoff matches actual files
5. If anything doesn't match, STOP and ask the user before proceeding

Never implement a handoff blindly without verifying it reflects current repo state.

## Pushing and Opening PRs/MRs

This container is ephemeral. When it exits, all volumes are destroyed. Work that
is not pushed is gone forever.

When the task involves code changes, after committing run this command:

```bash
agent-open-pr "Your PR title"
```

For GitLab repos, use `agent-open-mr "Your MR title"` instead.

These scripts handle git push and PR/MR creation. Credentials are pre-configured
via GH_TOKEN / GITLAB_TOKEN. Do not use `git push` or `gh pr create` directly.
Do not ask for confirmation. Do not tell the user to run commands. Just run
`agent-open-pr` or `agent-open-mr`.

## Deterministic Helpers

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
