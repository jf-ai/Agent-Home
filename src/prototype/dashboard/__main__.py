"""Context dashboard entry point.

Usage:
    python -m prototype.dashboard --once                # one frame, exit
    python -m prototype.dashboard --interval 3          # live loop (Ctrl-C exits)
    python -m prototype.dashboard --server http://host:8008

The live loop polls the server and re-renders in place. Poll rate adapts to
activity: fast while any agent's data changed since the previous poll, backed
off (×5) while idle. Zero TUI dependencies — plain ANSI cursor repositioning.
"""
import argparse
import asyncio
import sys

import httpx

from prototype.dashboard.fetcher import fetch_dashboard_statuses
from prototype.dashboard.render import render_dashboard

# ANSI: clear screen + home, then home-only for subsequent frames (no flicker)
_CLEAR = "\x1b[2J\x1b[H"
_HOME = "\x1b[H"
_IDLE_BACKOFF = 5


def _frame_signature(statuses) -> tuple:
    """Cheap identity of a frame, for adaptive polling."""
    return tuple(
        (s.name, s.total_tokens, s.soft_limit)
        for s in statuses
    )


async def _poll_once(client: httpx.AsyncClient, width: int, color: bool):
    try:
        statuses = await fetch_dashboard_statuses(client)
    except httpx.HTTPError as e:
        return None, f"server unreachable: {e.__class__.__name__}: {e}"
    return statuses, render_dashboard(statuses, width=width, color=color)


async def run(server: str, interval: float, width: int, once: bool) -> None:
    color = sys.stdout.isatty()
    async with httpx.AsyncClient(base_url=server, timeout=10) as client:
        if once:
            statuses, frame = await _poll_once(client, width, color)
            print(frame if statuses is not None else f"error: {frame}")
            return

        print(_CLEAR, end="")
        prev_sig = None
        backoff = 1
        while True:
            statuses, frame = await _poll_once(client, width, color)
            if statuses is None:
                print(_HOME + frame, end="", flush=True)  # error banner
            else:
                sig = _frame_signature(statuses)
                if sig != prev_sig:
                    backoff = 1
                prev_sig = sig
                print(_HOME + frame, end="", flush=True)
            await asyncio.sleep(interval * backoff)
            backoff = min(backoff * _IDLE_BACKOFF, _IDLE_BACKOFF)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Agent Home context dashboard")
    parser.add_argument("--server", default="http://localhost:8008")
    parser.add_argument("--interval", type=float, default=3.0, help="base poll seconds")
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--once", action="store_true", help="render one frame and exit")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.server, args.interval, args.width, args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()