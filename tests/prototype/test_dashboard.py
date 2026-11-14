"""Headless tests for the prototype dashboard: renderer geometry and fetcher math.

The TUI/loop shell is exercised by James live; these tests cover the pure
pieces so the bar math and estimation pipeline are verified without a terminal.
"""
import json

import httpx
import pytest

from prototype.dashboard.fetcher import fetch_agent_status
from prototype.dashboard.render import render_dashboard
from prototype.dashboard.types import AgentContextStatus


def _status(**overrides) -> AgentContextStatus:
    defaults = dict(
        name="test-agent",
        tool_tokens=1000,
        sys_tokens=2000,
        msg_tokens=3000,
        total_tokens=6000,
        soft_limit=8000,
    )
    return AgentContextStatus(**{**defaults, **overrides})


class TestRenderDashboard:
    def test_bar_geometry_nocolor(self):
        """Segments render proportionally in tools/sys/msgs order; marker at soft limit."""
        # scale = max(total=6000, soft=8000) * 1.1 = 8800; inner width = 58
        # tools: 1000/8800*58 = 6, sys: 2000/8800*58 = 13, msgs: 6000/8800*58-19 = 20
        # marker: 8000/8800*58 = 52
        frame = render_dashboard([_status()], width=60, color=False)
        lines = frame.splitlines()
        assert lines[0] == "test-agent"
        bar = lines[1]
        assert bar.startswith("[") and bar.endswith("]")
        inner = bar[1:-1]
        assert len(inner) == 58
        assert inner.startswith("/" * 6)
        assert inner[6:19] == "-" * 13
        assert inner[19:39] == "#" * 20
        assert inner[39:52] == " " * 13
        assert inner[52] == "|"

    def test_marker_inside_messages_when_total_exceeds_limit(self):
        """soft limit marker overlays the bar body when total already passes it."""
        frame = render_dashboard([_status(total_tokens=6000, msg_tokens=3000, soft_limit=4000)], width=60, color=False)
        inner = frame.splitlines()[1][1:-1]
        # scale=6000*1.1=6600, marker col = int(4000/6600*58) = 35 (inside the '#' segment)
        assert inner[35] == "|"

    def test_no_usage_data_placeholder(self):
        frame = render_dashboard([_status(total_tokens=None, msg_tokens=None, tool_tokens=None, sys_tokens=None)], width=60, color=False)
        assert "no usage data yet" in frame

    def test_color_mode_uses_ansi_and_block_chars(self):
        frame = render_dashboard([_status()], width=60, color=True)
        assert "\x1b[96m" in frame  # tools color
        assert "\x1b[92m" in frame  # msgs color
        assert "█" in frame
        assert "/" not in frame.splitlines()[1]  # symbols replaced by blocks

    def test_annotation_labels_tinted_with_segment_colors(self):
        """Each segment's numeric label shares its bar region's color (hue = link)."""
        frame = render_dashboard([_status()], width=60, color=True)
        ann = frame.splitlines()[2]
        assert "\x1b[96mtools=1.0k\x1b[0m" in ann
        assert "\x1b[94msys=2.0k\x1b[0m" in ann
        assert "\x1b[92mmsgs=3.0k\x1b[0m" in ann
        assert "total=6.0k" in ann  # total has no segment color
        assert "\x1b[93m| soft=8.0k\x1b[0m" in ann  # marker color, as before

    def test_annotations_line_has_numbers(self):
        frame = render_dashboard([_status()], width=60, color=False)
        ann = frame.splitlines()[2]
        assert "tools=1.0k" in ann
        assert "sys=2.0k" in ann
        assert "msgs=3.0k" in ann
        assert "total=6.0k" in ann
        assert "soft=8.0k" in ann


def _mock_api(app_state: dict) -> httpx.AsyncClient:
    """Build an httpx client whose transport serves canned Agent Home API responses."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        agent_id = "agent-1"
        if path == "/agents":
            return httpx.Response(200, json=[{"id": agent_id, "name": "Opus"}])
        if path == f"/agents/{agent_id}":
            return httpx.Response(200, json={
                "id": agent_id, "name": "Opus", "model": "claude-opus-4-5",
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                "compiled_system_prompt": "x" * 8000,  # -> 2000 sys tokens est
                "context_window_start": 0,
            })
        if path == f"/agents/{agent_id}/config":
            return httpx.Response(200, json={"soft_compaction_limit": 50000})
        if path == f"/agents/{agent_id}/messages":
            return httpx.Response(200, json={"messages": [
                {"id": "m1", "seq_id": 0, "type": "ModelRequest", "content": "{}", "timestamp": "2026-01-01T00:00:00Z", "total_tokens": None},
                {"id": "m2", "seq_id": 1, "type": "ModelResponse", "content": "{}", "timestamp": "2026-01-01T00:00:01Z", "total_tokens": 11000},
            ]})
        if path == f"/agents/{agent_id}/tool-definitions":
            return httpx.Response(200, json={
                "definitions": [{"name": f"tool_{i}", "parameters_json_schema": {"type": "object"}} for i in range(4)],
                "snapshot_id": "abc", "snapshot_created_at": "2026-01-01T00:00:00Z",
            })
        raise AssertionError(f"unexpected path {path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.mark.asyncio
async def test_fetcher_estimation_math():
    """Fetcher produces the renderer's input: est overheads + provider-anchored message tokens."""
    client = _mock_api({})
    try:
        status = await fetch_agent_status(client, "agent-1", "Opus")
    finally:
        await client.aclose()

    assert status.name == "Opus"
    assert status.sys_tokens == 2000          # 8000 chars / 4
    expected_tool_chars = len(json.dumps([
        {"name": f"tool_{i}", "parameters_json_schema": {"type": "object"}} for i in range(4)
    ]))
    assert status.tool_tokens == int(expected_tool_chars / 4)
    assert status.total_tokens == 11000       # exact, from provider usage
    assert status.tool_tokens is not None
    assert status.msg_tokens == 11000 - 2000 - status.tool_tokens
    assert status.soft_limit == 50000


@pytest.mark.asyncio
async def test_fetcher_handles_agent_without_usage():
    """No responses yet -> None total/msg, real estimates for overheads."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agents":
            return httpx.Response(200, json=[])
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": []})
        if path.endswith("/tool-definitions"):
            return httpx.Response(200, json={"definitions": [], "snapshot_id": None, "snapshot_created_at": None})
        if path.endswith("/agent-1"):
            return httpx.Response(200, json={
                "id": "agent-1", "name": "Opus", "model": "claude-opus-4-5",
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                "compiled_system_prompt": "", "context_window_start": 0,
            })
        if path.endswith("/config"):
            return httpx.Response(200, json={"soft_compaction_limit": 50000})
        raise AssertionError(f"unexpected path {path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    try:
        status = await fetch_agent_status(client, "agent-1", "Opus")
    finally:
        await client.aclose()

    assert status.total_tokens is None
    assert status.msg_tokens is None