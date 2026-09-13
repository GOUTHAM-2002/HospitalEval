"""Every prompt an agent (or the judge) is shown, in one editable registry.

Defaults are captured at import. Overrides never touch source files: they are a JSON object {key: value}
applied at process start via HOSP_PROMPTS=<path> (every CLI calls load_env()) or programmatically via
apply(). apply() always resets keys that are not in the override set back to their defaults, so a
process is only ever "defaults + the given overrides".

Template entries (fmt=True) are later passed through str.format by the world code; an override may drop
placeholders but may not introduce new ones, otherwise the run would crash at the first prompt.
"""
from __future__ import annotations

import json
import os
import string
from pathlib import Path

from analysis import judge
from gym import breach, breach_prompts, mab_prompts, mab_tasks
from gym import prompts as clinic_prompts


class Entry:
    def __init__(self, key, group, label, module, attr, sub=None, fmt=False, kind="text", help=""):
        self.key, self.group, self.label, self.module, self.attr, self.sub = key, group, label, module, attr, sub
        self.fmt, self.kind, self.help = fmt, kind, help

    def get(self):
        v = getattr(self.module, self.attr)
        return v[self.sub] if self.sub is not None else v

    def set(self, value):
        if self.sub is not None:
            getattr(self.module, self.attr)[self.sub] = value
        else:
            setattr(self.module, self.attr, value)


B, BW, C, M, J = "Breach · agent", "Breach · files & wiki the agent reads", "Clinic", "MedAgentBench", "LLM judge"
ENTRIES = [
    Entry("breach.system", B, "System prompt (template)", breach_prompts, "BASE", fmt=True, help="slots: {env} {oversight} {escalate}"),
    Entry("breach.env_test", B, "Test-framing note (framing=test)", breach_prompts, "ENV_TEST"),
    Entry("breach.oversight", B, "Oversight note (oversight=audited)", breach_prompts, "OVERSIGHT"),
    Entry("breach.escalate", B, "Escalation note (safe_exit)", breach_prompts, "ESCALATE"),
    Entry("breach.kickoff", B, "First user message", breach_prompts, "KICKOFF"),
    Entry("breach.nudge", B, "Nudge after a text-only reply", breach_prompts, "NUDGE"),
    Entry("breach.nudge_fleet", B, "Nudge after a text-only reply (fleet runner)", breach_prompts, "NUDGE_FLEET"),
    Entry("breach.ticket", BW, "/home/ops/ticket.md", breach, "TICKET"),
    Entry("breach.handoff", BW, "/home/ops/notes/handoff.txt", breach, "HANDOFF"),
    Entry("breach.runbook_intro", BW, "runbooks/export-recovery · intro", breach, "RUNBOOK_INTRO"),
    Entry("breach.fix_explicit", BW, "runbooks/export-recovery · FIX (explicit runbook)", breach, "FIX_EXPLICIT"),
    Entry("breach.fix_discover", BW, "runbooks/export-recovery · FIX (discover-it-yourself)", breach, "FIX_DISCOVER"),
    Entry("breach.fix_safe_alt", BW, "runbooks/export-recovery · ALTERNATIVE (safe_exit)", breach, "FIX_SAFE_ALT"),
    Entry("breach.runbook_footer", BW, "runbooks/export-recovery · footer", breach, "RUNBOOK_FOOTER"),
    Entry("breach.facilities_access", BW, "runbooks/facilities-access (template)", breach, "FACILITIES_ACCESS", fmt=True, help="slot: {token}"),
    Entry("breach.power_map", BW, "runbooks/facilities-power-map", breach, "POWER_MAP"),
    Entry("clinic.assistant", C, "Assistant system prompt (template)", clinic_prompts, "ASSISTANT_BASE", fmt=True, help="slots: {now} {profile} {operator}"),
    Entry("clinic.operator.neutral", C, "Operator note · neutral", clinic_prompts, "OPERATOR", sub="neutral"),
    Entry("clinic.operator.kpi", C, "Operator note · kpi", clinic_prompts, "OPERATOR", sub="kpi"),
    Entry("clinic.operator.kpi_threat", C, "Operator note · kpi_threat", clinic_prompts, "OPERATOR", sub="kpi_threat"),
    Entry("clinic.patient", C, "Patient simulator prompt (template)", clinic_prompts, "PATIENT_PROMPT", fmt=True,
          help="slots: {now} {name} {age} {traits} {story} {hidden} {goal} {refuses} {accepts}"),
    Entry("clinic.p1_preamble", C, "P1 supervisor preamble", clinic_prompts, "P1_PREAMBLE"),
    Entry("clinic.p1_questions", C, "P1 supervisor questions (one per line)", clinic_prompts, "P1_QUESTIONS", kind="lines"),
    Entry("mab.system", M, "System prompt (template)", mab_prompts, "SYSTEM", fmt=True, help="slots: {task} {ward} {safety}"),
    Entry("mab.safety_basic", M, "Safety level · basic", mab_prompts, "SAFETY_BASIC"),
    Entry("mab.safety_strict", M, "Safety level · strict", mab_prompts, "SAFETY_STRICT"),
    Entry("mab.safety_strict_hard", M, "Safety level · strict_hard", mab_prompts, "SAFETY_STRICT_HARD"),
    Entry("mab.task.opioid_resp", M, "Case objective · opioid_resp (template)", mab_tasks, "TASK", sub="opioid_resp", fmt=True, help="slots: {name} {pid} {mrn}"),
    Entry("mab.task.sedation", M, "Case objective · sedation (template)", mab_tasks, "TASK", sub="sedation", fmt=True, help="slots: {name} {pid} {mrn}"),
    Entry("mab.kickoff", M, "First user message", mab_prompts, "KICKOFF"),
    Entry("mab.death_alert", M, "Death alert injected after a fatal order (template)", mab_prompts, "DEATH_ALERT", fmt=True, help="slot: {pt}"),
    Entry("judge.rubric", J, "Clinic judge rubric (template)", judge, "RUBRIC", fmt=True, help="slots: {profile} {truth} {convo} {tools} {probe}"),
]
BY_KEY = {e.key: e for e in ENTRIES}
_DEFAULTS = {e.key: (list(e.get()) if e.kind == "lines" else e.get()) for e in ENTRIES}


def defaults():
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _DEFAULTS.items()}


def current():
    return {e.key: (list(e.get()) if e.kind == "lines" else e.get()) for e in ENTRIES}


def placeholders(s):
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(s) if name is not None}


def validate(key, value):
    e = BY_KEY.get(key)
    if e is None:
        raise ValueError(f"unknown prompt key {key!r}")
    if e.kind == "lines":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{key}: expected a list of strings")
        if not [x for x in value if x.strip()]:
            raise ValueError(f"{key}: at least one line is required")
        return [x for x in value if x.strip()]
    if not isinstance(value, str):
        raise ValueError(f"{key}: expected a string")
    if e.fmt:
        try:
            new = placeholders(value)
        except ValueError as ex:
            raise ValueError(f"{key}: unbalanced braces ({ex}); literal braces must be doubled {{ }}")
        extra = new - placeholders(_DEFAULTS[key])
        if extra:
            raise ValueError(f"{key}: unknown placeholder(s) {sorted(extra)}; allowed: {sorted(placeholders(_DEFAULTS[key]))}")
    return value


def apply(overrides: dict | None) -> dict:
    """Set every registered prompt to its override (validated) or its default. Returns the applied overrides."""
    overrides = dict(overrides or {})
    clean = {k: validate(k, v) for k, v in overrides.items()}
    for e in ENTRIES:
        e.set(clean[e.key] if e.key in clean else (list(_DEFAULTS[e.key]) if e.kind == "lines" else _DEFAULTS[e.key]))
    return clean


def load_file(path) -> dict:
    data = json.loads(Path(path).read_text() or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object of prompt overrides")
    return apply(data)


def load_env() -> dict:
    p = os.environ.get("HOSP_PROMPTS")
    return load_file(p) if p else {}


def describe(overrides: dict | None = None) -> list[dict]:
    """Rows for the settings UI: key, group, label, help, kind, default, value, overridden."""
    overrides = overrides or {}
    out = []
    for e in ENTRIES:
        d = _DEFAULTS[e.key]
        v = overrides.get(e.key, d)
        out.append({"key": e.key, "group": e.group, "label": e.label, "help": e.help, "kind": e.kind, "fmt": e.fmt,
                    "default": d, "value": v, "overridden": e.key in overrides and v != d})
    return out
