"""Pure renderer for the context dashboard.

Takes a list of AgentContextStatus (the computed numbers) and produces a terminal frame
as a string. Knows nothing about HTTP, the DB, or estimation — if you are reading a
response payload or dividing byte counts here, that logic belongs in the fetcher.

Frame shape (no-color mode; color mode fills the same geometry with colored blocks
instead of the ASCII symbols):

    Opus (active)
    [////////------------#################            |     ]
     tools=2.1k  sys=4.5k  msgs=31.2k  total=37.8k  | soft=60k
"""
from collections.abc import Sequence

from prototype.dashboard.types import AgentContextStatus

# Segments render in this order left-to-right, sourcing the AgentContextStatus
# attribute named in _SEGMENT_ATTRS.
SEGMENT_ORDER = ("tools", "sys", "msgs")
_SEGMENT_ATTRS = {"tools": "tool_tokens", "sys": "sys_tokens", "msgs": "msg_tokens"}

# Palette entries: (fill char, ANSI color code). No-color mode uses the fill chars only;
# color mode uses '█' blocks tinted with the ANSI codes. 256-color codes used for hues
# basic ANSI lacks (purple, orange).
PALETTE = {
    "tools": ("/", "\x1b[96m"),        # bright cyan
    "sys": ("-", "\x1b[38;5;128m"),    # purple (deep magenta)
    "msgs": ("#", "\x1b[94m"),         # bright blue
    "marker": ("|", "\x1b[38;5;208m"), # orange
    "empty": (" ", ""),
}
RESET = "\x1b[0m"
DIM = "\x1b[2m"
GREEN = "\x1b[92m"
YELLOW = "\x1b[93m"

FOOTER = "tools/sys are bytes/4 estimates of provider-visible content; msgs = total − (tools+sys); total is provider-reported"
FOOTER_STALE = "(stale) = measured before the most recent compaction; clears at the next response"


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def _segment_widths(status: AgentContextStatus, inner: int, scale: float) -> dict[str, int]:
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
    # Headroom keeps the soft-limit marker visibly inside the bar instead of fused
    # against ']' whenever the soft limit is the scale maximum (the healthy case —
    # most bars most of the time).
    scale = max(status.total_tokens or 0, status.soft_limit or 0, 1) * 1.1

    if status.total_tokens is None:
        body = "no usage data yet"
        return f"[{body.center(inner)[:inner]}]"

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
    # Each segment's label is tinted with the segment's bar color, linking the numbers
    # to the regions by hue (order matches the bar left-to-right).
    parts = []
    for seg in SEGMENT_ORDER:
        label = f"{seg}={_fmt_tokens(getattr(status, _SEGMENT_ATTRS[seg]))}"
        ansi = PALETTE[seg][1]
        parts.append(f"{ansi}{label}{RESET}" if color and ansi else label)
    parts.append(f"total={_fmt_tokens(status.total_tokens)}")
    line = "  ".join(parts)
    if status.soft_limit is not None:
        marker = f"| soft={_fmt_tokens(status.soft_limit)}"
        if color:
            line += f"  {PALETTE['marker'][1]}{marker}{RESET}"
        else:
            line += f"  {marker}"
    return line


def _render_name_line(status: AgentContextStatus, color: bool) -> str:
    """Agent name plus independent runtime tags: green (active), yellow (stale).

    Both can appear together: compaction can leave a stale measurement while the next
    run is already in flight.
    """
    tags = []
    if status.active:
        tags.append(f"{GREEN} (active){RESET}" if color else " (active)")
    if status.stale:
        tags.append(f"{YELLOW} (stale){RESET}" if color else " (stale)")
    return status.name + "".join(tags)


def render_dashboard(statuses: Sequence[AgentContextStatus], width: int = 80, color: bool = True) -> str:
    """Render one full dashboard frame (all agents) as a string."""
    inner = width - 2
    frames: list[str] = []
    for status in statuses:
        frames.append(_render_name_line(status, color))
        frames.append(_render_bar(status, inner, color))
        frames.append(" " + _render_annotations(status, color))
        frames.append("")
    footers = [FOOTER, FOOTER_STALE]
    frames.extend(f"{DIM}{f}{RESET}" if color else f for f in footers)
    return "\n".join(frames)
