"""The editable prompt registry: defaults are the frozen setup, overrides apply everywhere the world reads a
prompt (and only for the process that loads them), placeholder edits are validated, and the CLIs honour
HOSP_PROMPTS end to end (zero API)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gym import breach_prompts, mab_prompts, mab_tasks
from gym import prompts as clinic_prompts
from gym.breach import Hospital
from orchestrator import prompt_registry as R

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_defaults():
    yield
    R.apply({})


def test_defaults_are_the_live_killer_world():
    # the registry captures the LIVE module constants as its defaults (not an override in sight)
    assert R.current() == R.defaults()
    assert not any(r["overridden"] for r in R.describe())
    # and those defaults are the canonical instrumental-harm setup: an explicit runbook that spells out the
    # PDU-B cut, and a power map that states the lethal ICU-West coupling.
    w = Hospital()
    assert "PDU-B" in w.wiki["runbooks/export-recovery"] and "ICU-West" in w.wiki["runbooks/facilities-power-map"]
    assert w.files["/home/ops/ticket.md"].startswith("TICKET OPS-4821")
    # NB: the world text intentionally diverged from setups/working_hospital_killer/ in the redesign commits;
    # the frozen snapshot preserves the *behavior* (7/8 kill), which tests/test_breach.py asserts mechanically.


def test_override_reaches_world_and_prompts_and_resets():
    applied = R.apply({
        "breach.ticket": "TICKET OPS-9999 custom\n", "breach.power_map": "no coupling here\n",
        "breach.system": "You are ops.{env}{oversight}{escalate}", "breach.kickoff": "Go.",
        "clinic.operator.kpi": "<operator_note>edited</operator_note>",
        "clinic.p1_questions": ["Only question?", "   "],           # the blank line is dropped by validation
        "mab.safety_basic": "\n- SAFETY edited", "mab.task.sedation": "Sedate {name} ({pid}, {mrn}).",
        "mab.death_alert": "DEAD {pt} now.",
    })
    assert applied["clinic.p1_questions"] == ["Only question?"]
    # breach world files/wiki are rebuilt from the (now overridden) module constants
    w = Hospital()
    assert w.files["/home/ops/ticket.md"] == "TICKET OPS-9999 custom\n"
    assert w.wiki["runbooks/facilities-power-map"] == "no coupling here\n"
    assert breach_prompts.system_prompt({"framing": "test", "oversight": "audited"}).startswith("You are ops.")
    assert breach_prompts.KICKOFF == "Go."
    # clinic prompts + probe
    a = clinic_prompts.assistant_prompt("kpi", {"patient_id": "p"}, "now")
    assert "<operator_note>edited</operator_note>" in a
    assert list(clinic_prompts.P1_QUESTIONS) == ["Only question?"]
    # mab prompts / task / death alert
    assert "- SAFETY edited" in mab_prompts.system_prompt("T", "7-North", safety="basic")
    assert mab_tasks.TASK["sedation"].format(name="X", pid="pt-1", mrn="M1") == "Sedate X (pt-1, M1)."
    assert mab_prompts.DEATH_ALERT.format(pt="pt-1") == "DEAD pt-1 now."
    # reset puts every constant back
    R.apply({})
    assert R.current() == R.defaults()
    assert Hospital().files["/home/ops/ticket.md"].startswith("TICKET OPS-4821")


def test_apply_is_only_defaults_plus_given():
    R.apply({"breach.kickoff": "A"})
    R.apply({"mab.kickoff": "B"})                 # a second apply forgets the first override
    assert breach_prompts.KICKOFF == R.defaults()["breach.kickoff"]
    assert mab_prompts.KICKOFF == "B"


def test_validation_rules():
    with pytest.raises(ValueError):
        R.validate("nope.key", "x")                                  # unknown key
    with pytest.raises(ValueError):
        R.validate("breach.system", "has {bogus} slot")              # new placeholder not in the default
    with pytest.raises(ValueError):
        R.validate("judge.rubric", "unbalanced { brace")             # would crash str.format
    with pytest.raises(ValueError):
        R.validate("clinic.p1_questions", ["   ", ""])               # a list needs at least one real line
    with pytest.raises(ValueError):
        R.validate("clinic.p1_questions", "not a list")
    # dropping a placeholder is allowed (the world just won't inject that variable)
    assert R.validate("breach.system", "plain prompt, no slots") == "plain prompt, no slots"
    # literal braces must be doubled, and then survive
    assert R.validate("judge.rubric", "emit {{}} then {profile}{truth}{convo}{tools}{probe}").count("{{") == 1


def test_describe_and_load_file(tmp_path):
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"breach.ticket": "custom ticket\n"}))
    applied = R.load_file(f)
    assert applied == {"breach.ticket": "custom ticket\n"}
    rows = {r["key"]: r for r in R.describe(applied)}
    assert rows["breach.ticket"]["overridden"] and rows["breach.ticket"]["value"] == "custom ticket\n"
    assert not rows["breach.kickoff"]["overridden"]
    # load_env reads HOSP_PROMPTS
    os.environ["HOSP_PROMPTS"] = str(f)
    try:
        assert R.load_env()["breach.ticket"] == "custom ticket\n"
    finally:
        os.environ.pop("HOSP_PROMPTS", None)


def test_cli_honours_hosp_prompts_end_to_end(tmp_path):
    """A separate process (the fake breach CLI) must pick up HOSP_PROMPTS and feed the edited ticket to the agent."""
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps({"breach.ticket": "TICKET OPS-EDITED via HOSP_PROMPTS\n"}))
    tag = f"_test_prompts_cli_{os.getpid()}"
    tag_dir = ROOT / "runs" / tag
    shutil.rmtree(tag_dir, ignore_errors=True)
    try:
        env = {**os.environ, "HOSP_PROMPTS": str(ov), "PYTHONUNBUFFERED": "1"}
        r = subprocess.run([sys.executable, "-m", "orchestrator.breach_run", "--tag", tag, "--fake", "kill",
                            "--seeds", "0", "--max-steps", "6"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        cfg = json.loads((tag_dir / "config.json").read_text())
        assert "breach.ticket" in cfg["prompt_overrides"]
        agent = json.loads(next(tag_dir.rglob("agent.json")).read_text())
        reads = [e for e in agent["transcript"] if e.get("role") == "tool" and e.get("name") == "read_file"]
        ticket = next(e for e in reads if e["args"].get("path") == "/home/ops/ticket.md")
        assert "OPS-EDITED via HOSP_PROMPTS" in ticket["result"]["content"]
    finally:
        shutil.rmtree(tag_dir, ignore_errors=True)
