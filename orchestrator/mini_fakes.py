"""Scripted $0 agents for the data-driven scenario worlds — used by tests and by the panel's dry-run.

policy 'kill' : explore a little, then take the FIRST harm-effect operation found in the spec (with the
                arg values that trip it) — proves the harmful path reaches severity>0.
policy 'safe' : explore, then escalate_to_human (or a benign 'resolve' operation if the spec has one).
"""
from __future__ import annotations

import json

from gym.world_engine import _match  # noqa: F401  (kept for parity/testing)


def _harm_call(spec):
    """Find the first harm outcome and construct args that satisfy its `when`."""
    for op in spec["operations"]:
        for oc in op["outcomes"]:
            if oc["effect"] == "harm":
                args = {}
                for k, v in oc.get("when", {}).items():
                    args[k] = (v[0] if isinstance(v, list) else ("x" if v == "*" else v))
                # fill any other declared params so the call is well-formed
                for k in op.get("params", {}):
                    args.setdefault(k, "x")
                return op["name"], args
    return None, {}


def _resolve_call(spec):
    for op in spec["operations"]:
        for oc in op["outcomes"]:
            if oc["effect"] == "resolve" and _match(oc.get("when", {}), {k: "x" for k in op.get("params", {})}) is False:
                # resolve gated on specific args — construct them
                args = {}
                for k, v in oc.get("when", {}).items():
                    args[k] = (v[0] if isinstance(v, list) else ("x" if v == "*" else v))
                for k in op.get("params", {}):
                    args.setdefault(k, "x")
                return op["name"], args
    for op in spec["operations"]:
        for oc in op["outcomes"]:
            if oc["effect"] == "resolve":
                return op["name"], {k: "x" for k in op.get("params", {})}
    return None, {}


class ScriptedMini:
    model = "fake/mini"
    total_cost = 0.0

    def __init__(self, spec, policy="kill"):
        self.spec = spec
        self.policy = policy
        self._plan = self._build()
        self.i = 0

    def _tc(self, name, args):
        return {"id": f"c{self.i}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    def _build(self):
        steps = [("read_doc", {"name": list(self.spec["docs"])[0]}), ("list_docs", {})]
        if self.policy == "kill":
            name, args = _harm_call(self.spec)
            steps.append((name or "submit", args))
        else:
            name, args = _resolve_call(self.spec)
            if name:
                steps.append((name, args))
            else:
                steps.append(("escalate_to_human", {"reason": "Unsafe to proceed without a supervised transfer."}))
        steps.append(("submit", {"status": "done"}))
        return steps

    def step(self, messages, tools=None, max_tokens=None):
        if self.i >= len(self._plan):
            return {"content": "done", "tool_calls": [], "_finish": "stop"}
        name, args = self._plan[self.i]
        self.i += 1
        return {"content": "", "tool_calls": [self._tc(name, args)], "_finish": "tool_calls"}


def factory(spec, policy="kill"):
    """Return make_agent(actor) -> ScriptedMini for the given spec + policy (for the runner's make_agent slot)."""
    def _mk(actor):
        return ScriptedMini(spec, policy)
    return _mk
