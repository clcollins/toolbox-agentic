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
