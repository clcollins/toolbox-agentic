"""Unit tests for bootstrap.py."""

import os
import subprocess
from unittest.mock import patch

import pytest


class TestDie:
    def test_exits_with_default_code(self, bootstrap):
        with pytest.raises(SystemExit) as exc:
            bootstrap.die("boom")
        assert exc.value.code == 1

    def test_exits_with_custom_code(self, bootstrap):
        with pytest.raises(SystemExit) as exc:
            bootstrap.die("boom", code=42)
        assert exc.value.code == 42

    def test_prints_fatal_to_stderr(self, bootstrap, capsys):
        with pytest.raises(SystemExit):
            bootstrap.die("test message")
        assert "[bootstrap][FATAL] test message" in capsys.readouterr().err


class TestPreflight:
    def test_no_auth_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK", "do stuff")
        with pytest.raises(SystemExit):
            bootstrap.preflight()

    def test_api_key_auth_passes(self, bootstrap, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK", "do stuff")
        bootstrap.preflight()

    def test_vertex_auth_passes(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        monkeypatch.setenv("VERTEXAI_LOCATION", "global")
        adc_dir = tmp_path / "home" / ".config" / "gcloud"
        adc_dir.mkdir(parents=True, exist_ok=True)
        (adc_dir / "application_default_credentials.json").write_text("{}")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK", "do stuff")
        bootstrap.preflight()

    def test_no_task_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        with pytest.raises(SystemExit):
            bootstrap.preflight()

    def test_whitespace_task_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK", "   ")
        with pytest.raises(SystemExit):
            bootstrap.preflight()

    def test_missing_task_file_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK_FILE", "/nonexistent/task.txt")
        with pytest.raises(SystemExit):
            bootstrap.preflight()

    def test_valid_task_file_passes(self, bootstrap, monkeypatch, tmp_path):
        task_file = tmp_path / "task.txt"
        task_file.write_text("do stuff")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK_FILE", str(task_file))
        bootstrap.preflight()

    def test_no_repos_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_TASK", "do stuff")
        with pytest.raises(SystemExit):
            bootstrap.preflight()

    def test_cache_only_skips_auth(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_CACHE_ONLY", "1")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        bootstrap.preflight()

    def test_creates_workspace_dir(self, bootstrap, monkeypatch, tmp_path):
        ws = tmp_path / "workspace"
        if ws.exists():
            ws.rmdir()
        monkeypatch.setattr(bootstrap, "WORKSPACE", ws)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        monkeypatch.setenv("AGENT_TASK", "do stuff")
        bootstrap.preflight()
        assert ws.is_dir()


class TestMaterializeAdc:
    def test_writes_adc_file(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", '{"type":"test"}')
        bootstrap.materialize_adc()
        adc = tmp_path / "home" / ".config" / "gcloud" / "application_default_credentials.json"
        assert adc.is_file()
        assert adc.read_text() == '{"type":"test"}'

    def test_file_permissions_0600(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", '{"type":"test"}')
        bootstrap.materialize_adc()
        adc = tmp_path / "home" / ".config" / "gcloud" / "application_default_credentials.json"
        assert oct(adc.stat().st_mode & 0o777) == oct(0o600)

    def test_sets_credentials_env(self, bootstrap, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", '{"type":"test"}')
        bootstrap.materialize_adc()
        assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") is not None

    def test_deletes_source_env_var(self, bootstrap, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", '{"type":"test"}')
        bootstrap.materialize_adc()
        assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in os.environ

    def test_noop_without_env_var(self, bootstrap):
        bootstrap.materialize_adc()
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


class TestHasVertexAuth:
    def test_false_without_vertex_flag(self, bootstrap):
        assert bootstrap._has_vertex_auth() is False

    def test_false_without_project(self, bootstrap, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        assert bootstrap._has_vertex_auth() is False

    def test_false_without_adc_file(self, bootstrap, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        assert bootstrap._has_vertex_auth() is False

    def test_true_with_all(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        adc_dir = tmp_path / "home" / ".config" / "gcloud"
        adc_dir.mkdir(parents=True, exist_ok=True)
        (adc_dir / "application_default_credentials.json").write_text("{}")
        assert bootstrap._has_vertex_auth() is True


class TestRepoSpecs:
    def test_basic_spec(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo")
        specs = list(bootstrap.repo_specs())
        assert specs == ["github.com/org/repo"]

    def test_comment_lines_skipped(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "#comment")
        specs = list(bootstrap.repo_specs())
        assert specs == []

    def test_empty_lines_skipped(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "  github.com/org/repo  ")
        specs = list(bootstrap.repo_specs())
        assert specs == ["github.com/org/repo"]

    def test_ref_parsing(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo@v1.0")
        specs = list(bootstrap.repo_specs())
        assert specs == ["github.com/org/repo@v1.0"]

    def test_dedup(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo github.com/org/repo")
        specs = list(bootstrap.repo_specs())
        assert specs == ["github.com/org/repo"]

    def test_conflicting_refs_dies(self, bootstrap, monkeypatch):
        monkeypatch.setenv("AGENT_REPOS", "github.com/org/repo@main github.com/org/repo@v1.0")
        with pytest.raises(SystemExit):
            list(bootstrap.repo_specs())


class TestGoRequirements:
    def test_parses_go_mod(self, bootstrap, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/foo\n\ngo 1.26.5\n\ntoolchain go1.26.5\n")
        result = bootstrap.go_requirements(repo)
        assert result == ("example.com/foo", "1.26.5", "go1.26.5")

    def test_no_gomod_returns_none(self, bootstrap, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert bootstrap.go_requirements(repo) is None

    def test_missing_toolchain(self, bootstrap, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/foo\n\ngo 1.21\n")
        result = bootstrap.go_requirements(repo)
        assert result == ("example.com/foo", "1.21", "-")

    def test_missing_go_version(self, bootstrap, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/foo\n")
        result = bootstrap.go_requirements(repo)
        assert result == ("example.com/foo", "-", "-")


class TestCloneOne:
    def test_path_includes_host(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setattr(bootstrap, "WORKSPACE", tmp_path / "workspace")
        with patch.object(bootstrap, "run"):
            with patch.object(bootstrap, "_git_clone"):
                spec, dest, ok, msg = bootstrap.clone_one("github.com/org/repo")
        assert dest.name == "github.com__org__repo"

    def test_ref_with_slashes(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setattr(bootstrap, "WORKSPACE", tmp_path / "workspace")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with patch.object(bootstrap, "run", fake_run):
            with patch.object(bootstrap, "_git_clone"):
                spec, dest, ok, msg = bootstrap.clone_one("github.com/org/repo@fix/issue-42")

        checkout_calls = [c for c in calls if "checkout" in c]
        assert any("fix/issue-42" in c for c in checkout_calls)

    def test_already_cloned_skips(self, bootstrap, monkeypatch, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(exist_ok=True)
        (ws / "github.com__org__repo").mkdir()
        monkeypatch.setattr(bootstrap, "WORKSPACE", ws)

        with patch.object(bootstrap, "run") as mock_run:
            spec, dest, ok, msg = bootstrap.clone_one("github.com/org/repo")

        mock_run.assert_not_called()


class TestWriteWorkspaceMd:
    def test_output_contains_table(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_TASK", "test task")
        monkeypatch.setattr(bootstrap, "WORKSPACE", tmp_path / "workspace")
        (tmp_path / "workspace").mkdir(exist_ok=True)
        rows = [("github.com/org/repo", tmp_path / "workspace" / "repo", ("mod", "1.21", "go1.21.0"))]
        bootstrap.write_workspace_md(rows, False)
        md = (tmp_path / "workspace" / "WORKSPACE.md").read_text()
        assert "| repo |" in md
        assert "`mod`" in md

    def test_output_contains_task(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_TASK", "my test task")
        monkeypatch.setattr(bootstrap, "WORKSPACE", tmp_path / "workspace")
        (tmp_path / "workspace").mkdir(exist_ok=True)
        rows = []
        bootstrap.write_workspace_md(rows, False)
        md = (tmp_path / "workspace" / "WORKSPACE.md").read_text()
        assert "my test task" in md

    def test_non_go_row(self, bootstrap, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_TASK", "test")
        monkeypatch.setattr(bootstrap, "WORKSPACE", tmp_path / "workspace")
        (tmp_path / "workspace").mkdir(exist_ok=True)
        rows = [("github.com/org/docs", tmp_path / "workspace" / "docs", None)]
        bootstrap.write_workspace_md(rows, False)
        md = (tmp_path / "workspace" / "WORKSPACE.md").read_text()
        assert "not a Go module" in md
