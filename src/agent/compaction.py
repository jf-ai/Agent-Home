"""Compaction functions — Section 3.3

Handles context window management by advancing the message history pointer
when token limits are exceeded.
"""
import logging

from sqlalchemy import func, select

from agent.deps import AgentDeps
from agent.types import AgentConfig
from memory.system_prompt_compilation import compile_system_prompt
from messages.messages import deserialize_messages, load_messages
from db.models import MessageRecord
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

logger = logging.getLogger(__name__)


MIN_MESSAGES_AFTER_COMPACTION = 4


def is_compaction_needed(total_tokens: int | None, config: AgentConfig) -> bool:
    """Check if compaction should be triggered based on token count.

    Returns True when total_tokens exceeds the soft_compaction_limit.
    Returns False and logs a warning when total_tokens is None, which indicates
    no usage data was available for the turn (unexpected in normal operation).
    """
    if total_tokens is None:
        logger.warning("is_compaction_needed called with total_tokens=None; skipping compaction")
        return False
    return total_tokens > config.soft_compaction_limit


async def compact(deps: AgentDeps) -> bool:
    """Advance context_window_start to reduce context size.

    Retention is by MESSAGE COUNT: keep compaction_target_fraction of the
    in-context messages (never fewer than MIN_MESSAGES_AFTER_COMPACTION), then
    advance the pointer. This is just an easy way to do it for now that gives the user
    some confidence that the agent will have some controlled amount of messages left over
    after compact, independent of how full the ctx is from memories and tools.

    Known imprecision (acceptable until the agentic compaction overhaul): message
    sizes vary widely, so count-fraction retention only approximates
    token-fraction retention. Triggering is unaffected — the runner fires
    compaction off the accurate provider total.

    Guarantees:
    - Never evicts the most recent 4 messages
    - No-op if 4 or fewer messages in context
    - Does NOT delete messages (pointer manipulation only)
    - Tool call/return pairs are kept atomic (never split across the boundary)
    - Calls compile_system_prompt after advancing pointer
    """

    async with deps.read_scope() as session:
        messages = await load_messages(
            session, deps.agent_id, start_seq_id=deps.context_window_start
        )

    # small context guard
    if len(messages) <= MIN_MESSAGES_AFTER_COMPACTION:
        return False

    n_msg_to_keep = max(
        MIN_MESSAGES_AFTER_COMPACTION,
        int(deps.config.compaction_target_fraction * len(messages)),
    )

    if n_msg_to_keep >= len(messages):
        return False

    # Ensure tool call/return pairs are never split across the compaction boundary.
    # If the candidate start message is a ToolReturnPart or RetryPromptPart, include
    # the preceding ToolCallPart message too.
    candidate = messages[-n_msg_to_keep]
    if candidate.type == "ModelRequest":
        [deserialized] = deserialize_messages([candidate])
        if any(isinstance(p, (ToolReturnPart, RetryPromptPart)) for p in deserialized.parts):
            n_msg_to_keep += 1

    # A ToolReturnPart/RetryPromptPart can never be the first message in a context window,
    # so in practice this is at most an efficiency thing (turns compact into a no op if n_msg_to_keep == len(messages))
    # rather than a proper safety guard
    if n_msg_to_keep >= len(messages):
        return False

    new_start = messages[-n_msg_to_keep].seq_id
    async with deps.write_scope() as session:
        deps.context_window_start = new_start
        deps.compaction_warning_fired = False  # Reset for next compaction cycle
        await compile_system_prompt(deps, session)
    return True


async def clear_context(deps: AgentDeps) -> bool:
    """Advance context_window_start to the next seq_id, giving the agent a fresh session.

    Messages before the clear remain in the DB (retrievable, not deleted) but are
    excluded from subsequent runs. The compaction warning flag is reset so a fresh
    warning can fire when the new context grows.

    Returns True if the pointer was advanced, False if there was nothing to clear.
    """
    async with deps.read_scope() as session:
        result = await session.execute(
            select(func.max(MessageRecord.seq_id)).where(MessageRecord.agent_id == deps.agent_id)
        )
    max_seq_id = result.scalar()
    if max_seq_id is not None and ((max_seq_id + 1) > deps.context_window_start):
        async with deps.write_scope():
            deps.context_window_start = max_seq_id + 1
            deps.compaction_warning_fired = False
        return True
    return False
