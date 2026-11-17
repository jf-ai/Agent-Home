"""Headless tests for the prototype dashboard: fetcher mapping and renderer geometry.

The TUI/loop shell is exercised by James live; these tests cover the pure pieces so the
estimation pipeline and bar math are verified without a terminal.
"""
import httpx
import pytest

from prototype.dashboard.fetcher import fetch_dashboard_statuses, status_from_payload
from prototype.dashboard.__main__ import _poll_once
from prototype.dashboard.render import render_dashboard
from prototype.dashboard.types import AgentContextStatus


def _payload(**overrides) -> dict:
    """One /agents/status item, shaped as the server returns it."""
    defaults = dict(
        id="agent-1",
        name="Opus",
        total_tokens=11000,
        sys_prompt_bytes=8000,
        tool_defs_bytes=4000,
        soft_compaction_limit=50000,
        stale=False,
        active=False,
    )
    return {**defaults, **overrides}


class TestStatusFromPayload:
    """bytes→token estimation and the provider-anchored message residual."""

    def test_estimates_and_message_residual(self):
        status = status_from_payload(_payload())

        assert status.sys_tokens == 2000  # 8000 bytes / 4
        assert status.tool_tokens == 1000  # 4000 bytes / 4
        assert status.total_tokens == 11000  # exact, provider-reported
        assert status.msg_tokens == 8000  # 11000 - 2000 - 1000
        assert status.soft_limit == 50000
        assert (status.active, status.stale) == (False, False)

    def test_no_response_yet_reports_unknown_not_zero(self):
        """An agent with no in-window response: nothing measured, not zeros."""
        status = status_from_payload(
            _payload(total_tokens=None, sys_prompt_bytes=None, tool_defs_bytes=None)
        )

        assert (status.total_tokens, status.msg_tokens, status.sys_tokens, status.tool_tokens) == (
            None,
            None,
            None,
            None,
        )

    def test_message_residual_clamps_at_zero(self):
        """Estimation error can push the estimated overhead past the provider total.

        bytes/4 overestimates dense JSON (tool schemas) against real tokenization, so on a
        small context the residual goes negative — a display artefact, clamped to 0.
        """
        status = status_from_payload(
            _payload(total_tokens=5000, sys_prompt_bytes=32000, tool_defs_bytes=4000)
        )

        assert (status.sys_tokens, status.tool_tokens) == (8000, 1000)
        assert status.msg_tokens == 0

    def test_runtime_flags_pass_through(self):
        status = status_from_payload(_payload(active=True, stale=True))

        assert (status.active, status.stale) == (True, True)


@pytest.mark.asyncio
async def test_fetch_dashboard_statuses_makes_one_request_for_all_agents():
    """Regression guard: a single route, not a per-agent fan-out."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json=[_payload(), _payload(id="agent-2", name="Sonnet")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    try:
        statuses = await fetch_dashboard_statuses(client)
    finally:
        await client.aclose()

    assert [status.name for status in statuses] == ["Opus", "Sonnet"]
    assert requested == ["/agents/status"]


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
        frame = render_dashboard(
            [_status(total_tokens=6000, msg_tokens=3000, soft_limit=4000)], width=60, color=False
        )
        inner = frame.splitlines()[1][1:-1]
        # scale=6000*1.1=6600, marker col = int(4000/6600*58) = 35 (inside the '#' segment)
        assert inner[35] == "|"

    def test_no_usage_data_placeholder(self):
        frame = render_dashboard(
            [_status(total_tokens=None, msg_tokens=None, tool_tokens=None, sys_tokens=None)],
            width=60,
            color=False,
        )
        assert "no usage data yet" in frame

    def test_color_mode_uses_ansi_and_block_chars(self):
        frame = render_dashboard([_status()], width=60, color=True)
        assert "\x1b[96m" in frame  # tools color
        assert "\x1b[94m" in frame  # msgs color
        assert "█" in frame
        assert "/" not in frame.splitlines()[1]  # symbols replaced by blocks

    def test_annotation_labels_tinted_with_segment_colors(self):
        """Each segment's numeric label shares its bar region's color (hue = link)."""
        frame = render_dashboard([_status()], width=60, color=True)
        ann = frame.splitlines()[2]
        assert "\x1b[96mtools=1.0k\x1b[0m" in ann
        assert "\x1b[38;5;128msys=2.0k\x1b[0m" in ann
        assert "\x1b[94mmsgs=3.0k\x1b[0m" in ann
        assert "total=6.0k" in ann  # total has no segment color
        assert "\x1b[38;5;208m| soft=8.0k\x1b[0m" in ann  # marker color, as before

    def test_annotations_line_has_numbers(self):
        frame = render_dashboard([_status()], width=60, color=False)
        ann = frame.splitlines()[2]
        assert "tools=1.0k" in ann
        assert "sys=2.0k" in ann
        assert "msgs=3.0k" in ann
        assert "total=6.0k" in ann
        assert "soft=8.0k" in ann

    def test_footer_appears_once_per_frame(self):
        """The estimation disclaimer renders once for the whole frame, not per agent."""
        frame = render_dashboard([_status(), _status(name="second")], width=60, color=False)
        lines = frame.splitlines()
        assert frame.count("bytes/4 estimates") == 1
        assert lines[-2] == (
            "tools/sys are bytes/4 estimates of provider-visible content; "
            "msgs = total − (tools+sys); total is provider-reported"
        )
        assert lines[-1] == "(stale) = measured before the most recent compaction; clears at the next response"

    def test_active_tag_renders_next_to_name(self):
        """Active agents get a green ' (active)' tag; inactive get nothing."""
        frame = render_dashboard([_status(active=True), _status(name="idle-agent")], width=60, color=True)
        lines = frame.splitlines()
        assert "test-agent\x1b[92m (active)\x1b[0m" == lines[0]
        assert lines[4] == "idle-agent"  # next agent's name line, no tag

    def test_stale_tag_is_yellow_and_independent_of_active(self):
        """A compacted agent can be stale and running at once: both tags, both colors."""
        frame = render_dashboard(
            [_status(stale=True), _status(name="both", active=True, stale=True)],
            width=60,
            color=True,
        )
        lines = frame.splitlines()
        assert lines[0] == "test-agent\x1b[93m (stale)\x1b[0m"
        assert lines[4] == "both\x1b[92m (active)\x1b[0m\x1b[93m (stale)\x1b[0m"


class TestPollOnceErrorReporting:
    """A monitor must print failures rather than swallow them — and stay alive doing it."""

    @staticmethod
    def _client(handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    async def test_server_error_prints_status_and_server_detail(self):
        """A 500 from /agents/status names the offending agent in `detail` — show it."""
        client = self._client(
            lambda request: httpx.Response(
                500, json={"detail": "RuntimeError: Agent 'abc' has a message referencing a missing snapshot"}
            )
        )
        try:
            statuses, message = await _poll_once(client, width=60, color=False)
        finally:
            await client.aclose()

        assert statuses is None
        assert message.startswith("server error 500: ")
        assert "missing snapshot" in message

    async def test_unexpected_payload_is_reported_not_raised(self):
        """Contract drift (missing fields) must be visible, not a silent blank frame."""
        client = self._client(lambda request: httpx.Response(200, json=[{"name": "Opus"}]))
        try:
            statuses, message = await _poll_once(client, width=60, color=False)
        finally:
            await client.aclose()

        assert statuses is None
        assert message.startswith("unexpected status response: KeyError: ")

    async def test_connection_failure_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = self._client(handler)
        try:
            statuses, message = await _poll_once(client, width=60, color=False)
        finally:
            await client.aclose()

        assert statuses is None
        assert message == "server unreachable: ConnectError: connection refused"
