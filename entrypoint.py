#!/usr/bin/env python3
"""
Deterministic entrypoint for the hardened Claude Code agent runner.

Everything here runs BEFORE Claude launches, so it costs zero model tokens:
  1. Preflight: assert required env/secrets exist; fail fast otherwise.
  2. Wire git identity + gh/glab credential helpers from scoped tokens (12-factor).
  3. Clone the target repos (parallel) into /workspace  — MANY repos per run is normal.
  4. Detect each repo's required Go version (go.mod `go`/`toolchain`); optionally
     pre-warm those toolchains and warm the module cache.
  5. Optionally stitch a go.work workspace across the modules (cross-repo work).
  6. Write WORKSPACE.md (incl. a per-repo Go-version table) so Claude starts oriented.
  7. exec `claude` with the task prompt.

No host directories are mounted. All inputs arrive as env / mounted Secrets.

Required env:
  Claude auth — ONE of the following:
    ANTHROPIC_API_KEY                 Direct API key (Console).
    CLAUDE_CODE_USE_VERTEX=1          Vertex AI via Application Default Credentials (ADC).
      + ANTHROPIC_VERTEX_PROJECT_ID   GCP project with Vertex Claude access.
      + VERTEXAI_PROJECT              Same project (both required by Claude Code).
      + VERTEXAI_LOCATION             Vertex region (e.g. "global", "us-east5").
      + CLOUD_ML_REGION               Same as VERTEXAI_LOCATION (some SDKs read this).
      + GOOGLE_APPLICATION_CREDENTIALS (optional — path to ADC JSON; default ~/.config/gcloud/).
  AGENT_TASK               Task prompt/plan (or AGENT_TASK_FILE path).
Repo selection (one or both; deduplicated):
  AGENT_REPOS              Whitespace/newline list of "host/owner/repo[@ref]" specs.
  AGENT_CONTROL_REPO       Git URL of a control repo with repos.txt + optional .claude overlay.
Optional env:
  GH_TOKEN / GITLAB_TOKEN  Scoped tokens (see README for minimal scopes).
  GITLAB_HOST              Self-managed GitLab host (default gitlab.com).
  GIT_AUTHOR_NAME/EMAIL    Commit identity (defaults provided).
  GOPRIVATE                e.g. github.com/your-org/*,gitlab.com/your-group/*
  AGENT_WARM_TOOLCHAINS=1  Pre-download each repo's required Go toolchain (surfaces
                           version issues early; else GOTOOLCHAIN=auto fetches on demand).
  AGENT_WARM_MODCACHE=1    Also run `go mod download` per module.
  AGENT_GO_WORK=1          Create /workspace/go.work spanning all cloned Go modules.
  AGENT_INTERACTIVE=1      Interactive `claude` instead of headless `-p`.
"""

import concurrent.futures as cf
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/home/agent"))
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", "/workspace"))
CLAUDE_CFG = HOME / ".claude"
BAKED_CFG = Path("/opt/agent/claude-config")

GO_RE = re.compile(r"^go\s+(\S+)", re.M)
TOOLCHAIN_RE = re.compile(r"^toolchain\s+(\S+)", re.M)
MODULE_RE = re.compile(r"^module\s+(\S+)", re.M)


def log(msg):
    print(f"[entrypoint] {msg}", flush=True)


def warn(msg):
    print(f"[entrypoint][WARNING] {msg}", flush=True)


def die(msg, code=1):
    print(f"[entrypoint][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


def redact(val):
    if not val:
        return "(not set)"
    if len(val) <= 4:
        return "*" * len(val)
    return val[:4] + "*" * min(len(val) - 4, 20)


def materialize_adc():
    """Write ADC credentials from env var to disk (avoids bind-mount SELinux issues).

    scripts/run-podman.sh reads the host ADC file and passes its content as
    GOOGLE_APPLICATION_CREDENTIALS_JSON. We write it to the writable home volume
    (container_file_t SELinux context) so the Google auth library can find it.
    """
    adc_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not adc_json:
        return
    adc_dir = HOME / ".config" / "gcloud"
    adc_dir.mkdir(parents=True, exist_ok=True)
    adc_path = adc_dir / "application_default_credentials.json"
    adc_path.write_text(adc_json)
    os.chmod(adc_path, 0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_path)
    del os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    log("materialized ADC credentials from env var")


def _has_vertex_auth():
    """Check whether Vertex AI auth is configured (env vars + ADC credential file)."""
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") != "1":
        return False
    if not os.environ.get("VERTEXAI_PROJECT"):
        return False
    adc_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(HOME / ".config/gcloud/application_default_credentials.json"),
    )
    return Path(adc_path).is_file()


def preflight():
    cache_only = os.environ.get("AGENT_CACHE_ONLY") == "1"
    if not cache_only:
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_vertex = _has_vertex_auth()
        if has_vertex:
            log(
                f"auth: Vertex AI (project={os.environ.get('VERTEXAI_PROJECT')}, "
                f"location={os.environ.get('VERTEXAI_LOCATION', 'unset')})"
            )
        elif has_api_key:
            log("auth: ANTHROPIC_API_KEY")
        else:
            die(
                "No Claude auth configured. Provide ANTHROPIC_API_KEY, or set "
                "CLAUDE_CODE_USE_VERTEX=1 with VERTEXAI_PROJECT + ADC credentials."
            )
        task = (os.environ.get("AGENT_TASK") or "").strip()
        task_file = (os.environ.get("AGENT_TASK_FILE") or "").strip()
        if not (task or task_file):
            die("Provide AGENT_TASK or AGENT_TASK_FILE.")
        if task_file and not Path(task_file).is_file():
            die(f"AGENT_TASK_FILE does not exist: {task_file}")
    if not (os.environ.get("AGENT_REPOS") or os.environ.get("AGENT_CONTROL_REPO")):
        die("Provide AGENT_REPOS or AGENT_CONTROL_REPO.")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    check_forge_credentials()


def check_forge_credentials(emit=True):
    """Check if repos target a forge but the matching token is missing.

    Returns list of warning strings. Prints them if emit=True.
    """
    warnings = []
    repos = os.environ.get("AGENT_REPOS", "")
    if "github.com" in repos and not os.environ.get("GH_TOKEN"):
        warnings.append("repos include github.com but GH_TOKEN is not set — push/PR will fail")
    if "gitlab.com" in repos and not os.environ.get("GITLAB_TOKEN"):
        host = os.environ.get("GITLAB_HOST", "gitlab.com")
        warnings.append(f"repos include {host} but GITLAB_TOKEN is not set — push/MR will fail")
    if emit:
        for w in warnings:
            warn(w)
    return warnings


def check_writable_paths():
    """Verify the three writable mount points are actually writable."""
    results = {}
    tmpdir = Path("/tmp")  # noqa: S108
    for p in [HOME, WORKSPACE, tmpdir]:
        try:
            probe = p / ".write-test"
            probe.write_text("ok")
            probe.unlink()
            results[str(p)] = True
        except OSError:
            results[str(p)] = False
    return results


def check_binaries():
    """Check required binaries are on PATH."""
    results = {}
    for name in ["claude", "git", "gh", "glab", "go", "rg"]:
        results[name] = shutil.which(name) is not None
    return results


def check_baked_config():
    """Check baked agent config exists."""
    return (BAKED_CFG / "AGENTS.md").is_file()


def print_config_summary():
    """Print full config summary with redacted secrets, then exit."""
    p = log
    p("=== Agent Runner Preflight ===")
    if _has_vertex_auth():
        p(
            f"Claude auth:    Vertex AI (project={os.environ.get('VERTEXAI_PROJECT')}, "
            f"location={os.environ.get('VERTEXAI_LOCATION', 'unset')})"
        )
    elif os.environ.get("ANTHROPIC_API_KEY"):
        p(f"Claude auth:    ANTHROPIC_API_KEY ({redact(os.environ.get('ANTHROPIC_API_KEY'))})")
    else:
        p("Claude auth:    NOT CONFIGURED")
    p(f"GH_TOKEN:       {redact(os.environ.get('GH_TOKEN'))}")
    p(f"GITLAB_TOKEN:   {redact(os.environ.get('GITLAB_TOKEN'))}")
    p(f"AGENT_REPOS:    {os.environ.get('AGENT_REPOS', '(not set)')}")
    task = (os.environ.get("AGENT_TASK") or "").strip()
    if task:
        preview = task[:60].replace("\n", " ")
        p(f'AGENT_TASK:     ({len(task)} chars, starts with "{preview}...")')
    else:
        p("AGENT_TASK:     (not set)")
    p(f"AGENT_MODE:     {os.environ.get('AGENT_MODE', 'online')}")
    p(f"AGENT_INTERACTIVE: {os.environ.get('AGENT_INTERACTIVE', '(not set)')}")

    writable = check_writable_paths()
    parts = [f"{path} {'OK' if ok else 'FAIL'}" for path, ok in writable.items()]
    p(f"Writable paths: {', '.join(parts)}")

    bins = check_binaries()
    parts = [f"{name} {'OK' if ok else 'MISSING'}" for name, ok in bins.items()]
    p(f"Binaries:       {', '.join(parts)}")

    baked = check_baked_config()
    p(f"Baked config:   {BAKED_CFG / 'AGENTS.md'} {'OK' if baked else 'MISSING'}")

    warnings = check_forge_credentials(emit=False)
    writable_fails = [path for path, ok in writable.items() if not ok]
    bin_fails = [name for name, ok in bins.items() if not ok]

    total_warnings = len(warnings) + len(writable_fails) + len(bin_fails) + (0 if baked else 1)
    if total_warnings:
        p(f"=== {total_warnings} WARNING(S) ===")
        for w in warnings:
            warn(w)
        for path in writable_fails:
            warn(f"{path} is not writable")
        for name in bin_fails:
            warn(f"{name} binary not found on PATH")
        if not baked:
            warn(f"baked config {BAKED_CFG / 'AGENTS.md'} not found")
    else:
        p("=== ALL CHECKS PASSED ===")

    return total_warnings


def seed_claude_config():
    """Overlay baked-in config into $HOME/.claude (runtime emptyDir masks the image path)."""
    CLAUDE_CFG.mkdir(parents=True, exist_ok=True)
    if BAKED_CFG.is_dir():
        for item in BAKED_CFG.iterdir():
            dst = CLAUDE_CFG / item.name
            shutil.copytree(item, dst, dirs_exist_ok=True) if item.is_dir() else shutil.copy2(item, dst)
    log(f"seeded Claude config into {CLAUDE_CFG}")


def configure_git():
    run(["git", "config", "--global", "user.name", os.environ.get("GIT_AUTHOR_NAME", "agent-bot")])
    run(["git", "config", "--global", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "agent-bot@localhost")])
    run(["git", "config", "--global", "init.defaultBranch", "main"])
    os.environ["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a prompt in a headless box
    if os.environ.get("GH_TOKEN"):
        run(["gh", "auth", "setup-git"])
        log("configured gh as git credential helper for github.com")
    if os.environ.get("GITLAB_TOKEN"):
        host = os.environ.get("GITLAB_HOST", "gitlab.com")
        run(["git", "config", "--global", f"credential.https://{host}.helper", "!glab auth git-credential"])
        log(f"configured glab as git credential helper for {host}")
    if os.environ.get("GOPRIVATE"):
        run(["go", "env", "-w", f"GOPRIVATE={os.environ['GOPRIVATE']}"])


def configure_go_mode():
    """Select online (default) or air-gapped (offline-go) Go behavior.

    online     : GOTOOLCHAIN=auto fetches toolchains/deps from proxy.golang.org on demand.
    offline-go : seed the pre-baked toolchain+module cache into the writable GOMODCACHE,
                 then forbid all network module/toolchain fetching (GOPROXY=off). Anything
                 a repo needs must already be in the cache, or its `go` commands fail loudly.
    """
    mode = os.environ.get("AGENT_MODE", "online")
    if mode != "offline-go":
        log("Go mode: online (GOTOOLCHAIN=auto, proxy fetch on demand)")
        return
    src = Path(os.environ.get("AGENT_GOCACHE_SRC", "/opt/go-cache"))
    modcache = Path(os.environ.get("GOMODCACHE", str(HOME / "go/pkg/mod")))
    if src.is_dir() and any(src.iterdir()):
        modcache.mkdir(parents=True, exist_ok=True)
        run(["cp", "-a", str(src) + "/.", str(modcache) + "/"])
        log(f"offline-go: seeded module/toolchain cache {src} -> {modcache}")
    else:
        log(f"offline-go: WARNING no pre-baked cache at {src}; builds may fail offline")
    # hard-forbid network fetches; rely on the seeded cache + committed go.sum
    for k, v in {"GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "auto"}.items():
        os.environ[k] = v
        run(["go", "env", "-w", f"{k}={v}"])
    log("offline-go: GOPROXY=off GOSUMDB=off (no network module/toolchain fetch)")


def repo_specs():
    """Yield deduped 'host/owner/repo[@ref]' specs from AGENT_REPOS and/or a control repo."""
    seen = {}  # base_spec -> full_tok (with @ref if any)

    def emit(tok):
        tok = tok.strip()
        if not tok or tok.startswith("#"):
            return None
        base = tok.split("@", 1)[0]
        if base in seen:
            if seen[base] != tok:
                die(
                    f"repo '{base}' specified with conflicting refs: "
                    f"'{seen[base]}' vs '{tok}'. Multiple refs of the same repo "
                    f"is not currently supported."
                )
            return None
        seen[base] = tok
        return tok

    if os.environ.get("AGENT_CONTROL_REPO"):
        ctrl = WORKSPACE / ".control"
        ctrl_url = "https://" + os.environ["AGENT_CONTROL_REPO"].removeprefix("https://")
        try:
            _git_clone(ctrl_url, ctrl)
        except subprocess.CalledProcessError as e:
            die(f"failed to clone control repo '{ctrl_url}': {e}")
        overlay = ctrl / ".claude"
        if overlay.is_dir():
            shutil.copytree(overlay, CLAUDE_CFG, dirs_exist_ok=True)
            log("overlaid .claude from control repo")
        manifest = ctrl / "repos.txt"
        if manifest.is_file():
            for line in manifest.read_text().splitlines():
                t = emit(line)
                if t:
                    yield t
    for tok in os.environ.get("AGENT_REPOS", "").split():
        t = emit(tok)
        if t:
            yield t


def _git_clone(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    run(["git", "clone", "--filter=blob:none", url, str(dest)])


def clone_one(spec):
    ref = None
    if "@" in spec:
        spec, ref = spec.split("@", 1)
    host, _, path = spec.partition("/")
    dest = WORKSPACE / f"{host}__{path.replace('/', '__')}"
    try:
        _git_clone(f"https://{host}/{path}.git", dest)
        if ref:
            run(["git", "-C", str(dest), "checkout", ref])
        return (spec, dest, True, "OK")
    except subprocess.CalledProcessError as e:
        return (spec, dest, False, str(e))


def clone_all(specs):
    if not specs:
        die("no repositories resolved from AGENT_REPOS / AGENT_CONTROL_REPO")
    log(f"cloning {len(specs)} repo(s) in parallel...")
    results = []
    with cf.ThreadPoolExecutor(max_workers=min(8, len(specs))) as ex:
        for spec, dest, ok, msg in ex.map(clone_one, specs):
            log(f"  {'OK  ' if ok else 'FAIL'} {spec}  {msg if not ok else ''}".rstrip())
            results.append((spec, dest, ok))
    if any(not ok for _, _, ok in results):
        die("one or more clones failed; aborting before launching Claude")
    return [(spec, dest) for spec, dest, ok in results if ok]


def go_requirements(dest: Path):
    """Return (module, go_version, toolchain) from a repo's go.mod, or None if no module."""
    gomod = dest / "go.mod"
    if not gomod.is_file():
        return None
    txt = gomod.read_text(errors="replace")
    m = MODULE_RE.search(txt)
    g = GO_RE.search(txt)
    t = TOOLCHAIN_RE.search(txt)
    return (m.group(1) if m else "?", g.group(1) if g else "-", t.group(1) if t else "-")


def go_scan(cloned):
    """Detect per-repo Go versions; optionally pre-warm the required toolchains."""
    warm = os.environ.get("AGENT_WARM_TOOLCHAINS") == "1"
    modcache = os.environ.get("AGENT_WARM_MODCACHE") == "1"
    rows = []
    for spec, dest in cloned:
        req = go_requirements(dest)
        if req is None:
            rows.append((spec, dest, None))
            continue
        module, gover, toolchain = req
        rows.append((spec, dest, (module, gover, toolchain)))
        if warm:
            # `go version` inside the module triggers GOTOOLCHAIN=auto to fetch+verify
            # the exact toolchain this repo needs (from proxy.golang.org / sum.golang.org).
            try:
                out = run(["go", "version"], cwd=dest, capture_output=True, text=True).stdout.strip()
                log(f"  toolchain ready for {spec}: {out}")
            except subprocess.CalledProcessError as e:
                log(f"  WARN toolchain warm failed for {spec}: {e}")
        if modcache:
            try:
                run(["go", "mod", "download"], cwd=dest)
            except subprocess.CalledProcessError as e:
                log(f"  WARN mod download failed for {spec}: {e}")
    return rows


def maybe_go_work(rows):
    """Optionally create /workspace/go.work spanning all Go modules (cross-repo work).

    Off by default: a workspace forces every `go` command into workspace mode, which is
    great for interdependent repos but can hide real version/dependency mismatches for
    independent ones. Enable with AGENT_GO_WORK=1; disable per-command with GOWORK=off.
    """
    if os.environ.get("AGENT_GO_WORK") != "1":
        return False
    mod_dirs = [str(dest) for _, dest, req in rows if req is not None]
    if len(mod_dirs) < 2:
        return False
    try:
        run(["go", "work", "init", *mod_dirs], cwd=WORKSPACE)
        log(f"created go.work spanning {len(mod_dirs)} module(s)")
        return True
    except subprocess.CalledProcessError as e:
        log(f"WARN could not create go.work (likely incompatible Go versions): {e}")
        return False


def write_workspace_md(rows, go_work):
    task = os.environ.get("AGENT_TASK")
    if not task and os.environ.get("AGENT_TASK_FILE"):
        task = Path(os.environ["AGENT_TASK_FILE"]).read_text()
    L = [
        "# WORKSPACE",
        "",
        "Isolated, ephemeral agent container. No host access. Egress is restricted to an",
        "allow-listed policy proxy. Work only inside `/workspace`.",
        "",
        "## Repositories (cloned; credentials wired)",
        "",
        "| repo | path | module | go | toolchain |",
        "|---|---|---|---|---|",
    ]
    for spec, dest, req in rows:
        if req is None:
            L.append(f"| `{spec}` | `{dest.name}` | *(not a Go module)* | - | - |")
        else:
            module, gover, toolchain = req
            L.append(f"| `{spec}` | `{dest.name}` | `{module}` | {gover} | {toolchain} |")
    L += [
        "",
        "Go toolchains are resolved **per repo** (`GOTOOLCHAIN=auto`): running `go build`/"
        "`go test` inside a repo automatically uses the version its go.mod requires.",
        "go.work workspace: "
        f"{'ENABLED at /workspace/go.work' if go_work else 'not enabled (each repo builds independently)'}.",
        "",
        "## Deterministic helpers (prefer over ad-hoc shell)",
        "",
        "- `agent-clone <host/owner/repo>[@ref]` — clone another repo with creds wired",
        "- `agent-open-pr` / `agent-open-mr` — push branch + open PR/MR (adds attribution footer)",
        "- `agent-ci-watch [github|gitlab]` — watch CI to completion for the current branch",
        "",
        "## Task",
        "",
        task or "(no task provided)",
        "",
    ]
    (WORKSPACE / "WORKSPACE.md").write_text("\n".join(L))
    log("wrote WORKSPACE.md (with per-repo Go-version table)")
    return task


def launch_claude(task):
    args = ["claude", "--dangerously-skip-permissions"]  # safe ONLY behind container+network walls
    if os.environ.get("AGENT_INTERACTIVE") == "1":
        # seed config with a no-op headless run so the interactive session
        # skips the first-run onboarding wizard (theme picker, etc.)
        run(["claude", "--dangerously-skip-permissions", "-p", "exit"])
        if task:
            args.append(task)
        log("launching claude (interactive)")
        os.execvp("claude", args)
    args += ["-p", task]
    log("launching claude (headless)")
    os.execvp("claude", args)


def main():
    materialize_adc()

    if "--preflight" in sys.argv:
        warnings = print_config_summary()
        sys.exit(1 if warnings else 0)

    preflight()
    seed_claude_config()
    configure_git()
    configure_go_mode()
    cloned = clone_all(list(repo_specs()))
    rows = go_scan(cloned)
    if os.environ.get("AGENT_CACHE_ONLY") == "1":
        log("cache-only: module/toolchain cache warmed; skipping Claude launch")
        return
    go_work = maybe_go_work(rows)
    task = write_workspace_md(rows, go_work)
    os.chdir(WORKSPACE)
    launch_claude(task)


if __name__ == "__main__":
    main()
