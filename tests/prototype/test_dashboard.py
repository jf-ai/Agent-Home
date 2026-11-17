"""Headless tests for the prototype dashboard: fetcher mapping and renderer geometry.

The TUI/loop shell is exercised by James live; these tests cover the pure pieces so the
estimation pipeline and bar math are verified without a terminal.
"""
import httpx
import pytest

from prototype.dashboard.fetcher import fetch_dashboard_statuses, status_from_payload


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
