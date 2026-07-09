# Implementation Plan — Hardened Claude Code Agent Runner

## Goal

Build a single container image that runs Claude Code autonomously for Go development
across GitHub/GitLab repos, isolated from the host by default, launchable on-demand
with Podman or on a kubeadm cluster.

## Security Invariants

1. Non-root (uid 1001) inside the container
2. Read-only root filesystem; writable state on ephemeral disk-backed volumes
3. All capabilities dropped, no-new-privileges, seccomp RuntimeDefault, SELinux enforcing
4. Own namespaces (never host PID/IPC/network)
5. No Kubernetes API access (automountServiceAccountToken: false)
6. No host bind mounts — repos cloned at runtime
7. Single egress path through a policy proxy
8. Scoped, revocable credentials only, injected at runtime
9. Ephemeral: each run is disposable and self-destructs

## Key Decisions

- **UBI 9 base**: glibc 2.34 matches Claude Code's Linux binary requirements
- **Go from upstream tarball**: pinned version with per-arch checksums, not distro package
- **GOTOOLCHAIN=auto**: each repo selects its own Go version via go.mod
- **Python entrypoint**: bootstrap.py runs before Claude (zero model tokens)
- **glab from release binary**: checksum-verified, not in UBI repos
- **Claude Code from signed dnf repo**: gpgcheck=1 for supply-chain integrity
- **Disk-backed volumes**: NEVER RAM-backed tmpfs for Go caches (would OOM)
- **Internal Podman network**: agent has no gateway; proxy is dual-homed
- **GET-only research at proxy**: method restriction is only hard at the network layer

## Trade-offs

- Egress proxy adds latency but is the only way to enforce method restrictions
- GOTOOLCHAIN=auto downloads toolchains on demand (network dependency) but
  offline-go mode provides air-gapped alternative
- Pre-baking toolchains increases image size but eliminates runtime downloads
- go.work is opt-in because workspace mode can mask real dependency mismatches

## Version Anchors (verified July 2026)

- Go: 1.26.5 (latest stable)
- glab: 1.107.0 (latest release)
- Claude Code: signed dnf repo (GPG: 31DD DE24 DDFA B679...)
- GitHub CLI: official RPM repo
- UBI 9: registry.access.redhat.com/ubi9/ubi:9.6

## Verification

1. `python3 -m py_compile bootstrap.py` — syntax check
2. `bash -n` on all shell scripts — parse check
3. JSON parse on claude/settings.json
4. YAML parse on all k8s/*.yaml files
5. Cross-reference env var names across Containerfile, bootstrap.py, run-podman.sh, k8s/job.yaml
6. Build the Containerfile for amd64 (checksums must be filled first)
