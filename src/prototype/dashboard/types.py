"""Data types shared by the dashboard fetcher and renderer.

AgentContextStatus is the renderer's input contract: everything the renderer
needs to draw one agent's bar, and nothing more. The fetcher (API calls +
token estimation) is responsible for producing these; the renderer is a pure
function of them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContextStatus:
    """Context status of one agent, as token counts per context component.

    Estimation notes (all figures are approximate except where stated):
    - tool_tokens / sys_tokens are chars/4 estimates of the serialized tool
      schemas and compiled system prompt respectively.
    - total_tokens is the provider-reported usage on the agent's last response
      (input + output tokens for that request) — the only *exact* figure.
    - msg_tokens = total_tokens - tool_tokens - sys_tokens, i.e. the residual
      attributed to message history (includes the completion itself; small).
    - None means "unknown" (e.g. an agent that has no responses yet), not zero.
    """
    name: str
    tool_tokens: int | None
    sys_tokens: int | None
    msg_tokens: int | None
    total_tokens: int | None
    soft_limit: int | None