"""Pure renderer for the context dashboard.

Takes a list of AgentContextStatus (the computed numbers) and produces a
terminal frame as a string. Knows nothing about HTTP, the DB, or estimation —
if you are reading a response payload or dividing character counts here,
that logic belongs in the fetcher.

Frame shape (no-color mode; color mode fills the same geometry with colored
blocks instead of the ASCII symbols):

    Opus
    [////////------------#################            |     ]
     tools=2.1k  sys=4.5k  msgs=31.2k  total=37.8k  | soft=60k
"""
from collections.abc import Sequence

from prototype.dashboard.types import AgentContextStatus

# Segments render in this order left-to-right, sourcing the AgentContextStatus
# attribute named in _SEGMENT_ATTRS.
SEGMENT_ORDER = ("tools", "sys", "msgs")
_SEGMENT_ATTRS = {"tools": "tool_tokens", "sys": "sys_tokens", "msgs": "msg_tokens"}

# Palette entries: (fill char, ANSI color code). No-color mode uses the fill
# chars only; color mode uses '█' blocks tinted with the ANSI codes.
PALETTE = {
    "tools": ("/", "\x1b[96m"),   # bright cyan
    "sys": ("-", "\x1b[94m"),     # bright blue
    "msgs": ("#", "\x1b[92m"),    # bright green
    "marker": ("|", "\x1b[93m"),  # bright yellow
    "empty": (" ", ""),
}
RESET = "\x1b[0m"


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def _segment_widths(status: AgentContextStatus, inner: int, scale: int) -> dict[str, int]:
    """Proportional segment widths; 'msgs' absorbs rounding remainder."""
    if status.total_tokens is None or status.total_tokens == 0:
        return {seg: 0 for seg in SEGMENT_ORDER}
    widths = {}
    used = 0
    for seg in SEGMENT_ORDER[:-1]:
        value = getattr(status, _SEGMENT_ATTRS[seg]) or 0
        w = int(value / scale * inner)
        widths[seg] = w
        used += w
    widths["msgs"] = max(0, int(status.total_tokens / scale * inner) - used)
    return widths


def _render_bar(status: AgentContextStatus, inner: int, color: bool) -> str:
    # Headroom keeps the soft-limit marker visibly inside the bar instead of
    # fused against ']' whenever the soft limit is the scale maximum (the
    # healthy case — most bars most of the time).
    scale = max(status.total_tokens or 0, status.soft_limit or 0, 1) * 1.1

    if status.total_tokens is None:
        body = "no usage data yet"
        bar = f"[{body.center(inner)[:inner]}]"
        return bar

    widths = _segment_widths(status, inner, scale)
    cells: list[tuple[str, int]] = [(seg, widths[seg]) for seg in SEGMENT_ORDER]

    # Compose inner row as a list so the soft-limit marker can override a cell.
    row: list[str] = []
    for seg, w in cells:
        fill, ansi = PALETTE[seg]
        row.extend([f"{ansi}█{RESET}" if color else fill] * w)
    row.extend([PALETTE["empty"][0]] * (inner - len(row)))

    if status.soft_limit is not None:
        col = min(int(status.soft_limit / scale * inner), inner - 1)
        if color:
            row[col] = f"{PALETTE['marker'][1]}│{RESET}"
        else:
            row[col] = PALETTE["marker"][0]

    return f"[{''.join(row)}]"


def _render_annotations(status: AgentContextStatus, color: bool) -> str:
    parts = [
        f"tools={_fmt_tokens(status.tool_tokens)}",
        f"sys={_fmt_tokens(status.sys_tokens)}",
        f"msgs={_fmt_tokens(status.msg_tokens)}",
        f"total={_fmt_tokens(status.total_tokens)}",
    ]
    line = "  ".join(parts)
    if status.soft_limit is not None:
        marker = f"| soft={_fmt_tokens(status.soft_limit)}"
        if color:
            line += f"  {PALETTE['marker'][1]}{marker}{RESET}"
        else:
            line += f"  {marker}"
    return line


def render_dashboard(statuses: Sequence[AgentContextStatus], width: int = 80, color: bool = True) -> str:
    """Render one full dashboard frame (all agents) as a string."""
    inner = width - 2
    frames: list[str] = []
    for status in statuses:
        frames.append(status.name)
        frames.append(_render_bar(status, inner, color))
        frames.append(" " + _render_annotations(status, color))
        frames.append("")
    return "\n".join(frames)
