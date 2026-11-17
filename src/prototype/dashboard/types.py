"""Data types shared by the dashboard fetcher and renderer.

AgentContextStatus is the renderer's input contract: everything the renderer needs to
draw one agent's bar, and nothing more. The fetcher (API call + token estimation)
produces these; the renderer is a pure function of them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContextStatus:
    """Context status of one agent, as token counts per context component.

    Estimation notes (every figure except total_tokens is approximate):
    - tool_tokens / sys_tokens are bytes/4 estimates of the provider-visible tool
      definitions and compiled system prompt. Byte-level tokenizers make bytes/4 a
      slightly better estimator than chars/4 for non-ASCII text — memory blocks are
      full of arrows, em-dashes and emoji, each costing more bytes than chars.
    - total_tokens is the provider-reported usage on the agent's last response
      (input + output tokens for that request) — the only *exact* figure.
    - msg_tokens = total_tokens - tool_tokens - sys_tokens: the residual attributed to
      message history, clamped at 0 (a prompt that grew after the measured response can
      make the raw residual negative).
    - None means "unknown" (an agent with no in-window response yet), not zero.
    """
    name: str
    tool_tokens: int | None
    sys_tokens: int | None
    msg_tokens: int | None
    total_tokens: int | None
    soft_limit: int | None
    # Runtime facts reported by the server, not derived here: active = a run is in
    # flight (the agent lock is held); stale = the figures predate the current
    # context window (compaction advanced it since the measurement).
    active: bool = False
    stale: bool = False
