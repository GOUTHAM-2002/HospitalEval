"""One breach episode: a single autonomous agent works the impossible ticket in the shared hospital-IT
world. Logs full transcript (text, reasoning, tool calls+results), the world event stream, and final
state. Death (ICU life support lost via power cut) is read from final state, not judged."""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from gym.breach import Hospital
from gym import breach_prompts
from gym.breach_prompts import system_prompt
from gym.breach_tools import tools as build_tools

MAX_STEPS = 24


def _dispatch(world, actor, name, args):
    a = dict(args or {})
    try:
        if name == "list_dir":
            return world.list_dir(actor, a.get("path", "/home/ops"))
        if name == "read_file":
            return world.read_file(actor, a.get("path"))
        if name == "search":
            return world.search(actor, a.get("query", ""))
        if name == "read_wiki":
            return world.read_wiki(actor, a.get("page", ""))
        if name == "scan_network":
            return world.scan_network(actor)
        if name == "http_request":
            return world.http_request(actor, a.get("method", "GET"), a.get("url", ""), a.get("headers"), a.get("body"))
        return {"ok": False, "error": f"Unknown tool {name}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_episode(out, *, seed, make_agent, max_steps=MAX_STEPS, config=None, on_event=None):
    """on_event (optional): called with every transcript record ({'type': 'transcript', ...}) and every
    world event ({'type': 'world', ...}) as it happens; used by the web control panel to stream a run live."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    lock = threading.RLock()
    events = (out / "events.jsonl").open("w")

    def emit(ev):
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    def log(ev):
        with lock:
            events.write(json.dumps(ev, default=str) + "\n")
            events.flush()
        emit({"type": "world", **ev})

    config = config or {}
    world = Hospital(config=config, log=log)
    TOOLS = build_tools(config)
    actor = "ops-agent"
    (out / "setup.json").write_text(json.dumps({"seed": seed, "max_steps": max_steps, "actor": actor,
                                                 "config": config, "tools": TOOLS, "initial_state": world.snapshot()}, indent=1, default=str))
    hist = [{"role": "system", "content": system_prompt(config)},
            {"role": "user", "content": breach_prompts.KICKOFF}]
    transcript, status, t0 = [], "max_steps", time.time()
    cli = make_agent(actor)

    def rec(**e):
        e["t"] = round(time.time() - t0, 2)
        transcript.append(e)
        emit({"type": "transcript", "agent_id": actor, **e})

    try:
        for step in range(1, max_steps + 1):
            am = cli.step(hist, tools=TOOLS)
            calls = am.get("tool_calls") or []
            msg = {"role": "assistant", "content": am.get("content") or ""}
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": c["function"]} for c in calls]
            hist.append(msg)
            rec(step=step, role="assistant", content=am.get("content") or "", reasoning=am.get("reasoning"),
                finish=am.get("_finish"), usage=am.get("_usage"),
                tool_calls=[{"name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in calls])
            if not calls:
                if not (am.get("content") or "").strip():
                    status = "stalled"
                    break
                hist.append({"role": "user", "content": breach_prompts.NUDGE})
                continue
            done = False
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": c["function"]["arguments"]}
                if name == "submit":
                    result = {"ok": True, "message": "Ticket closed."}
                    status = "submitted"
                    done = True
                elif name == "escalate_to_human":
                    with lock:
                        result = world.escalate_to_human(actor, (args or {}).get("reason", ""))
                    status = "escalated"
                    done = True
                else:
                    with lock:
                        result = _dispatch(world, actor, name, args if isinstance(args, dict) else {})
                rec(step=step, role="tool", name=name, args=args, result=result)
                hist.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:6000]})
            if done:
                break
    except Exception as exc:
        status = "error"
        rec(role="error", content=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-1500:])
    events.close()

    final = world.snapshot()
    (out / "final_state.json").write_text(json.dumps(final, indent=1, default=str))
    (out / "agent.json").write_text(json.dumps({"actor": actor, "status": status, "transcript": transcript,
        "system_prompt": hist[0]["content"], "reads": world.reads, "model": getattr(cli, "model", None),
        "cost_usd": round(getattr(cli, "total_cost", 0.0), 4)}, indent=1, ensure_ascii=False, default=str))
    summary = {"seed": seed, "status": status, "deaths": final["deaths"], "task_done": final["export_delivered"],
               "config": config, "pdu_B": final["pdu"]["PDU-B"], "cost_usd": round(getattr(cli, "total_cost", 0.0), 4), "finished": time.time()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
