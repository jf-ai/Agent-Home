"""
Pydantic request/response schemas for the API layer.
"""
from datetime import datetime

from pydantic import BaseModel, field_validator

from agent.types import AgentConfig

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from agent.crud import ContextStatus
    from db.models import AgentRecord, MemoryBlockRecord


# --- Request Schemas ---

class MessageRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message cannot be empty")
        return v


class CreateAgentRequest(BaseModel):
    name: str
    system_instructions: str
    config: AgentConfig


class CreateMemoryBlockRequest(BaseModel):
    label: str
    content: str = ""
    description: str = ""
    char_limit: int = 20000


class UpdateBlockContentRequest(BaseModel):
    content: str


# --- Response Schemas ---

class AgentMetadataResponse(BaseModel):
    id: str
    name: str
    model: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: "AgentRecord") -> "AgentMetadataResponse":
        """Construct from an AgentRecord database model."""
        return cls(
            id=record.id,
            name=record.name,
            model=record.agent_config.model_name,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class AgentContextStatusResponse(BaseModel):
    """One agent's context composition, as of its most recent model response.

    Byte counts are UTF-8 bytes of provider-visible content — the system prompt, and
    the tool definitions minus pydantic-ai metadata that never reaches a provider.
    They are None when the agent has no in-window response yet. Consumers convert to
    tokens themselves; this layer reports facts, not estimates.

    stale: the measurement predates the current context window (compaction advanced it
    since). active: a run is in flight right now — in-process runtime state, not a DB fact.
    """
    id: str
    name: str
    total_tokens: int | None = None
    sys_prompt_bytes: int | None = None
    tool_defs_bytes: int | None = None
    soft_compaction_limit: int
    stale: bool
    active: bool

    @classmethod
    def from_record(
        cls, record: "AgentRecord", status: "ContextStatus", *, active: bool
    ) -> "AgentContextStatusResponse":
        return cls(
            id=record.id,
            name=record.name,
            total_tokens=status.total_tokens,
            sys_prompt_bytes=status.sys_prompt_bytes,
            tool_defs_bytes=status.tool_defs_bytes,
            soft_compaction_limit=record.agent_config.soft_compaction_limit,
            stale=status.stale,
            active=active,
        )


# TODO: MemoryBlock (block_crud.py) is a superset of this — replace MemoryBlockResponse with
# MemoryBlock everywhere and delete this class. Exposes id/agent_id/position/created_at too,
# which clients actually need (id for targeting updates/deletes, position for ordering).
class MemoryBlockResponse(BaseModel):
    @classmethod
    def from_record(cls, block: "MemoryBlockRecord") -> "MemoryBlockResponse":
        return cls(
            label=block.label,
            description=block.description,
            content=block.content,
            char_limit=block.char_limit,
            updated_at=block.updated_at,
        )

    label: str
    description: str
    content: str
    char_limit: int
    updated_at: datetime


class CoreMemoryResponse(BaseModel):
    blocks: list[MemoryBlockResponse]


# TODO: MessageItem returns raw type + content (serialized ModelMessage JSON) for now.
# We're punting display-layer parsing until we have hands-on experience with the format
# and know what the UI actually needs. Revisit when building the UI/CLI connection.
class MessageItem(BaseModel):
    id: str
    seq_id: int     # Where first message in agent's history is msg 0
    type: str       # 'ModelRequest' | 'ModelResponse' | 'Summary'
    content: str    # raw serialized ModelMessage JSON
    timestamp: datetime


class MessagesResponse(BaseModel):
    # list[Any]: display-layer parsing deferred until UI/CLI needs are known (see MessageItem TODO above)
    # TODO: Constrain back to MessageItem once we have the format settled
    messages: list


class HealthResponse(BaseModel):
    status: str


class SystemInstructionsResponse(BaseModel):
    system_instructions: str
