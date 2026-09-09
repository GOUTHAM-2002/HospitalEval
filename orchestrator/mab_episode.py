"""One MedAgentBench-style episode: N covering-clinician agents share ONE FHIR server.

No patient dialogue. Each agent gets its (impossible) assignment and runs tool-calling steps against
the shared server. Agents are stepped in interleaved rounds so peer writes accumulate and can be
discovered. An agent stops when it calls submit_task or exhausts its step budget.

Logs everything: full per-agent transcript (assistant text, exposed reasoning, tool calls+results),
the server Provenance/audit stream, initial and final FHIR state, per-agent status.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gym import mab_tasks
from gym.fhir import FHIRServer
from gym.mab_prompts import system_prompt
from gym.mab_tools import TOOLS

MAX_STEPS = 10   # tool-calling steps per agent


def _dispatch(server, actor, name, args):
    a = dict(args or {})
    m = {
        "get_patient": lambda: server.get_patient(actor, a.get("patient_id")),
        "search_observations": lambda: server.search_observations(actor, a.get("patient_id"), a.get("code")),
        "list_allergies": lambda: server.list_allergies(actor, a.get("patient_id")),
        "list_medications": lambda: server.list_medications(actor, a.get("patient_id")),
        "create_medication_order": lambda: server.create_medication_order(
            actor, a.get("patient_id"), a.get("drug"), a.get("dose"), a.get("unit"),
            a.get("route", "oral"), a.get("status", "active"), bool(a.get("safety_override", False)),
            a.get("cosigner"), a.get("note")),
        "discontinue_medication": lambda: server.discontinue_medication(
            actor, a.get("patient_id"), a.get("medication_id"), a.get("reason")),
        "search_ward_orders": lambda: server.search_ward_orders(actor, a.get("ward", mab_tasks.WARD)),
        "send_communication": lambda: server.send_communication(
            actor, a.get("ward", mab_tasks.WARD), a.get("subject", ""), a.get("body", "")),
        "read_communications": lambda: server.read_communications(actor, a.get("ward", mab_tasks.WARD)),
    }
    fn = m.get(name)
    if not fn:
        return {"ok": False, "error": f"Unknown tool {name}"}
    try:
        return fn()
    except Exception as exc:  # never crash the run
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_episode(out, *, family, seed, misconfig, make_agent, n_agents=2, max_steps=MAX_STEPS, log_print=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    spec, agents = mab_tasks.build(family, n_agents, seed, misconfig=misconfig)
    lock = threading.RLock()
    audit = (out / "provenance.jsonl").open("w")

    def log_prov(ev):
        with lock:
            audit.write(json.dumps(ev, default=str) + "\n")
            audit.flush()

    server = FHIRServer(spec, misconfig=misconfig, log=log_prov)
    (out / "setup.json").write_text(json.dumps({
        "family": family, "seed": seed, "misconfig": misconfig, "n_agents": n_agents, "ward": mab_tasks.WARD,
        "max_steps": max_steps, "agents": agents, "tools": TOOLS, "initial_state": server.snapshot()},
        indent=1, default=str))

    states = {}
    for a in agents:
        cli = make_agent(a["agent_id"])
        states[a["agent_id"]] = {
            "client": cli, "patient_id": a["patient_id"], "status": "running",
            "hist": [{"role": "system", "content": system_prompt(a["task"], mab_tasks.WARD)},
                     {"role": "user", "content": "Begin. Read the record first, then act."}],
            "transcript": [], "t0": time.time(), "steps": 0}

    def step(aid):
        st = states[aid]
        if st["status"] != "running":
            return
        cli = st["client"]
        try:
            am = cli.step(st["hist"], tools=TOOLS)
        except Exception as exc:
            st["status"] = "error"
            st["transcript"].append({"role": "error", "content": f"{type(exc).__name__}: {exc}",
                                     "trace": traceback.format_exc()[-1500:]})
            return
        st["steps"] += 1
        calls = am.get("tool_calls") or []
        msg = {"role": "assistant", "content": am.get("content") or ""}
        if calls:
            msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": c["function"]} for c in calls]
        st["hist"].append(msg)
        st["transcript"].append({"step": st["steps"], "role": "assistant", "content": am.get("content") or "",
                                 "reasoning": am.get("reasoning"), "finish": am.get("_finish"),
                                 "usage": am.get("_usage"),
                                 "tool_calls": [{"name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in calls]})
        if not calls:
            if not (am.get("content") or "").strip():
                st["status"] = "stalled"
            return
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c["function"]["arguments"]}
            if name == "submit_task":
                result = {"ok": True, "message": "Shift signed off."}
                st["status"] = "submitted"
            else:
                with lock:
                    result = _dispatch(server, aid, name, args if isinstance(args, dict) else {})
            st["transcript"].append({"step": st["steps"], "role": "tool", "name": name, "args": args, "result": result})
            st["hist"].append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:5000]})
            if log_print and isinstance(result, dict) and result.get("flags"):
                print(json.dumps({"agent": aid, "tool": name, "flags": result["flags"]}), flush=True)
        # nudge to re-check / finish as budget runs down
        if st["steps"] >= max_steps and st["status"] == "running":
            st["status"] = "max_steps"

    # interleaved rounds
    for _ in range(max_steps + 1):
        active = [a["agent_id"] for a in agents if states[a["agent_id"]]["status"] == "running"]
        if not active:
            break
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            list(pool.map(step, active))
    audit.close()

    final = server.snapshot()
    (out / "final_state.json").write_text(json.dumps(final, indent=1, default=str))
    results = [{"agent_id": aid, "patient_id": st["patient_id"], "status": st["status"], "steps": st["steps"],
                "transcript": st["transcript"], "system_prompt": st["hist"][0]["content"],
                "model": getattr(st["client"], "model", None),
                "cost_usd": round(getattr(st["client"], "total_cost", 0.0), 4)} for aid, st in states.items()]
    (out / "agents.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    summary = {"family": family, "seed": seed, "misconfig": misconfig, "n_agents": n_agents,
               "statuses": {r["agent_id"]: r["status"] for r in results},
               "cost_usd": round(sum(r["cost_usd"] for r in results), 4), "finished": time.time()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
