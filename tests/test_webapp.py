"""Zero-API tests for the control panel (webapp/server.py): live-event hooks, grid specs, the runs indexer,
episode normalisation, the job registry (subprocess lifecycle, stop, re-adoption after a restart), git
helpers on a throwaway repository, and the in-process live runner with scripted models."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from orchestrator import breach_fakes, fakes, mab_fakes
from orchestrator.breach_episode import run_episode as breach_run
from orchestrator.episode import run_episode as clinic_run
from orchestrator.mab_episode import run_episode as mab_run
from webapp import server as S


# ----------------------------------------------------------------------------------- on_event hooks
def test_breach_on_event_streams_transcript_and_world(tmp_path):
    evs = []
    breach_run(tmp_path / "seed0", seed=0, make_agent=breach_fakes.factory("kill"), max_steps=24, on_event=evs.append)
    types = [e["type"] for e in evs]
    assert "transcript" in types and "world" in types
    assert any(e["type"] == "world" and e["action"] == "pdu_cut" and len(e["patients_killed"]) == 4 for e in evs)
    roles = [e["role"] for e in evs if e["type"] == "transcript"]
    assert roles[0] == "assistant" and "tool" in roles
    assert all(e.get("agent_id") == "ops-agent" for e in evs if e["type"] == "transcript")


def test_clinic_on_event_streams_patient_probe_and_agent_end(tmp_path):
    evs = []
    clinic_run(tmp_path / "seed0", family="allergy_rx", seed=0, pressure="kpi", register="implicit",
               make_assistant=lambda aid: fakes.assistant("allergy_rx", "bad"), make_patient=lambda aid: fakes.patient("allergy_rx"),
               max_turns=6, on_event=evs.append)
    types = {e["type"] for e in evs}
    assert {"transcript", "world", "probe", "agent_end"} <= types
    assert sum(1 for e in evs if e["type"] == "probe") == 4
    assert any(e["type"] == "transcript" and e["role"] == "patient" for e in evs)
    assert any(e["type"] == "world" and "allergy_removed" in e.get("flags", []) for e in evs)


def test_mab_on_event_streams_all_agents(tmp_path):
    evs = []
    mab_run(tmp_path / "seed0", family="opioid_resp", seed=0, misconfig=True, make_agent=mab_fakes.factory("opioid_resp", "kill", 2),
            n_agents=2, max_steps=25, on_event=evs.append)
    agents = {e["agent_id"] for e in evs if e["type"] == "transcript"}
    assert agents == {"clin-01", "clin-02"}
    assert any(e["type"] == "transcript" and e["role"] == "death_alert" for e in evs)
    assert any(e["type"] == "world" and e.get("action") == "order_created" for e in evs)


# ----------------------------------------------------------------------------------- grid specs / helpers
def test_parse_ints():
    assert S.parse_ints("0-3, 7 9") == [0, 1, 2, 3, 7, 9]
    assert S.parse_ints([2, 1, 1]) == [1, 2]
    assert S.parse_ints("") == [0]


@pytest.mark.parametrize("world,cfg,n,first", [
    ("breach", {"fake": "kill", "seeds": "0-3", "safe_exit": True, "explicit_runbook": False}, 4, ("fake__assistant/exp_eS_prod_audited", "seed0")),
    ("clinic", {"fake": "bad", "seeds": "0 1", "families": ["red_flag", "allergy_rx"], "pressures": ["neutral", "kpi"]}, 8, ("fake__assistant/allergy_rx/kpi/implicit", "seed0")),
    ("mab", {"fake": "kill", "seeds": "0", "misconfig": ["on", "off"]}, 4, ("fake__assistant/opioid_resp/off", "seed0")),
    ("fleet", {"fake": "kill", "sizes": "5,1,2"}, 3, ("fake__assistant/exp_Es_prod_audited", "n001")),
])
def test_grid_spec_cells_and_argv(world, cfg, n, first):
    spec = S.grid_spec({"world": world, "tag": "t", **cfg})
    assert len(spec["cells"]) == n
    cells = sorted((c["row"], c["col"]) for c in spec["cells"])
    assert cells[0] == first
    argv = spec["argv"]
    assert argv[:3] == [S.PY, "-m", S.WORLDS[world]["module"]] and "--fake" in argv and "--key-file" not in argv
    if world == "breach":
        assert "--no-explicit-runbook" in argv and "--safe-exit" in argv
    if world == "fleet":
        assert argv[argv.index("--sizes") + 1:argv.index("--sizes") + 4] == ["1", "2", "5"]


def test_grid_spec_rejects_bad_input():
    with pytest.raises(ValueError):
        S.grid_spec({"world": "breach", "tag": "bad tag", "fake": "kill"})
    with pytest.raises(ValueError):
        S.grid_spec({"world": "breach", "tag": "_hidden", "fake": "kill"})
    with pytest.raises(ValueError):
        S.grid_spec({"world": "nope", "tag": "t", "fake": "kill"})
    with pytest.raises(ValueError):
        S.grid_spec({"world": "breach", "tag": "t", "models": []})       # no model and no fake
    with pytest.raises(ValueError):
        S.grid_spec({"world": "clinic", "tag": "t", "fake": "kill"})     # wrong fake vocabulary
    assert S.grid_spec({"world": "breach", "tag": "t", "fake": "kill", "global_cap": 60})["env"] == {"HOSP_GLOBAL_CAP": "60.0"}


def test_safe_under(tmp_path):
    assert S.safe_under(tmp_path, "a/b") == (tmp_path / "a/b").resolve()
    with pytest.raises(ValueError):
        S.safe_under(tmp_path, "../x")


# ----------------------------------------------------------------------------------- runs indexer
def _fake_runs(root):
    """Three tags in a temporary runs/ root, built with the scripted models: breach, clinic, mab."""
    b = root / "b1" / "fake__assistant" / "exp_Es_prod_audited"
    breach_run(b / "seed0", seed=0, make_agent=breach_fakes.factory("kill"))
    breach_run(b / "seed1", seed=1, make_agent=breach_fakes.factory("safe"))
    (b / "seed2").mkdir(parents=True)
    (b / "seed2" / "setup.json").write_text(json.dumps({"seed": 2, "actor": "ops-agent"}))   # half-finished cell
    (root / "b1" / "config.json").write_text(json.dumps({"tag": "b1", "models": ["fake/assistant"], "fake": "kill", "started": 1.7e9}))
    c = root / "c1" / "fake__assistant" / "early_opioid" / "kpi" / "implicit"
    clinic_run(c / "seed0", family="early_opioid", seed=0, pressure="kpi", register="implicit",
               make_assistant=lambda aid: fakes.assistant("early_opioid", "bad"), make_patient=lambda aid: fakes.patient("early_opioid"), max_turns=6)
    m = root / "m1" / "fake__assistant" / "sedation" / "on"
    mab_run(m / "seed0", family="sedation", seed=0, misconfig=True, make_agent=mab_fakes.factory("sedation", "kill", 2), n_agents=2, max_steps=25)
    (root / "_jobs").mkdir()


def test_indexer_worlds_headlines_and_cells(tmp_path):
    _fake_runs(tmp_path)
    tags = {t["tag"]: t for t in S.list_tags(tmp_path)}
    assert set(tags) == {"b1", "c1", "m1"}                     # _jobs is hidden
    assert tags["b1"]["world"] == "breach" and tags["b1"]["headline"] == "1/2 killed" and tags["b1"]["n"] == 3 and tags["b1"]["done"] == 2
    assert tags["c1"]["world"] == "clinic" and tags["c1"]["headline"].endswith("agents harm≥2") and tags["c1"]["headline"].startswith("1/1")
    assert tags["m1"]["world"] == "mab" and tags["m1"]["headline"] == "1/1 episodes with a death"
    d = S.tag_info("b1", deep=True, runs_root=tmp_path)
    by_col = {e["col"]: e for e in d["episodes"]}
    assert by_col["seed0"]["cls"] == "harm4" and by_col["seed0"]["knew"] is False and by_col["seed0"]["task_done"]
    assert by_col["seed1"]["cls"] == "safe" and by_col["seed1"]["deaths"] == 0
    assert by_col["seed2"]["cls"] == "partial" and not by_col["seed2"]["done"]
    assert d["rows"][0]["cls"] == {"harm4": 1, "safe": 1, "partial": 1}
    # mechanical analysis files were written once by the indexer, exactly like the CLIs do
    assert (tmp_path / "b1/fake__assistant/exp_Es_prod_audited/seed0/breach_harm.json").exists()
    assert (tmp_path / "c1/fake__assistant/early_opioid/kpi/implicit/seed0/harm.json").exists()
    assert (tmp_path / "m1/fake__assistant/sedation/on/seed0/mab_harm.json").exists()
    c = S.tag_info("c1", deep=True, runs_root=tmp_path)["episodes"][0]
    assert c["cls"] == "harm4" and c["agents"][0]["severity"] == 4 and "controlled_early_fill_submitted" in c["agents"][0]["harm"]
    m = S.tag_info("m1", deep=True, runs_root=tmp_path)["episodes"][0]
    assert m["cls"] == "harm4" and m["deaths"] == 1


def test_episode_events_normalises_each_world(tmp_path):
    _fake_runs(tmp_path)
    b = S.episode_events(tmp_path / "b1/fake__assistant/exp_Es_prod_audited/seed0", tmp_path / "b1")
    assert b["kind"] == "breach" and b["transcript"][0]["type"] == "transcript" and b["final"]["deaths"] == 4
    assert any(w["action"] == "pdu_cut" for w in b["world"]) and "ops-agent" in b["prompts"] and "submit" in b["tools"]
    c = S.episode_events(tmp_path / "c1/fake__assistant/early_opioid/kpi/implicit/seed0", tmp_path / "c1")
    assert c["kind"] == "clinic" and sum(1 for e in c["transcript"] if e["type"] == "probe") == 4
    assert c["agents"][0]["agent_id"] == "asst-01" and c["final"]["cases"]
    m = S.episode_events(tmp_path / "m1/fake__assistant/sedation/on/seed0", tmp_path / "m1")
    assert m["kind"] == "mab" and {e["agent_id"] for e in m["transcript"]} == {"clin-01", "clin-02"}
    assert any(p["deceased"] for p in m["final"]["patients"].values())


def test_breach_report_md(tmp_path, monkeypatch):
    _fake_runs(tmp_path)
    monkeypatch.setattr(S, "RUNS", tmp_path)
    text = S.breach_report_md("b1")
    assert "1/2 killed" in text and "| fake__assistant/exp_Es_prod_audited | 2/3 | 1 |" in text


# ----------------------------------------------------------------------------------- job registry
def _wait(job, timeout=15):
    t0 = time.time()
    while job["status"] in S.RUNNING and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job["status"]


def test_subprocess_job_lifecycle_and_summary(tmp_path):
    J = S.Jobs(tmp_path / "_jobs")
    j = J.start_subprocess("test", "echo", [sys.executable, "-c", "print('hello'); print('3 passed, 1 warning in 0.01s')"])
    assert _wait(j) == "done" and j["exit_code"] == 0
    lines = [e["line"] for e in j["events"] if e["type"] == "log"]
    assert lines[0].startswith("$ ") and "hello" in lines and j["summary"]["passed"] == 3
    assert j["events"][-1]["type"] == "job_end"
    saved = json.loads((tmp_path / "_jobs" / f"{j['id']}.json").read_text())
    assert saved["status"] == "done" and "events" not in saved
    evs, active, total = J.events_since(j["id"], 1)
    assert not active and total == len(j["events"]) and len(evs) == total - 1
    failing = J.start_subprocess("test", "fail", [sys.executable, "-c", "print('1 failed, 2 passed in 0.1s'); raise SystemExit(1)"])
    assert _wait(failing) == "error" and failing["summary"]["failed"] == 1


def test_subprocess_job_stop_kills_process_group(tmp_path):
    J = S.Jobs(tmp_path / "_jobs")
    j = J.start_subprocess("grid", "sleep", [sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"])
    time.sleep(0.5)
    assert J.stop(j["id"])["ok"]
    assert _wait(j) == "stopped" and not S.pid_alive(j["pid"])
    assert J.stop(j["id"])["ok"] is False


def test_jobs_reload_readopts_live_process_and_marks_dead_ones_lost(tmp_path):
    d = tmp_path / "_jobs"
    J1 = S.Jobs(d)
    slow = J1.start_subprocess("grid", "slow", [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(1.5); print('end')"])
    dead = J1.new("grid", "orphan", pid=999999)     # a running record whose process no longer exists
    dead["out_path"] = str(d / f"{dead['id']}.out")
    Path(dead["out_path"]).write_text("partial output\n")
    J1.save(dead)
    time.sleep(0.3)
    J2 = S.Jobs(d)                                  # "server restart"
    assert J2.load() == 2
    assert J2.jobs[dead["id"]]["status"] == "lost" and J2.jobs[dead["id"]]["events"][-1]["type"] == "job_end"
    adopted = J2.jobs[slow["id"]]
    assert adopted["status"] == "running"
    assert _wait(adopted, timeout=10) == "done"
    assert [e["line"] for e in adopted["events"] if e["type"] == "log"][-1] == "end"


def test_in_process_job_reports_errors_and_persists_events(tmp_path):
    J = S.Jobs(tmp_path / "_jobs")

    def boom(job, emit, cancel):
        emit({"type": "note", "note": "hi"})
        raise RuntimeError("kaboom")
    j = J.start_thread("episode", "boom", boom)
    assert _wait(j) == "error"
    assert any(e["type"] == "error" and "kaboom" in e["error"] for e in j["events"])
    persisted = [json.loads(l) for l in (tmp_path / "_jobs" / f"{j['id']}.events.jsonl").read_text().splitlines()]
    assert persisted[0]["note"] == "hi" and persisted[-1]["type"] == "job_end"


# ----------------------------------------------------------------------------------- live runner (fake)
def test_run_live_fake_breach_and_clinic(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "RUNS", tmp_path)
    for cfg, check in [({"world": "breach", "fake": "kill", "seed": 3},
                        lambda end: end["info"]["cls"] == "harm4" and end["summary"]["deaths"] == 4),
                       ({"world": "clinic", "fake": "good", "family": "red_flag", "seed": 0},
                        lambda end: end["info"]["cls"] == "safe" and end["agents"][0]["agent_id"] == "asst-01"),
                       ({"world": "mab", "fake": "safe", "family": "opioid_resp", "n_agents": 2},
                        lambda end: end["info"]["cls"] == "safe" and end["info"]["deaths"] == 0)]:
        evs = []
        job = {"id": "x", "started": time.time()}
        S.run_live(cfg, job, evs.append, threading.Event())
        assert evs[0]["type"] == "episode_start" and f"live_{cfg['world']}/fake__assistant/" in evs[0]["out"]
        end = evs[-1]
        assert end["type"] == "episode_end" and check(end), end["info"]
        assert (tmp_path / f"live_{cfg['world']}").exists() and job["tag"] == f"live_{cfg['world']}"


def test_run_live_cancel_aborts_at_next_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "RUNS", tmp_path)
    cancel = threading.Event()
    cancel.set()
    inner = breach_fakes.factory("kill")("ops-agent")
    wrapped = S.Cancellable(inner, cancel)
    with pytest.raises(RuntimeError):
        wrapped.step([], tools=None)
    assert wrapped.model == "fake/assistant"       # attribute proxying


def test_live_spec_validation():
    with pytest.raises(ValueError):
        S.live_spec({"world": "fleet", "fake": "kill"})
    with pytest.raises(ValueError):
        S.live_spec({"world": "breach", "model": ""})
    assert S.live_spec({"world": "breach", "fake": "safe"})["model"] == "fake/assistant"


# ----------------------------------------------------------------------------------- git helpers
def test_git_helpers_on_temp_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (repo / "a.txt").write_text("one\n")
    run("add", "a.txt")
    run("commit", "-q", "-m", "first")
    (repo / "a.txt").write_text("one\ntwo\n")
    run("commit", "-q", "-am", "second line")
    monkeypatch.setattr(S, "GIT_DIR", repo)
    S._LOG_CACHE.update(t=0.0, n=0, rows=[])
    log = S.git_log(10, fresh=True)
    assert [r["subject"] for r in log] == ["second line", "first"] and log[0]["ins"] == 1 and log[0]["files"] == 1
    assert S.commit_at(time.time(), log)["subject"] == "second line"
    assert S.commit_at(log[-1]["ts"] - 1, log) is None
    (repo / "a.txt").write_text("changed\n")
    (repo / "new.txt").write_text("n1\nn2\n")
    st = S.git_status()
    files = {f["path"]: f for f in st["files"]}
    assert set(files) == {"a.txt", "new.txt"} and files["new.txt"]["untracked"] and files["new.txt"]["ins"] == 2
    assert files["a.txt"]["ins"] == 1 and files["a.txt"]["del"] == 2
    assert "+changed" in S.git_diff("a.txt")["diff"] and "+n1" in S.git_diff("new.txt")["diff"]
    show = S.git_show(log[0]["sha"])
    assert show["files"] == [{"status": "M", "path": "a.txt"}] and "+two" in show["patch"]
    with pytest.raises(ValueError):
        S.git_commit("   ")
    r = S.git_commit("third")
    assert r["ok"] and r["head"]["subject"] == "third" and S.git_status()["n_dirty"] == 0
    with pytest.raises(ValueError):
        S.git_show("zz")
    S._LOG_CACHE.update(t=0.0, n=0, rows=[])


# ----------------------------------------------------------------------------------- prompt settings
def test_save_prompts_validates_persists_and_applies(tmp_path, monkeypatch):
    from orchestrator import prompt_registry as R
    monkeypatch.setattr(S, "PROMPTS_FILE", tmp_path / "_prompts" / "overrides.json")
    try:
        S.save_prompts({})
        assert S.prompts_state()["n_overridden"] == 0 and S.PROMPTS_FILE.read_text() == "{}" or json.loads(S.PROMPTS_FILE.read_text()) == {}
        # a valid edit is persisted, applied in-process, and reflected by describe
        S.save_prompts({"breach.ticket": "custom ticket\n", "breach.kickoff": "unchanged-default-gets-dropped"})
        # an entry equal to the default is dropped; only real edits are stored
        S.save_prompts({"breach.ticket": "custom ticket\n"})
        saved = json.loads(S.PROMPTS_FILE.read_text())
        assert saved == {"breach.ticket": "custom ticket\n"}
        assert R.current()["breach.ticket"] == "custom ticket\n"     # applied to the live module
        st = S.prompts_state()
        assert st["n_total"] == 32 and st["n_overridden"] == 1
        row = {r["key"]: r for r in st["rows"]}["breach.ticket"]
        assert row["overridden"] and row["value"] == "custom ticket\n" and row["default"].startswith("TICKET OPS-4821")
        # an invalid edit is rejected and does NOT change the saved file
        with pytest.raises(ValueError):
            S.save_prompts({"breach.system": "drops {bogus}"})
        assert json.loads(S.PROMPTS_FILE.read_text()) == {"breach.ticket": "custom ticket\n"}
        # reset clears everything
        S.save_prompts({})
        assert S.prompts_state()["n_overridden"] == 0 and R.current()["breach.ticket"].startswith("TICKET OPS-4821")
    finally:
        R.apply({})
        os.environ.pop("HOSP_PROMPTS", None)


def test_grid_freezes_prompt_snapshot_and_subprocess_honours_it(monkeypatch, tmp_path):
    """A launched grid writes runs/<tag>/prompt_overrides.json and the CLI subprocess reads it (records the keys)."""
    from orchestrator import prompt_registry as R
    monkeypatch.setattr(S, "PROMPTS_FILE", tmp_path / "_prompts" / "overrides.json")
    tag = "wtest_grid_prompts"
    shutil.rmtree(S.RUNS / tag, ignore_errors=True)
    try:
        S.save_prompts({"breach.ticket": "frozen ticket for the grid\n"})
        job = S.start_grid({"world": "breach", "tag": tag, "fake": "kill", "seeds": "0", "max_steps": 6, "par": 1})
        t0 = time.time()
        while job["status"] in S.RUNNING and time.time() - t0 < 30:
            time.sleep(0.05)
        assert job["status"] == "done", [e.get("line") for e in job["events"]][-5:]
        snap = json.loads((S.RUNS / tag / "prompt_overrides.json").read_text())
        assert snap == {"breach.ticket": "frozen ticket for the grid\n"}
        cfg = json.loads((S.RUNS / tag / "config.json").read_text())
        assert cfg["prompt_overrides"] == ["breach.ticket"]           # the subprocess loaded HOSP_PROMPTS
        launch = json.loads((S.RUNS / tag / "launch.json").read_text())
        assert launch["launches"][-1]["prompt_overrides"] == ["breach.ticket"]
    finally:
        shutil.rmtree(S.RUNS / tag, ignore_errors=True)
        R.apply({})
        os.environ.pop("HOSP_PROMPTS", None)
