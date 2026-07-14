"""Security contract tests — assert invariants by reading file contents.

These tests fail if someone weakens security settings. They are the automated
equivalent of a security review checklist.
"""

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def settings():
    with open(REPO_ROOT / "agent-config" / "settings.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def job_yaml():
    with open(REPO_ROOT / "k8s" / "job.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def netpol_yaml():
    with open(REPO_ROOT / "k8s" / "networkpolicy.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def secret_yaml():
    with open(REPO_ROOT / "k8s" / "secret.example.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def run_podman_text():
    return (REPO_ROOT / "scripts" / "run-podman.sh").read_text()


class TestSettingsJson:
    EXPECTED_ALLOW = [
        "Bash(agent-clone *)",
        "Bash(agent-open-pr *)",
        "Bash(agent-open-mr *)",
        "Bash(agent-ci-watch *)",
        "Bash(go *)",
        "Bash(gofmt *)",
        "Bash(git *)",
        "Bash(gh *)",
        "Bash(glab *)",
        "Bash(make *)",
        "Bash(jq *)",
        "Bash(rg *)",
        "Bash(grep *)",
        "Bash(find *)",
        "Bash(ls *)",
        "Bash(cat *)",
        "Bash(head *)",
        "Bash(tail *)",
        "Bash(wc *)",
        "Bash(diff *)",
        "Bash(sort *)",
        "Bash(uniq *)",
        "Bash(mkdir *)",
        "Bash(cp *)",
        "Bash(mv *)",
        "Bash(touch *)",
        "Bash(chmod *)",
        "Bash(tar *)",
        "Bash(gzip *)",
    ]

    EXPECTED_DENY = [
        "Read(~/.ssh/**)",
        "Read(~/.aws/**)",
        "Read(~/.config/gh/**)",
        "Read(~/.config/glab-cli/**)",
        "Read(**/.env)",
        "Edit(~/.ssh/**)",
        "Edit(~/.aws/**)",
        "Edit(~/.config/gh/**)",
        "Edit(~/.config/glab-cli/**)",
        "Edit(**/.env)",
    ]

    DANGEROUS_COMMANDS = [
        "curl",
        "wget",
        "nc",
        "ncat",
        "socat",
        "python",
        "python3",
        "pip",
        "pip3",
        "npm",
        "node",
        "ruby",
        "perl",
        "ssh",
        "scp",
        "rsync",
        "dd",
        "mount",
    ]

    @pytest.mark.parametrize("entry", EXPECTED_ALLOW)
    def test_allow_entry_present(self, settings, entry):
        assert entry in settings["permissions"]["allow"]

    @pytest.mark.parametrize("entry", EXPECTED_DENY)
    def test_deny_entry_present(self, settings, entry):
        assert entry in settings["permissions"]["deny"]

    @pytest.mark.parametrize("cmd", DANGEROUS_COMMANDS)
    def test_no_dangerous_command_allowed(self, settings, cmd):
        for entry in settings["permissions"]["allow"]:
            assert not entry.startswith(f"Bash({cmd} "), f"dangerous command '{cmd}' found in allow list"

    def test_merge_denied(self, settings):
        deny = settings["permissions"]["deny"]
        assert "Bash(gh pr merge *)" in deny
        assert "Bash(glab mr merge *)" in deny

    def test_hook_targets_go_files(self, settings):
        hooks = settings["hooks"]["PostToolUse"]
        edit_hook = next(h for h in hooks if h["matcher"] == "Edit")
        assert "*.go" in edit_hook["hooks"][0]["command"]

    def test_disable_autoupdater(self, settings):
        assert settings["env"]["DISABLE_AUTOUPDATER"] == "1"

    def test_disable_updates(self, settings):
        assert settings["env"]["DISABLE_UPDATES"] == "1"

    def test_disable_telemetry(self, settings):
        assert settings["env"]["DISABLE_TELEMETRY"] == "1"


class TestJobYaml:
    def test_run_as_non_root(self, job_yaml):
        ctx = job_yaml["spec"]["template"]["spec"]["securityContext"]
        assert ctx["runAsNonRoot"] is True

    def test_run_as_user_1001(self, job_yaml):
        ctx = job_yaml["spec"]["template"]["spec"]["securityContext"]
        assert ctx["runAsUser"] == 1001

    def test_readonly_root_filesystem(self, job_yaml):
        container = job_yaml["spec"]["template"]["spec"]["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True

    def test_no_privilege_escalation(self, job_yaml):
        container = job_yaml["spec"]["template"]["spec"]["containers"][0]
        assert container["securityContext"]["allowPrivilegeEscalation"] is False

    def test_capabilities_drop_all(self, job_yaml):
        container = job_yaml["spec"]["template"]["spec"]["containers"][0]
        assert "ALL" in container["securityContext"]["capabilities"]["drop"]

    def test_automount_token_false(self, job_yaml):
        assert job_yaml["spec"]["template"]["spec"]["automountServiceAccountToken"] is False

    def test_seccomp_runtime_default(self, job_yaml):
        ctx = job_yaml["spec"]["template"]["spec"]["securityContext"]
        assert ctx["seccompProfile"]["type"] == "RuntimeDefault"

    def test_active_deadline(self, job_yaml):
        assert job_yaml["spec"]["activeDeadlineSeconds"] > 0


class TestNetworkPolicy:
    def test_exactly_two_egress_rules(self, netpol_yaml):
        assert len(netpol_yaml["spec"]["egress"]) == 2

    def test_dns_rule_permits_udp_and_tcp(self, netpol_yaml):
        dns_rule = netpol_yaml["spec"]["egress"][0]
        protocols = {p["protocol"] for p in dns_rule["ports"]}
        ports = {p["port"] for p in dns_rule["ports"]}
        assert "UDP" in protocols
        assert "TCP" in protocols
        assert 53 in ports

    def test_proxy_rule_scoped_to_label(self, netpol_yaml):
        proxy_rule = netpol_yaml["spec"]["egress"][1]
        selector = proxy_rule["to"][0]["podSelector"]["matchLabels"]
        assert selector["app"] == "agent-egress-proxy"
        assert proxy_rule["ports"][0]["port"] == 8080


class TestSecretExample:
    def test_no_real_secrets(self, secret_yaml):
        for key, value in secret_yaml["stringData"].items():
            value_stripped = str(value).strip()
            assert value_stripped.startswith("REPLACE_WITH_") or value_stripped in ("1", "global"), (
                f"key '{key}' may contain a real secret: {value_stripped[:20]}"
            )


class TestRunPodmanSh:
    def test_cap_drop_all(self, run_podman_text):
        assert "--cap-drop=ALL" in run_podman_text

    def test_no_new_privileges(self, run_podman_text):
        assert "--security-opt no-new-privileges" in run_podman_text

    def test_read_only(self, run_podman_text):
        assert "--read-only" in run_podman_text

    def test_user_1001(self, run_podman_text):
        assert "--user 1001:1001" in run_podman_text

    def test_no_host_bind_mounts(self, run_podman_text):
        import re

        host_mounts = re.findall(r'-v\s+/[^$\s"]+:', run_podman_text)
        assert host_mounts == [], f"found host bind mounts: {host_mounts}"

    def test_memory_limit(self, run_podman_text):
        assert "--memory 8g" in run_podman_text

    def test_pids_limit(self, run_podman_text):
        assert "--pids-limit 512" in run_podman_text
