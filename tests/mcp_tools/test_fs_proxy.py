"""Tests for fs_proxy environment passthrough functionality."""
import json
import os
import signal
import subprocess
import time
from types import SimpleNamespace

import pytest

from mcp_tools.fs_proxy import (
    _build_passthrough_env,
    _CCResultFormatMiddleware,
    _read_persisted_output,
    _task_pid,
    create_fs_proxy,
)


class TestBuildPassthroughEnv:
    """Tests for _build_passthrough_env helper."""

    def test_returns_empty_dict_for_empty_list(self):
        result = _build_passthrough_env([])
        assert result == {}

    def test_returns_empty_dict_when_vars_not_set(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        result = _build_passthrough_env(["NONEXISTENT_VAR"])
        assert result == {}

    def test_returns_set_vars_only(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_A", "value_a")
        monkeypatch.delenv("TEST_VAR_B", raising=False)
        result = _build_passthrough_env(["TEST_VAR_A", "TEST_VAR_B"])
        assert result == {"TEST_VAR_A": "value_a"}

    def test_returns_all_set_vars(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_A", "value_a")
        monkeypatch.setenv("TEST_VAR_B", "value_b")
        result = _build_passthrough_env(["TEST_VAR_A", "TEST_VAR_B"])
        assert result == {"TEST_VAR_A": "value_a", "TEST_VAR_B": "value_b"}


class TestCreateFsProxy:
    """Tests for create_fs_proxy factory function."""

    def test_env_flows_to_create_proxy(self, mocker):
        """Verify env dict is passed through to create_proxy."""
        mock_create_proxy = mocker.patch("mcp_tools.fs_proxy.create_proxy")
        # create_proxy returns a mock that has the methods we call
        mock_proxy = mocker.MagicMock()
        mock_create_proxy.return_value = mock_proxy

        test_env = {"MY_VAR": "my_value"}
        create_fs_proxy(env=test_env)

        # Verify create_proxy was called with config containing our env
        mock_create_proxy.assert_called_once()
        config_arg = mock_create_proxy.call_args[0][0]
        assert config_arg["mcpServers"]["claude-code"]["env"] == {
            **test_env,
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1",
        }

    def test_required_cwd_var_wins_over_caller_env(self, mocker):
        """The required cwd-isolation value is set last, so a caller can't override it."""
        mock_create_proxy = mocker.patch("mcp_tools.fs_proxy.create_proxy")
        mock_create_proxy.return_value = mocker.MagicMock()

        create_fs_proxy(env={"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "0"})

        config_arg = mock_create_proxy.call_args[0][0]
        assert config_arg["mcpServers"]["claude-code"]["env"] == {
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"
        }

    def test_env_is_only_required_var_when_none(self, mocker):
        """With no env passed, the env is exactly the required cwd-isolation var."""
        mock_create_proxy = mocker.patch("mcp_tools.fs_proxy.create_proxy")
        mock_proxy = mocker.MagicMock()
        mock_create_proxy.return_value = mock_proxy

        create_fs_proxy()

        mock_create_proxy.assert_called_once()
        config_arg = mock_create_proxy.call_args[0][0]
        assert config_arg["mcpServers"]["claude-code"]["env"] == {
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"
        }

    def test_backend_is_claude_code_serve(self, mocker):
        """The proxied backend must be `claude mcp serve` — the pure tool server,
        not the agent-loop wrapper."""
        mock_create_proxy = mocker.patch("mcp_tools.fs_proxy.create_proxy")
        mock_create_proxy.return_value = mocker.MagicMock()

        create_fs_proxy()

        config_arg = mock_create_proxy.call_args[0][0]
        server_config = config_arg["mcpServers"]["claude-code"]
        assert server_config["command"] == "claude"
        assert server_config["args"] == ["mcp", "serve"]

    def test_allowlist_enforced(self, mocker):
        """Only the allowlisted CC tools are exposed — the CC tool surface includes
        session/account-bound tools that must not leak through."""
        mock_create_proxy = mocker.patch("mcp_tools.fs_proxy.create_proxy")
        mock_proxy = mocker.MagicMock()
        mock_create_proxy.return_value = mock_proxy

        create_fs_proxy()

        mock_proxy.enable.assert_called_once()
        kwargs = mock_proxy.enable.call_args.kwargs
        assert kwargs["names"] == {"Read", "Edit", "Write", "Bash"}
        assert kwargs["only"] is True


async def _run_bash_middleware(stdout_json: dict, is_error: bool = False) -> str:
    content = SimpleNamespace(type="text", text=json.dumps(stdout_json))
    result = SimpleNamespace(content=[content], is_error=is_error)

    async def call_next(_context):
        return result

    context = SimpleNamespace(message=SimpleNamespace(name="Bash"))
    await _CCResultFormatMiddleware().on_call_tool(context, call_next)
    return content.text


def _cut_payload(path, size: int) -> dict:
    return {"stdout": "x" * 30000, "stderr": "", "interrupted": False,
            "persistedOutputPath": str(path), "persistedOutputSize": size}


class TestPersistedBashOutput:
    """Large Bash output: serve cuts it and names a file holding the full text."""

    async def test_full_output_read_from_file(self, tmp_path):
        spill = tmp_path / "out.txt"
        spill.write_text("row\n" * 150000)
        text = await _run_bash_middleware(_cut_payload(spill, spill.stat().st_size))
        assert text == ("row\n" * 150000).rstrip("\n")

    async def test_missing_file_gives_cut_with_note(self, tmp_path):
        text = await _run_bash_middleware(_cut_payload(tmp_path / "gone.txt", 600000))
        assert text.startswith("x" * 30000)
        assert "[Output incomplete" in text

    async def test_non_utf8_file_does_not_crash(self, tmp_path):
        spill = tmp_path / "bin.txt"
        spill.write_bytes(b"ok \xff\xfe bytes\n")
        text = await _run_bash_middleware(_cut_payload(spill, spill.stat().st_size))
        assert text.startswith("ok ")
        assert text.endswith(" bytes")

    def test_read_failure_returns_none(self, tmp_path):
        assert _read_persisted_output(str(tmp_path / "gone.txt")) is None
        assert _read_persisted_output(str(tmp_path)) is None  # a directory


class TestTimedOutBash:
    def _payload(self, path) -> dict:
        return {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": 9,
                "timedOutAfterMs": 3000, "backgroundOutputPath": str(path)}

    async def test_output_so_far_read_from_file(self, tmp_path):
        out = tmp_path / "t.output"
        out.write_text("1\n2\n")
        text = await _run_bash_middleware(self._payload(out))
        assert text.startswith("Command timed out after 3000 ms")
        assert text.endswith("Output so far:\n1\n2")

    async def test_missing_file_still_reports_timeout(self, tmp_path):
        text = await _run_bash_middleware(self._payload(tmp_path / "gone.output"))
        assert text.startswith("Command timed out after 3000 ms")
        assert "could not be retrieved" in text

    async def test_reports_pid_of_running_command(self, tmp_path):
        out = tmp_path / "t.output"
        proc = _start_task("sleep 30; echo done", out)
        try:
            text = await _run_bash_middleware(self._payload(out))
        finally:
            _kill_task(proc)
        assert f"Its PID is {proc.pid}." in text


def _start_task(command: str, out) -> subprocess.Popen:
    """A command writing to `out` in its own session, as serve's background tasks run."""
    with open(out, "wb") as f:
        return subprocess.Popen(
            ["sh", "-c", command], stdout=f, stderr=f, start_new_session=True
        )


def _kill_task(proc: subprocess.Popen) -> None:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def _wait_closed(out, timeout: float = 5.0) -> None:
    """proc.wait() reaps only sh; its killed children may close the file a moment later."""
    deadline = time.monotonic() + timeout
    while _task_pid(str(out)) is not None and time.monotonic() < deadline:
        time.sleep(0.05)


class TestTaskPid:
    """Finding a backgrounded command's PID from the processes holding its output file."""

    def test_finds_the_session_leading_shell_not_its_child(self, tmp_path):
        out = tmp_path / "t.output"
        proc = _start_task("sleep 30; echo done", out)  # sh stays, with sleep as its child
        try:
            assert _task_pid(str(out)) == proc.pid
        finally:
            _kill_task(proc)

    def test_none_once_finished(self, tmp_path):
        out = tmp_path / "t.output"
        proc = _start_task("echo hi", out)
        proc.wait()
        _wait_closed(out)
        assert _task_pid(str(out)) is None

    def test_none_for_a_holder_not_leading_its_session(self, tmp_path):
        """A process that merely has the file open (here, the test itself) isn't a task."""
        out = tmp_path / "t.output"
        with open(out, "wb"):
            assert _task_pid(str(out)) is None

    def test_none_for_a_missing_file(self, tmp_path):
        assert _task_pid(str(tmp_path / "gone.output")) is None
