"""
Agent CRUD operations
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from agent.deps import AgentDeps

from agent.types import AgentConfig
from db.models import AgentRecord, MessageRecord, SystemPromptSnapshot, ToolDefinitionSnapshot
from db.connection import ReadOnlySession
from memory.system_prompt_compilation import compile_system_prompt


async def get_agent_record(session: ReadOnlySession, agent_id: str) -> AgentRecord | None:
    """Load agent by ID. Returns None if not found."""
    return await session.get(AgentRecord, agent_id)


async def get_agent_record_by_name(session: ReadOnlySession, name: str) -> AgentRecord | None:
    """Load agent by name. Returns None if not found.

    Efficient single-row lookup — name has a unique constraint in the schema.
    """
    result = await session.execute(select(AgentRecord).where(AgentRecord.name == name))
    return result.scalar_one_or_none()


async def get_all_agents(session: ReadOnlySession) -> list[AgentRecord]:
    """Return all agents on the server."""
    result = await session.execute(select(AgentRecord))
    return list(result.scalars().all())


async def agent_exists(session: ReadOnlySession, agent_id: str) -> bool:
    """Return True if an agent with the given ID exists, without loading the full record. TODO: Unit test"""
    stmt = select(exists().where(AgentRecord.id == agent_id))
    result = await session.execute(stmt)
    return bool(result.scalar())


# --- Context status (read-only, for monitoring consumers) ---

@dataclass(frozen=True)
class ContextStatus:
    """Context composition as of an agent's most recent model response.

    Every field comes from a single MessageRecord: total_tokens is provider-reported
    usage for that request, and the byte counts describe the system prompt and tool
    definitions that request actually used. Taking them from one row is what keeps the
    display self-consistent — after a compaction recompile the live prompt differs from
    the one an older total was measured against.

    Byte counts are UTF-8 bytes of provider-visible content, not tokens: consumers apply
    their own bytes-per-token estimate. None means the agent has no in-window response yet.

    stale means the measurement predates the current context window: compaction advanced
    the pointer since the row was persisted, so the total describes a window that no longer
    exists. It stays stale until the next response is persisted — which, for compaction
    that fires at natural end of turn, can be until the user's next message.
    """
    total_tokens: int | None
    sys_prompt_bytes: int | None
    tool_defs_bytes: int | None
    stale: bool


def tool_defs_wire_bytes(snapshot_id: str, content: str) -> int:
    """UTF-8 bytes of a tool-definition snapshot's provider-visible content.

    Snapshots store dataclasses.asdict(ToolDefinition) — all 17 fields, including
    pydantic-ai internals that never reach a provider (return_schema, kind, sequential,
    defer_loading, toolset_id, capability_id, ...). On a real 9-tool agent that
    overstates the sent payload by ~44% overall and by 5.7x on a tool with a fat
    return_schema and a short description. Only name, description and the parameter
    schema go on the wire, so only those are counted. The provider's JSON envelope
    (the field names around them, ~60 bytes per tool) is not counted: this is a
    slight floor, not an exact figure.

    Subscript access is deliberate. A renamed field must fail loudly rather than
    undercount: snapshots are permanent, so rows written by an older pydantic-ai
    survive a schema change, and a silent miss would report a plausible wrong number.
    description is the one legitimately-optional field — MCP servers are not required
    to provide one, so None counts as empty while a missing key raises.
    """
    total = 0
    for definition in json.loads(content):
        try:
            name = definition["name"]
            description = definition["description"] or ""
            schema = definition["parameters_json_schema"]
        except KeyError as e:
            raise RuntimeError(
                f"Tool definition snapshot {snapshot_id!r} has an unexpected shape "
                f"(missing {e}). Did pydantic-ai's ToolDefinition change?"
            ) from e
        total += (
            len(name.encode())
            + len(description.encode())
            + len(json.dumps(schema, separators=(",", ":")).encode())
        )
    return total


async def get_context_status(
    session: ReadOnlySession, agent_id: str, context_window_start: int
) -> ContextStatus:
    """Return one agent's context composition as of its most recent response.

    The chosen row is the latest message with provider usage data inside the current
    window. It must be the latest NON-NULL total: mid-run the newest row is usually a
    tool-return ModelRequest (total_tokens = None), so "latest message" would blank the
    display for most of a run. Windowing by context_window_start is what makes a cleared
    agent report None instead of its pre-clear total (cleared rows are kept, just excluded).

    sys_prompt_bytes uses octet_length — the byte count is in the value header, so this
    never reads the prompt payload (matters: real prompts are ~100 KB+). SQLite >= 3.43.

    The stale check compares the row's recorded window start against the window start in
    effect now; both are ids of MessageRecords, resolved in the same query.
    """
    current_start = aliased(MessageRecord)
    result = await session.execute(
        select(
            MessageRecord.total_tokens,
            MessageRecord.context_window_start_msg_id,
            func.octet_length(SystemPromptSnapshot.content),
            ToolDefinitionSnapshot.id,
            ToolDefinitionSnapshot.content,
            current_start.id,
        )
        .outerjoin(SystemPromptSnapshot, SystemPromptSnapshot.id == MessageRecord.system_prompt_hash)
        .outerjoin(ToolDefinitionSnapshot, ToolDefinitionSnapshot.id == MessageRecord.tool_definition_hash)
        .outerjoin(
            current_start,
            and_(
                current_start.agent_id == MessageRecord.agent_id,
                current_start.seq_id == context_window_start,
            ),
        )
        .where(
            MessageRecord.agent_id == agent_id,
            MessageRecord.total_tokens.is_not(None),
            MessageRecord.seq_id >= context_window_start,
        )
        .order_by(MessageRecord.seq_id.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return ContextStatus(
            total_tokens=None, sys_prompt_bytes=None, tool_defs_bytes=None, stale=False
        )

    total_tokens, row_window_start_id, sys_prompt_bytes, tool_snapshot_id, tool_content, current_start_id = row
    if sys_prompt_bytes is None or tool_snapshot_id is None or tool_content is None:
        # Snapshot rows are permanent; a missing one means broken referential integrity.
        raise RuntimeError(
            f"Agent {agent_id!r} has a message referencing a missing context snapshot "
            f"(system prompt or tool definitions). The DB is inconsistent."
        )
    return ContextStatus(
        total_tokens=total_tokens,
        sys_prompt_bytes=sys_prompt_bytes,
        tool_defs_bytes=tool_defs_wire_bytes(tool_snapshot_id, tool_content),
        stale=row_window_start_id != current_start_id,
    )


async def replace_agent_config(deps: AgentDeps, config: AgentConfig) -> AgentConfig:
    """Replace agent config in DB. Agent is already verified to exist via deps.

    Resets compaction_warning_fired when soft_compaction_limit is raised — ensures
    the agent gets a fresh warning as the new, larger uncovered zone accumulates.
    Lowering the limit does not reset: compaction will fire sooner, evicting content
    the agent already consolidated when the original warning fired.
    """
    async with deps.write_scope():
        if config.soft_compaction_limit > deps.config.soft_compaction_limit:
            deps.compaction_warning_fired = False
        deps.config = config
    return deps.config


async def replace_system_instructions(deps: AgentDeps, instructions: str) -> str:
    """Replace system instructions in DB and recompile. Agent is already verified to exist via deps."""
    async with deps.write_scope() as session:
        deps.system_instructions = instructions
        await compile_system_prompt(deps, session)
    return deps.system_instructions


async def create_agent_record(
    session: AsyncSession,
    name: str,
    system_instructions: str,
    config: AgentConfig,
) -> AgentRecord:
    """Create a new agent record and flush. Does NOT commit — caller controls
    the transaction boundary (when the agent becomes visible to other requests).
    """
    record = AgentRecord(name=name, system_instructions=system_instructions, agent_config=config)
    session.add(record)
    await session.flush()  # populate record.id
    return record
