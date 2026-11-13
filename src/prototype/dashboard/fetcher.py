"""Fetch AgentContextStatus from an Agent Home server's API.

This module owns ALL remote data access and estimation for the dashboard:
every HTTP call and every chars/4 token estimate lives here. The renderer
must never see raw payloads or estimation arithmetic — it consumes finished
AgentContextStatus values.

Known inefficiency (accepted for the prototype, see design notes): each poll
does 4 GETs per agent and pulls the full windowed message list even though
only the last response's total_tokens is needed. If the dashboard graduates,
the server-side fix is likely scalar status stored on AgentRecord and updated
at points where the data is already in hand (persist/compile), which also
fixes the static-data re-reading (tools, system prompt barely ever change).
"""
import json
import logging

import httpx

from prototype.dashboard.types import AgentContextStatus

logger = logging.getLogger(__name__)

# chars/4 is the same crude token estimator the compaction code historically
# used; good enough for proportional display, not for billing.
_CHARS_PER_TOKEN = 4


async def fetch_agent_status(client: httpx.AsyncClient, agent_id: str, agent_name: str) -> AgentContextStatus:
    """Fetch one agent's context status. Four calls: detail, config, messages, tools."""
    detail = (await client.get(f"/agents/{agent_id}")).raise_for_status().json()
    config = (await client.get(f"/agents/{agent_id}/config")).raise_for_status().json()
    messages = (await client.get(f"/agents/{agent_id}/messages")).raise_for_status().json()
    tools = (await client.get(f"/agents/{agent_id}/tool-definitions")).raise_for_status().json()

    sys_tokens = int(len(detail["compiled_system_prompt"]) / _CHARS_PER_TOKEN)

    tool_tokens = None
    if tools["definitions"]:
        tool_tokens = int(len(json.dumps(tools["definitions"])) / _CHARS_PER_TOKEN)

    total_tokens = next(
        (m["total_tokens"] for m in reversed(messages["messages"]) if m["total_tokens"] is not None),
        None,
    )

    msg_tokens = None
    if total_tokens is not None:
        overhead = sys_tokens + (tool_tokens or 0)
        msg_tokens = max(total_tokens - overhead, 0)

    soft_limit = config.get("soft_compaction_limit")

    return AgentContextStatus(
        name=agent_name,
        tool_tokens=tool_tokens,
        sys_tokens=sys_tokens,
        msg_tokens=msg_tokens,
        total_tokens=total_tokens,
        soft_limit=soft_limit,
    )


async def fetch_dashboard_statuses(client: httpx.AsyncClient) -> list[AgentContextStatus]:
    """Fetch status for every agent on the server."""
    agents = (await client.get("/agents")).raise_for_status().json()
    return [
        await fetch_agent_status(client, agent["id"], agent["name"])
        for agent in agents
    ]