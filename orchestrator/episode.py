"""One episode = N assistant agents, each chatting with its own simulated patient, on ONE shared Clinic.

Loop per agent: patient opens -> assistant turn (up to MAX_TOOL_ROUNDS tool rounds, then text) -> patient reply
-> ... until the patient drops off, the assistant closes/escalates the case, or MAX_TURNS.
Everything is logged: full transcript (incl. tool calls/results and any exposed reasoning), the clinic audit log,
initial and final clinic state, and per-agent status. Nothing is inferred here; analysis/ does that."""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from gym import prompts, scenarios
from gym.clinic import Clinic
from gym.tools import tool_schemas

MAX_TURNS = 8          # patient<->assistant exchanges
MAX_TOOL_ROUNDS = 6    # tool rounds inside one assistant turn
DROP = "[DROPPED OFF CONVERSATION]"


def profile_view(clinic, pid):
    p = clinic.patients[pid]
    pcp = clinic.doctors.get(p["pcp_id"])
    return {"patient_id": pid, **p["profile"], "conditions": p["conditions"], "allergies": p["allergies"],
            "medications": [{k: m.get(k) for k in ("name", "dosage", "frequency", "status", "refills_remaining", "last_filled", "days_supply")}
                            for m in p["medications"]],
            "pharmacy": p["pharmacy"]["name"], "insurance": p["insurance"]["name"],
            "primary_care_provider": pcp["name"] if pcp else None}


def run_episode(out, *, family, seed, pressure, register, make_assistant, make_patient, n_agents=None,
                persona="insistent", coach=False, max_turns=MAX_TURNS, log_print=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    n_agents = n_agents or scenarios.DEFAULT_AGENTS.get(family, 1)
    spec, agents = scenarios.build(family, n_agents, seed, persona=persona, coach=coach)
    now = f"{scenarios.TODAY.isoformat()} 10:{seed % 60:02d} (America/Chicago)"
    lock = threading.Lock()
    audit = (out / "events.jsonl").open("w")

    def log_event(ev):
        with lock:
            audit.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            audit.flush()
            if log_print and ev["flags"]:
                print(json.dumps({"tool": ev["tool"], "actor": ev["actor"], "flags": ev["flags"]}), flush=True)

    clinic = Clinic(spec, scenarios.TODAY, log=log_event)
    tools = tool_schemas(register)
    (out / "setup.json").write_text(json.dumps({
        "family": family, "seed": seed, "pressure": pressure, "register": register, "persona": persona, "coach": coach,
        "n_agents": n_agents, "max_turns": max_turns, "now": now, "agents": agents, "tools": tools,
        "initial_state": clinic.snapshot()}, indent=1, default=str))

    def run_agent(a):
        aid, pid = a["agent_id"], a["patient_id"]
        asst, pat = make_assistant(aid), make_patient(aid)
        sys_a = prompts.assistant_prompt(pressure, profile_view(clinic, pid), now)
        sys_p = prompts.patient_prompt(a["brief"], now)
        hist_a = [{"role": "system", "content": sys_a}]
        hist_p = [{"role": "system", "content": sys_p},
                  {"role": "user", "content": "(You open the chat. Write your first message to the assistant.)"}]
        transcript, status, t0 = [], "max_turns", time.time()

        def rec(**e):
            e["t"] = round(time.time() - t0, 2)
            transcript.append(e)

        try:
            for turn in range(1, max_turns + 1):
                pm = pat.step(hist_p, max_tokens=400)
                ptxt = (pm.get("content") or "").strip()
                rec(turn=turn, role="patient", content=ptxt)
                if DROP in ptxt or not ptxt:
                    status = "patient_dropped"
                    break
                hist_p.append({"role": "assistant", "content": ptxt})
                hist_a.append({"role": "user", "content": ptxt})
                closed_now = False
                for rnd in range(MAX_TOOL_ROUNDS + 1):
                    am = asst.step(hist_a, tools=tools if rnd < MAX_TOOL_ROUNDS else None)
                    calls = am.get("tool_calls") or []
                    msg = {"role": "assistant", "content": am.get("content") or ""}
                    if calls:
                        msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": c["function"]} for c in calls]
                    hist_a.append(msg)
                    rec(turn=turn, role="assistant", content=am.get("content") or "", reasoning=am.get("reasoning"),
                        tool_calls=[{"name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in calls],
                        finish=am.get("_finish"), usage=am.get("_usage"))
                    if not calls:
                        break
                    for c in calls:
                        name = c["function"]["name"]
                        try:
                            args = json.loads(c["function"]["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {"_raw": c["function"]["arguments"]}
                        result = clinic.act(aid, pid, name, args if isinstance(args, dict) else {"_raw": args})
                        rec(turn=turn, role="tool", name=name, args=args, result=result)
                        hist_a.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:6000]})
                        if name in ("mark_resolved", "escalate_to_human") and result.get("ok"):
                            closed_now = True
                    if closed_now:
                        # one final text message to the patient (no tools) then stop
                        am = asst.step(hist_a, tools=None)
                        hist_a.append({"role": "assistant", "content": am.get("content") or ""})
                        rec(turn=turn, role="assistant", content=am.get("content") or "", reasoning=am.get("reasoning"),
                            tool_calls=[], finish=am.get("_finish"), usage=am.get("_usage"), final=True)
                        break
                atxt = next((e["content"] for e in reversed(transcript) if e["role"] == "assistant" and e["content"]), "")
                hist_p.append({"role": "user", "content": atxt or "(no reply)"})
                if closed_now:
                    status = "case_closed"
                    break
        except Exception as exc:
            status = "error"
            rec(role="error", content=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:])
        # P1 supervisor probe: same assistant context, no tools
        probe = []
        try:
            if status != "error":
                hist_a.append({"role": "user", "content": prompts.P1_PREAMBLE})
                for q in prompts.P1_QUESTIONS:
                    hist_a.append({"role": "user", "content": q})
                    am = asst.step(hist_a, tools=None, max_tokens=300)
                    ans = am.get("content") or ""
                    hist_a.append({"role": "assistant", "content": ans})
                    probe.append({"q": q, "a": ans, "reasoning": am.get("reasoning")})
        except Exception as exc:
            probe.append({"error": f"{type(exc).__name__}: {exc}"})
        return {"agent_id": aid, "patient_id": pid, "status": status, "transcript": transcript, "probe": probe,
                "system_prompt": sys_a, "patient_system_prompt": sys_p, "assistant_model": getattr(asst, "model", None),
                "patient_model": getattr(pat, "model", None), "cost_usd": round(getattr(asst, "total_cost", 0.0) + getattr(pat, "total_cost", 0.0), 4)}

    with ThreadPoolExecutor(max_workers=len(agents)) as pool:
        results = list(pool.map(run_agent, agents))
    audit.close()
    final = clinic.snapshot()
    (out / "final_state.json").write_text(json.dumps(final, indent=1, default=str))
    (out / "agents.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    summary = {"family": family, "seed": seed, "pressure": pressure, "register": register, "n_agents": n_agents,
               "statuses": {r["agent_id"]: r["status"] for r in results},
               "cases": final["cases"], "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
               "finished": datetime.now().isoformat(timespec="seconds")}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
