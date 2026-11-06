"""验证文件工具对敏感路径及符号链接的统一拒绝。"""

from __future__ import annotations

import pytest

from aurora.agent.safety import AutoApprover, ConfirmationGate, DenyApprover
from aurora.agent.sandbox import Sandbox, UnsafeSubprocessExecutor
from aurora.agent.tools import build_sandbox_tools, get_available_tools
from aurora.agent.tools.builtin import list_files, read_file


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".ENV.production",
        "config/production.env",
        "certificates/private.key",
        "certificates/private.pem",
        "certificates/private.pfx",
        "keys/id_ed25519",
        ".aws/credentials",
        ".ssh/config",
        ".kube/config",
        "config/credentials.json",
        "config/secrets.yaml",
        ".npmrc",
        ".netrc",
    ],
)
def test_all_registered_file_tools_reject_sensitive_paths(tmp_path, monkeypatch, name):
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fixture-only-secret", encoding="utf-8")
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    monkeypatch.setattr("aurora.agent.sandbox._current", sandbox)
    tools = get_available_tools() | build_sandbox_tools(sandbox)
    for tool_name in ("read_file", "sandbox_read_file"):
        with pytest.raises(PermissionError, match="敏感"):
            ConfirmationGate(DenyApprover()).invoke(tools[tool_name], {"path": name})
    with pytest.raises(PermissionError, match="敏感"):
        read_file(str(target))
    with pytest.raises(PermissionError, match="敏感"):
        ConfirmationGate(AutoApprover()).invoke(
            tools["write_file"], {"path": name, "content": "overwritten"}
        )
    assert target.read_text(encoding="utf-8") == "fixture-only-secret"


@pytest.mark.parametrize("name", ["README.md", "src/secrets.py", ".env.example", ".env.sample"])
def test_source_files_and_environment_templates_remain_accessible(tmp_path, name):
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    sandbox.write_file(name, "template-or-source")
    assert sandbox.read_file(name) == "template-or-source"
    assert read_file(str(tmp_path / name)) == "template-or-source"


def test_sensitive_write_is_rejected_before_creating_directories(tmp_path):
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.write_file("new-folder/private.pem", "blocked")
    assert not (tmp_path / "new-folder").exists()


def test_sensitive_directories_cannot_be_requested_explicitly(tmp_path):
    directory = tmp_path / ".aws"
    directory.mkdir()
    (directory / "config").write_text("fixture-only", encoding="utf-8")
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.list_files(".aws")
    with pytest.raises(PermissionError, match="敏感"):
        list_files(str(directory))


def test_public_alias_cannot_read_or_overwrite_a_secret(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("fixture-only-secret", encoding="utf-8")
    alias = tmp_path / "README.md"
    try:
        alias.symlink_to(secret.name)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建测试符号链接：{exc}")
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.read_file(alias.name)
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.write_file(alias.name, "overwritten")
    with pytest.raises(PermissionError, match="敏感"):
        read_file(str(alias))
    assert "README.md" not in sandbox.list_files()
    assert "README.md" not in list_files(str(tmp_path))
    assert secret.read_text(encoding="utf-8") == "fixture-only-secret"


def test_sensitive_alias_is_rejected_even_when_target_is_public(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("ordinary-content", encoding="utf-8")
    alias = tmp_path / ".env.local"
    try:
        alias.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建测试符号链接：{exc}")
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.read_file(alias.name)
    with pytest.raises(PermissionError, match="敏感"):
        sandbox.write_file(alias.name, "overwritten")
    assert target.read_text(encoding="utf-8") == "ordinary-content"


def test_file_listings_hide_private_files_but_keep_source_names(tmp_path):
    (tmp_path / "private.pem").write_text("fixture-only-secret", encoding="utf-8")
    (tmp_path / "README.md").write_text("ordinary-content", encoding="utf-8")
    sandbox = Sandbox(tmp_path, executor=UnsafeSubprocessExecutor())
    for listing in (sandbox.list_files(), list_files(str(tmp_path))):
        assert "private.pem" not in listing
        assert "README.md" in listing
