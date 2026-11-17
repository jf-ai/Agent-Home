"""Fetch AgentContextStatus from an Agent Home server's API.

This module owns ALL remote data access and estimation for the dashboard: the single
HTTP call, and every bytes→tokens estimate. The renderer must never see raw payloads or
estimation arithmetic — it consumes finished AgentContextStatus values.

One request per poll covers every agent (GET /agents/status). The server resolves each
agent's figures from one message row and reports byte counts of provider-visible content,
so nothing here scales with context size or agent count.
"""
import logging

import httpx

from prototype.dashboard.types import AgentContextStatus

logger = logging.getLogger(__name__)

# bytes/4: the historical chars/4 estimator applied to UTF-8 bytes. Good enough for
# proportional display, not for billing.
_BYTES_PER_TOKEN = 4


def _est_tokens(n_bytes: int | None) -> int | None:
    """Estimate tokens from a UTF-8 byte count. None stays None (unknown, not zero)."""
    return None if n_bytes is None else int(n_bytes / _BYTES_PER_TOKEN)


def status_from_payload(payload: dict) -> AgentContextStatus:
    """Convert one /agents/status item into the renderer's input contract."""
    sys_tokens = _est_tokens(payload["sys_prompt_bytes"])
    tool_tokens = _est_tokens(payload["tool_defs_bytes"])
    total_tokens = payload["total_tokens"]

    msg_tokens = None
    if total_tokens is not None and sys_tokens is not None and tool_tokens is not None:
        msg_tokens = max(total_tokens - sys_tokens - tool_tokens, 0)

    return AgentContextStatus(
        name=payload["name"],
        tool_tokens=tool_tokens,
        sys_tokens=sys_tokens,
        msg_tokens=msg_tokens,
        total_tokens=total_tokens,
        soft_limit=payload["soft_compaction_limit"],
        active=payload["active"],
        stale=payload["stale"],
    )


async def fetch_dashboard_statuses(client: httpx.AsyncClient) -> list[AgentContextStatus]:
    """Fetch every agent's context status in a single request."""
    agents = (await client.get("/agents/status")).raise_for_status().json()
    return [status_from_payload(item) for item in agents]
