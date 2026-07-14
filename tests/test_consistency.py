"""Cross-file consistency tests — detect drift between code, config, and docs."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def settings():
    with open(REPO_ROOT / "claude" / "settings.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def containerfile_text():
    return (REPO_ROOT / "Containerfile").read_text()


@pytest.fixture(scope="module")
def run_podman_text():
    return (REPO_ROOT / "run-podman.sh").read_text()


@pytest.fixture(scope="module")
def job_yaml_text():
    return (REPO_ROOT / "k8s" / "job.yaml").read_text()


@pytest.fixture(scope="module")
def policy_module():
    spec = importlib.util.spec_from_file_location("policy_consist", REPO_ROOT / "egress-proxy" / "policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSettingsContainerfileConsistency:
    COREUTILS = {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "diff",
        "sort",
        "uniq",
        "mkdir",
        "cp",
        "mv",
        "touch",
        "chmod",
        "tar",
        "gzip",
        "find",
    }

    def test_allow_list_binaries_installed(self, settings, containerfile_text):
        allow = settings["permissions"]["allow"]
        binaries = set()
        for entry in allow:
            match = re.match(r"Bash\((\S+)\s", entry)
            if match:
                binaries.add(match.group(1))

        binaries -= self.COREUTILS
        binaries -= {"grep"}

        for binary in binaries:
            if binary.startswith("agent-"):
                assert "agent-" in containerfile_text or "bin/" in containerfile_text, (
                    f"agent helper '{binary}' not copied in Containerfile"
                )
            elif binary in ("go", "gofmt"):
                assert "go" in containerfile_text.lower()
            elif binary == "gh":
                assert "gh" in containerfile_text
            elif binary == "glab":
                assert "glab" in containerfile_text
            elif binary == "jq":
                assert "jq" in containerfile_text
            elif binary == "rg":
                assert "ripgrep" in containerfile_text or "rg" in containerfile_text
            elif binary == "make":
                assert "make" in containerfile_text


class TestEnvVarConsistency:
    BOOTSTRAP_ENV_VARS = [
        "ANTHROPIC_API_KEY",
        "AGENT_TASK",
        "AGENT_REPOS",
        "CLAUDE_CODE_USE_VERTEX",
        "VERTEXAI_PROJECT",
    ]

    @pytest.mark.parametrize("var", BOOTSTRAP_ENV_VARS)
    def test_env_var_in_run_podman(self, run_podman_text, var):
        assert var in run_podman_text, f"env var '{var}' not found in run-podman.sh"

    @pytest.mark.parametrize("var", BOOTSTRAP_ENV_VARS)
    def test_env_var_in_job_yaml(self, job_yaml_text, var):
        assert var in job_yaml_text, f"env var '{var}' not found in k8s/job.yaml"


class TestTrustedHostConsistency:
    def test_security_md_documents_trusted_hosts(self, policy_module):
        security_md = (REPO_ROOT / "SECURITY.md").read_text()
        core_hosts = ["github.com", "api.anthropic.com", "proxy.golang.org"]
        for host in core_hosts:
            assert host in security_md, f"trusted host '{host}' not documented in SECURITY.md"
