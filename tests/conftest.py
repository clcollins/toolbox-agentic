import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear agent/auth env vars to prevent test pollution."""
    for key in list(os.environ):
        if key.startswith("AGENT_") or key.startswith("VERTEXAI_") or key.startswith("CLOUD_ML"):
            monkeypatch.delenv(key, raising=False)
    for key in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "GITLAB_HOST",
        "GOPRIVATE",
        "EGRESS_PROFILE",
        "GOPROXY",
        "GOSUMDB",
        "GOTOOLCHAIN",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def entrypoint(monkeypatch, tmp_path):
    """Import entrypoint.py with patched HOME and WORKSPACE."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "home").mkdir()
    (tmp_path / "workspace").mkdir()

    if "entrypoint" in sys.modules:
        del sys.modules["entrypoint"]

    sys.path.insert(0, str(REPO_ROOT))
    try:
        import entrypoint as mod

        monkeypatch.setattr(mod, "HOME", tmp_path / "home")
        monkeypatch.setattr(mod, "WORKSPACE", tmp_path / "workspace")
        monkeypatch.setattr(mod, "BAKED_CFG", tmp_path / "baked-cfg")
        yield mod
    finally:
        sys.path.remove(str(REPO_ROOT))
        if "entrypoint" in sys.modules:
            del sys.modules["entrypoint"]


@pytest.fixture
def policy():
    """Import egress-proxy/policy.py via importlib (hyphenated directory)."""
    spec = importlib.util.spec_from_file_location("policy", REPO_ROOT / "egress-proxy" / "policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod


@pytest.fixture
def policy_offline():
    """Import policy.py with EGRESS_PROFILE=offline-go to test host subtraction."""
    os.environ["EGRESS_PROFILE"] = "offline-go"
    try:
        spec = importlib.util.spec_from_file_location("policy_offline", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("EGRESS_PROFILE", None)


@pytest.fixture
def policy_with_agent_egress():
    """Import policy.py with AGENT_EGRESS='example.com'."""
    os.environ["AGENT_EGRESS"] = "example.com"
    try:
        spec = importlib.util.spec_from_file_location("policy_ae", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS", None)


@pytest.fixture
def policy_with_multiple_egress():
    """Import policy.py with AGENT_EGRESS='a.com,b.net,c.org'."""
    os.environ["AGENT_EGRESS"] = "a.com,b.net,c.org"
    try:
        spec = importlib.util.spec_from_file_location("policy_me", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS", None)


@pytest.fixture
def policy_with_spaced_egress():
    """Import policy.py with AGENT_EGRESS=' a.com , b.com '."""
    os.environ["AGENT_EGRESS"] = " a.com , b.com "
    try:
        spec = importlib.util.spec_from_file_location("policy_se", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS", None)


@pytest.fixture
def policy_with_empty_egress():
    """Import policy.py with AGENT_EGRESS=''."""
    os.environ["AGENT_EGRESS"] = ""
    try:
        spec = importlib.util.spec_from_file_location("policy_ee", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS", None)


@pytest.fixture
def policy_with_override():
    """Import policy.py with AGENT_EGRESS_OVERRIDE='custom.com'."""
    os.environ["AGENT_EGRESS_OVERRIDE"] = "custom.com"
    try:
        spec = importlib.util.spec_from_file_location("policy_ov", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS_OVERRIDE", None)


@pytest.fixture
def policy_with_override_multiple():
    """Import policy.py with AGENT_EGRESS_OVERRIDE='x.com,y.net,z.org'."""
    os.environ["AGENT_EGRESS_OVERRIDE"] = "x.com,y.net,z.org"
    try:
        spec = importlib.util.spec_from_file_location("policy_ovm", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS_OVERRIDE", None)


@pytest.fixture
def policy_with_both_vars():
    """Import policy.py with both AGENT_EGRESS and AGENT_EGRESS_OVERRIDE set."""
    os.environ["AGENT_EGRESS"] = "ignored.com"
    os.environ["AGENT_EGRESS_OVERRIDE"] = "override.com"
    try:
        spec = importlib.util.spec_from_file_location("policy_both", REPO_ROOT / "egress-proxy" / "policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.environ.pop("AGENT_EGRESS", None)
        os.environ.pop("AGENT_EGRESS_OVERRIDE", None)
