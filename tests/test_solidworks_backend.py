"""Offline dependency/contract checks; no server process or SolidWorks needed."""
import copy
import hashlib
import inspect
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakes  # noqa: F401

from app.mcp_client.backend import build_check, check_tools, source_check, source_hash, extension_hashes, APPLICATION_TOOL_PARAMETERS
from app.mcp_client.generated_tools import TOOL_KIND, ToolsMixin
from app.mcp_client.solidworks import _clean_env


def declared_tools():
    return [SimpleNamespace(name=name, input_schema={"properties": {param: {} for param in inspect.signature(getattr(ToolsMixin, name)).parameters
                                                                   if param not in ("self", "timeout")}}) for name in TOOL_KIND] + [
        SimpleNamespace(name=name, input_schema={"properties": {p: {} for p in params}})
        for name, params in APPLICATION_TOOL_PARAMETERS.items()]


def test_source_lock_catches_missing_or_modified_code_and_normalizes_git_line_endings():
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        code = repo / "server.py"
        code.write_bytes(b"first\r\nsecond\r\n")
        digest = source_hash(code)
        code.write_bytes(b"first\nsecond\n")
        assert source_hash(code) == digest
        lock = {"version": 1, "revision": "locked", "files": {"server.py": digest}}
        assert source_check(repo, lock)["ok"]
        code.write_text("changed", encoding="utf-8")
        assert not source_check(repo, lock)["ok"]
        assert not source_check(repo, {**lock, "files": {"missing.py": digest}})["ok"]


def test_source_lock_refuses_empty_checksums_and_path_escape():
    with tempfile.TemporaryDirectory() as directory:
        for lock in ({"version": 1, "files": {}}, {"version": 1, "files": {"../outside.py": "bad"}}):
            assert not source_check(Path(directory), lock)["ok"]


def test_tool_contract_detects_missing_tools_and_parameter_drift():
    tools = declared_tools()
    assert check_tools(tools)["ok"]
    assert not check_tools(tools[1:])["ok"]
    assert not check_tools(tools[:-1])["ok"]  # owned inspection tool is required too
    tools[0].input_schema["properties"]["unexpected"] = {}
    report = check_tools(tools)
    assert not report["ok"] and report["parameter_drift"]


def test_build_manifest_binds_the_executable_to_the_locked_source_revision():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        exe = root / "SolidworksExecution.exe"
        exe.write_bytes(b"test executable")
        lock = root / "lock.json"
        lock.write_text(json.dumps({"revision": "locked"}), encoding="utf-8")
        manifest = {"version": 1, "source_revision": "locked", "extensions": extension_hashes(),
                    "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
        with patch("app.config.SOLIDWORKS_LOCK_PATH", lock):
            assert not build_check(exe)["ok"]
            (root / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            assert build_check(exe)["ok"]
            changed_extension = {**manifest, "extensions": {}}
            (root / "build-manifest.json").write_text(json.dumps(changed_extension), encoding="utf-8")
            assert not build_check(exe)["ok"]
            (root / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            exe.write_bytes(b"different executable")
            assert not build_check(exe)["ok"]


def test_adapter_environment_retains_backend_settings_but_strips_provider_secrets():
    with patch.dict("os.environ", {"GEMINI_API_KEY": "hidden", "DOMO_DEVELOPER_TOKEN": "hidden", "DB_PASSWORD": "hidden"}):
        env = _clean_env({"EXECUTION_EXE_PATH": "ours.exe", "SOLIDWORKS_MCP_REPO": "source"})
    assert not any(k in env for k in ("GEMINI_API_KEY", "DOMO_DEVELOPER_TOKEN", "DB_PASSWORD"))
    assert env["EXECUTION_EXE_PATH"] == "ours.exe" and env["SOLIDWORKS_MCP_REPO"] == "source"
