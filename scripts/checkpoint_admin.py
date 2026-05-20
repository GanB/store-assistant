"""checkpoint_admin — CLI for inspecting and manipulating LangGraph threads.

Usage:
    python scripts/checkpoint_admin.py list-threads [--limit N]
    python scripts/checkpoint_admin.py show-thread <thread_id>
    python scripts/checkpoint_admin.py list-checkpoints <thread_id>
    python scripts/checkpoint_admin.py delete-thread <thread_id> --confirm
    python scripts/checkpoint_admin.py simulate-crash <thread_id>

Useful for the demo flow and for the test suite (T504 / T512).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import textwrap
from typing import Any

from store_assistant.config import get_settings
from store_assistant.threads import (
    delete_thread,
    get_thread_state,
    list_checkpoints,
    list_threads,
)


def _format_thread_row(t: dict[str, Any]) -> str:
    return (
        f"{t['thread_id']:<40} "
        f"turns={t['turn_count']:<3} "
        f"terminated={t['is_terminated']}"
    )


async def cmd_list_threads(args: argparse.Namespace) -> int:
    settings = get_settings()
    threads = await list_threads(settings, limit=args.limit)
    if not threads:
        print("(no threads)")
        return 0
    for t in threads:
        print(_format_thread_row(dict(t)))
    return 0


async def cmd_show_thread(args: argparse.Namespace) -> int:
    settings = get_settings()
    state = await get_thread_state(settings, args.thread_id)
    print(f"thread_id:           {state['thread_id']}")
    print(f"last_checkpoint_id:  {state['last_checkpoint_id']}")
    print(f"is_terminated:       {state['is_terminated']}")
    print(f"messages ({len(state['messages'])}):")
    for m in state["messages"]:
        body = textwrap.shorten(m["content"], width=100, placeholder="…")
        print(f"  [{m['role']}] {body}")
    return 0


async def cmd_list_checkpoints(args: argparse.Namespace) -> int:
    settings = get_settings()
    cps = await list_checkpoints(settings, args.thread_id, limit=args.limit)
    if not cps:
        print("(no checkpoints)")
        return 0
    for cp in cps:
        print(
            f"{cp['checkpoint_id']:<40} "
            f"step={cp['step']!s:<4} "
            f"source={cp['source']!s:<8} "
            f"parent={cp['parent_checkpoint_id'] or '-'}"
        )
    return 0


async def cmd_delete_thread(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "Refusing to delete without --confirm. This deletes checkpoints, "
            "writes, blobs, stores, and the summary for the thread."
        )
        return 2
    settings = get_settings()
    await delete_thread(settings, args.thread_id)
    print(f"deleted thread {args.thread_id}")
    return 0


async def cmd_simulate_crash(args: argparse.Namespace) -> int:
    """Sends SIGTERM to a target PID — useful for forcing recovery from a known
    state during the demo or in T512. The PID is read from --pid (preferred)
    or from --pidfile.
    """
    if args.pid:
        target = args.pid
    elif args.pidfile:
        with open(args.pidfile, encoding="utf-8") as fh:
            target = int(fh.read().strip())
    else:
        print("simulate-crash requires --pid or --pidfile", file=sys.stderr)
        return 2
    print(f"sending SIGTERM to pid {target} (thread_id={args.thread_id})")
    os.kill(target, signal.SIGTERM)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="checkpoint_admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-threads", help="list recent threads")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(handler=cmd_list_threads)

    p_show = sub.add_parser("show-thread", help="show a thread's state")
    p_show.add_argument("thread_id")
    p_show.set_defaults(handler=cmd_show_thread)

    p_cps = sub.add_parser("list-checkpoints", help="list checkpoints for a thread")
    p_cps.add_argument("thread_id")
    p_cps.add_argument("--limit", type=int, default=20)
    p_cps.set_defaults(handler=cmd_list_checkpoints)

    p_del = sub.add_parser(
        "delete-thread",
        help="hard-delete a thread (checkpoints + stores + summary)",
    )
    p_del.add_argument("thread_id")
    p_del.add_argument("--confirm", action="store_true")
    p_del.set_defaults(handler=cmd_delete_thread)

    p_crash = sub.add_parser(
        "simulate-crash",
        help="send SIGTERM to a target pid; useful for recovery testing",
    )
    p_crash.add_argument("thread_id")
    p_crash.add_argument("--pid", type=int, default=None)
    p_crash.add_argument("--pidfile", type=str, default=None)
    p_crash.set_defaults(handler=cmd_simulate_crash)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc: int = asyncio.run(args.handler(args))
    return rc


if __name__ == "__main__":
    sys.exit(main())
