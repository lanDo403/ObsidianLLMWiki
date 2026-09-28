"""Claude integration contracts; never call a live model or change a user profile."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from scripts import rewrite_backend, wiki_compile
from scripts.wiki_models import ChangeSet, WikiPageUpdate
from tests.test_wiki_compile import _good_fm, _seed_space

ROOT = Path(__file__).resolve().parents[1]


def test_shared_contract_and_claude_skill_metadata():
    contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert re.search(r"^@AGENTS\.md$", contract, re.M)
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill.split("---", 2)[1])
    assert frontmatter["name"] == "obsidian-llmwiki"
    assert "memory" in frontmatter["description"]
    assert "recall" in frontmatter["when_to_use"]
    assert "trigger_phrases" not in frontmatter
    assert "${CLAUDE_SKILL_DIR}/repository" in skill


def _registration_script(repo: Path, destination: Path) -> str:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    function = installer.split("register_global_skill() {", 1)[1].split("\nregister_claude_md()", 1)[0]
    return ("set -euo pipefail\n"
            f"REPO_DIR={shlex.quote(repo.as_posix())}\n"
            f"SKILL_DIR={shlex.quote(destination.as_posix())}\n"
            "register_global_skill() {" + function + "\nregister_global_skill\n")


def _fake_repository(path: Path) -> None:
    for name in ("SKILL.md", "AGENTS.md", "tags.yaml", "rules/atomization.md", "rules/taxonomy.md",
                 "rules/personal_notes.md", "rules/contacts.md", "rules/memory_retrieval.md",
                 "rules/source_registry.md", "docs/agent-workflows.md", "scripts/memory_index.py"):
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(name, encoding="utf-8")


def test_bash_installer_syntax():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable")
    # Normalize checkout line endings as a Linux checkout does.
    result = subprocess.run([bash, "--noprofile", "--norc", "-n"],
                            input=(ROOT / "install.sh").read_text(encoding="utf-8"),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


# ponytail: POSIX installer check; add native MSYS coverage in a Windows test environment.
@pytest.mark.skipif(os.name != "posix", reason="Bash symlink registration requires a POSIX test environment")
def test_global_registration_links_and_idempotence(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable")
    repo = tmp_path / "repo with spaces"
    skill = tmp_path / "installed skill"
    _fake_repository(repo)
    script = _registration_script(repo, skill)
    # No real user HOME or Claude configuration is used.
    result = subprocess.run([bash, "--noprofile", "--norc"], input=script,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    for name in ("AGENTS.md", "rules/memory_retrieval.md", "rules/source_registry.md",
                 "docs/agent-workflows.md", "repository/scripts/memory_index.py"):
        assert (skill / name).is_file(), name
    assert (skill / "repository").resolve() == repo.resolve()
    second = subprocess.run([bash, "--noprofile", "--norc"], input=script,
                            text=True, capture_output=True)
    assert second.returncode == 0, second.stderr
    # Existing real directories must survive without a nested accidental link.
    (skill / "rules").unlink()
    (skill / "rules").mkdir()
    sentinel = skill / "rules/user.md"
    sentinel.write_text("personal data", encoding="utf-8")
    third = subprocess.run([bash, "--noprofile", "--norc"], input=script,
                           text=True, capture_output=True)
    assert third.returncode != 0 and "refusing to replace" in third.stderr
    assert sentinel.read_text(encoding="utf-8") == "personal data"


@pytest.mark.parametrize("preserve_links,expected", [(True, 0), (False, 5)])
def test_compile_through_claude_cli_preserves_writer_guard(tmp_path, monkeypatch, preserve_links, expected):
    root = tmp_path / "vault/LLM Wiki/demo"
    _seed_space(root)
    body = "# Architecture\n[[postgres]]" + (" [[redis]]" if preserve_links else "")
    cs = ChangeSet("demo", "test", updates=[WikiPageUpdate(
        "pages/architecture.md", [], _good_fm("demo", "core", "architecture"), body)])
    config = {"vault": {"vault_path": str(tmp_path / "vault")},
              "rclone": {"staging_dir": str(tmp_path / "staging")}}
    original = {p: p.read_bytes() for p in root.rglob("*.md")}
    monkeypatch.setattr(wiki_compile, "_load_config", lambda **kw: config)
    monkeypatch.setattr(wiki_compile, "write_debug_prompt", lambda *a, **kw: tmp_path / "prompt.txt")
    monkeypatch.setattr(wiki_compile, "DEBUG_RESPONSE_PATH", tmp_path / "response.json")
    # Explicit --backend must win even inside a Codex process, while the nested
    # Claude invocation must not inherit the Claude nesting marker.
    monkeypatch.setenv("CODEX_THREAD_ID", "outer-codex")
    monkeypatch.setenv("CLAUDECODE", "outer-claude")
    calls = []
    def fake_cli(cmd, **kwargs):
        assert cmd == ["claude", "--print"]
        assert "CLAUDECODE" not in kwargs["env"]
        assert "WIKI_LINKS_LOST" in kwargs["input"]
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(cs.to_dict()), "")
    monkeypatch.setattr(rewrite_backend.subprocess, "run", fake_cli)
    writer = Mock(return_value=0)
    monkeypatch.setattr(wiki_compile, "write_to_vault", writer)
    monkeypatch.setattr(sys, "argv", ["wiki_compile.py", "demo", "--backend", "claude"])
    assert wiki_compile.main() == expected
    assert len(calls) == 1
    assert writer.call_count == int(preserve_links)
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == original


def test_claude_auto_detection(monkeypatch):
    for key in ("CODEX_THREAD_ID", "CODEX_CI", "OBSIDIAN_LLMWIKI_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert rewrite_backend.detect_backend() == "claude"
