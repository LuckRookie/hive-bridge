#!/usr/bin/env python3
"""Talk to the shared Codex app-server daemon over its Unix domain socket.

Connects to the exact same daemon used by Codex Desktop (via `codex app-server proxy`)
and `codex resume` (`/tmp/codex-daemon-<uid>/<hash>`, WebSocket JSON-RPC).
Sessions created here appear live in Codex Desktop (`threadSource: user`).

Subcommands:
  list       List sessions (optionally filtered by --cwd)
  new        Create a session, run its first turn, write turn.json / events.jsonl / summary.json
  send       Send a message to an existing session and wait for the turn to complete
  steer      Inject a message into a currently running turn
  interrupt  Interrupt a currently running turn
  delete     Delete a session

Requires Python 3.9+ and `websockets` >= 13.
"""

import argparse
import asyncio
import glob
import itertools
import json
import os
import stat
import sys
import time
from pathlib import Path

from websockets.asyncio.client import unix_connect

CLIENT_NAME = "hive-bridge-codex"
STATE_DIR = Path(os.environ.get("HIVE_BRIDGE_HOME", Path.home() / ".hive-bridge"))
RUNS_INDEX = STATE_DIR / "runs.jsonl"


def find_socket() -> str:
    override = os.environ.get("CODEX_DAEMON_SOCKET")
    if override and os.path.exists(override) and stat.S_ISSOCK(os.stat(override).st_mode):
        return override
    paths = [
        p
        for p in glob.glob(f"/tmp/codex-daemon-{os.getuid()}/*")
        if not p.endswith(".lock") and os.path.exists(p) and stat.S_ISSOCK(os.stat(p).st_mode)
    ]
    if len(paths) != 1:
        sys.exit(f"expected exactly one Codex daemon socket, found {paths or 'none'}; is the daemon running?")
    return paths[0]


def record_run(entry: dict):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUNS_INDEX, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class Daemon:
    def __init__(self, ws, event_log=None):
        self.ws = ws
        self.event_log = event_log
        self.ids = itertools.count(1)
        self.pending = {}
        self.events = asyncio.Queue()
        self.approvals = []
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, event_log=None):
        ws = await unix_connect(find_socket(), "ws://localhost/", max_size=None)
        d = cls(ws, event_log)
        await d.request(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await d._send({"method": "initialized"})
        return d

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if self.event_log:
                self.event_log.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.event_log.flush()
            if "id" in msg and "method" not in msg:
                fut = self.pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif "id" in msg:
                await self._answer_server_request(msg)
            else:
                await self.events.put(msg)

    async def _answer_server_request(self, msg):
        # Never silently approve privilege escalations: decline, record, and let the turn proceed.
        method, params = msg["method"], msg.get("params", {})
        self.approvals.append(
            {
                "method": method,
                "command": params.get("command"),
                "reason": params.get("reason"),
            }
        )
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            await self._send({"id": msg["id"], "result": {"decision": "decline"}})
        elif method == "item/permissions/requestApproval":
            await self._send({"id": msg["id"], "result": {"permissions": {}}})
        else:
            await self._send(
                {
                    "id": msg["id"],
                    "error": {"code": -32601, "message": f"{CLIENT_NAME} does not handle {method}"},
                }
            )

    async def _send(self, msg):
        await self.ws.send(json.dumps(msg, ensure_ascii=False))

    async def request(self, method, params=None, timeout=120):
        rid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self._send({"id": rid, "method": method, "params": params or {}})
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(f"{method} failed: {msg['error'].get('message', msg['error'])}")
        return msg["result"]

    async def close(self):
        await self.ws.close()
        self.reader.cancel()


def text_input(text: str):
    return [{"type": "text", "text": text, "text_elements": []}]


def read_prompt(args) -> str:
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    if args.prompt_file:
        if args.prompt_file == "-":
            return sys.stdin.read()
        with open(args.prompt_file, encoding="utf-8") as f:
            return f.read()
    if args.text:
        return args.text
    sys.exit("give --prompt-file, --text, or --stdin")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


async def run_turn(d: Daemon, thread_id: str, prompt: str, args, cwd: str = "", name: str = ""):
    params = {"threadId": thread_id, "input": text_input(prompt)}
    if args.model:
        params["model"] = args.model
    if args.effort:
        params["effort"] = args.effort
    if args.output_schema:
        with open(args.output_schema, encoding="utf-8") as f:
            params["outputSchema"] = json.load(f)

    out_dir = os.path.abspath(args.out)
    started_at = time.time()
    turn_id = (await d.request("turn/start", params))["turn"]["id"]
    with open(os.path.join(out_dir, "turn.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "runtime": "codex",
                "threadId": thread_id,
                "turnId": turn_id,
                "cwd": cwd,
                "name": name,
                "startedAt": started_at,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    record_run(
        {
            "runtime": "codex",
            "out": out_dir,
            "threadId": thread_id,
            "turnId": turn_id,
            "cwd": cwd,
            "name": name,
            "status": "running",
            "startedAt": started_at,
            "updatedAt": started_at,
        }
    )
    emit({"event": "turn_started", "threadId": thread_id, "turnId": turn_id, "out": out_dir})

    s = {
        "runtime": "codex",
        "threadId": thread_id,
        "turnId": turn_id,
        "cwd": cwd,
        "status": None,
        "error": None,
        "durationMs": None,
        "finalMessage": None,
        "messages": [],
        "commands": [],
        "fileChanges": [],
        "otherItems": [],
        "approvalsDeclined": d.approvals,
        "tokenUsage": None,
    }
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            ev = await asyncio.wait_for(d.events.get(), max(0.1, deadline - time.time()))
        except asyncio.TimeoutError:
            break
        p = ev.get("params", {})
        if p.get("threadId") != thread_id:
            continue
        method = ev.get("method")
        if method == "item/completed":
            item = p["item"]
            kind = item.get("type")
            if kind == "agentMessage":
                s["messages"].append(item.get("text"))
            elif kind == "commandExecution":
                cmd_item = {k: item.get(k) for k in ("command", "cwd", "exitCode", "durationMs", "status")}
                cmd_item["output"] = (item.get("aggregatedOutput") or "")[-4000:]
                s["commands"].append(cmd_item)
            elif kind == "fileChange":
                s["fileChanges"].append(
                    {
                        "status": item.get("status"),
                        "files": [{"path": c.get("path"), "kind": c.get("kind")} for c in item.get("changes") or []],
                    }
                )
            elif kind not in ("userMessage", "reasoning", "hookPrompt"):
                s["otherItems"].append({"type": kind, "status": item.get("status"), "tool": item.get("tool")})
        elif method == "thread/tokenUsage/updated":
            s["tokenUsage"] = p.get("tokenUsage")
        elif method == "turn/completed" and p["turn"]["id"] == turn_id:
            s["status"] = p["turn"]["status"]
            s["error"] = p["turn"].get("error")
            s["durationMs"] = p["turn"].get("durationMs")
            break
    else:
        s["status"] = "timeout"

    if s["status"] is None:
        s["status"] = "timeout"
    s["finalMessage"] = s["messages"][-1] if s["messages"] else None
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    record_run(
        {
            "runtime": "codex",
            "out": out_dir,
            "threadId": thread_id,
            "turnId": turn_id,
            "cwd": cwd,
            "name": name,
            "status": s["status"],
            "startedAt": started_at,
            "updatedAt": time.time(),
        }
    )
    emit(
        {
            "event": "turn_finished",
            "status": s["status"],
            "commands": len(s["commands"]),
            "fileChanges": len(s["fileChanges"]),
            "approvalsDeclined": len(s["approvalsDeclined"]),
            "summary": summary_path,
        }
    )


async def cmd_list(args):
    d = await Daemon.connect()
    params = {"limit": args.limit, "useStateDbOnly": True}
    if args.cwd:
        params["cwd"] = os.path.abspath(args.cwd)
    for t in (await d.request("thread/list", params)).get("data", []):
        emit(
            {
                "id": t["id"],
                "name": t.get("name") or (t.get("preview") or "")[:60],
                "cwd": t.get("cwd"),
                "status": t.get("status"),
                "updatedAt": t.get("updatedAt"),
            }
        )
    await d.close()


async def cmd_new(args):
    os.makedirs(args.out, exist_ok=True)
    prompt = read_prompt(args)
    with open(os.path.join(args.out, "events.jsonl"), "a", encoding="utf-8") as ev_log:
        d = await Daemon.connect(ev_log)
        cwd = os.path.abspath(args.cwd)
        params = {
            "cwd": cwd,
            "threadSource": "user",
            "sandbox": args.sandbox,
            "approvalPolicy": "on-request",
        }
        if args.model:
            params["model"] = args.model
        thread_id = (await d.request("thread/start", params))["thread"]["id"]
        if args.name:
            await d.request("thread/name/set", {"threadId": thread_id, "name": args.name})
        emit({"event": "thread_started", "threadId": thread_id, "name": args.name, "cwd": cwd})
        await run_turn(d, thread_id, prompt, args, cwd=cwd, name=args.name or "")
        await d.close()


async def cmd_send(args):
    os.makedirs(args.out, exist_ok=True)
    prompt = read_prompt(args)
    with open(os.path.join(args.out, "events.jsonl"), "a", encoding="utf-8") as ev_log:
        d = await Daemon.connect(ev_log)
        r = await d.request("thread/resume", {"threadId": args.thread, "excludeTurns": True})
        thread_obj = r.get("thread", {})
        status = thread_obj.get("status")
        if isinstance(status, dict) and status.get("type") == "active":
            await d.close()
            sys.exit(f"session {args.thread} is running a turn now; wait, steer or interrupt instead")
        await run_turn(
            d,
            args.thread,
            prompt,
            args,
            cwd=thread_obj.get("cwd") or "",
            name=thread_obj.get("name") or "",
        )
        await d.close()


async def cmd_steer(args):
    d = await Daemon.connect()
    r = await d.request(
        "turn/steer",
        {
            "threadId": args.thread,
            "expectedTurnId": args.turn,
            "input": text_input(read_prompt(args)),
        },
    )
    emit({"event": "steered", **r})
    await d.close()


async def cmd_interrupt(args):
    d = await Daemon.connect()
    emit(
        {
            "event": "interrupted",
            **(await d.request("turn/interrupt", {"threadId": args.thread, "turnId": args.turn})),
        }
    )
    await d.close()


async def cmd_delete(args):
    d = await Daemon.connect()
    await d.request("thread/delete", {"threadId": args.thread})
    emit({"event": "deleted", "threadId": args.thread})
    await d.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--cwd")
    p.add_argument("--limit", type=int, default=20)

    def turn_opts(parser):
        parser.add_argument("--prompt-file")
        parser.add_argument("--text")
        parser.add_argument("--stdin", action="store_true")
        parser.add_argument("--model")
        parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
        parser.add_argument("--output-schema", help="JSON Schema file that constrains the final message")
        parser.add_argument("--out", required=True, help="directory for turn.json, events.jsonl, summary.json")
        parser.add_argument("--timeout", type=int, default=6 * 3600, help="seconds to wait for the turn")

    p = sub.add_parser("new")
    p.add_argument("--cwd", required=True)
    p.add_argument("--name")
    p.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    turn_opts(p)

    p = sub.add_parser("send")
    p.add_argument("--thread", required=True)
    turn_opts(p)

    for name in ("steer", "interrupt"):
        p = sub.add_parser(name)
        p.add_argument("--thread", required=True)
        p.add_argument("--turn", required=True, help="the running turn id, from <out>/turn.json")
        if name == "steer":
            p.add_argument("--prompt-file")
            p.add_argument("--text")
            p.add_argument("--stdin", action="store_true")

    p = sub.add_parser("delete")
    p.add_argument("--thread", required=True)

    args = ap.parse_args()
    try:
        asyncio.run(globals()[f"cmd_{args.cmd}"](args))
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
