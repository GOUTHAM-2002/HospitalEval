"""Batch runner for the data-driven scenario worlds (gym/worlds).

Runs scenarios x models x seeds, each a mini-episode, into runs/<tag>/scenarios/<id>/<model>/seedN_*, and
writes a roll-up cells.json + a short report.md. Resumable: finished (scenario,model,seed) cells are skipped.

  # $0 scripted smoke across every runnable scenario:
  python3 -m orchestrator.mini_run --tag mini_smoke --fake kill --scenarios all --seeds 0

  # real models on a subset:
  python3 -m orchestrator.mini_run --tag mini1 --models anthropic/claude-fable-5.1 openai/gpt-6-astra \
      --scenarios all --seeds 0 1 --cap 2 --global-cap 60 --key-file .env
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from gym.worlds import SPECS, get_spec
from orchestrator.mini_episode import run_episode

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def _slug(s):
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in str(s))


def _done(cell_dir):
    """A cell is done if any seed dir under it has a summary.json."""
    return cell_dir.exists() and any((d / "summary.json").exists() for d in cell_dir.glob("seed*"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--scenarios", nargs="+", default=["all"], help="'all' or a list of scenario ids")
    ap.add_argument("--models", nargs="+", default=[])
    ap.add_argument("--fake", default=None, help="kill|safe for a $0 scripted run (ignores --models)")
    ap.add_argument("--seeds", nargs="+", type=int, default=[0])
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--cap", type=float, default=2.0)
    ap.add_argument("--global-cap", type=float, default=None)
    ap.add_argument("--max-steps", type=int, default=24)
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--key-file", default=None)
    args = ap.parse_args(argv)

    ids = sorted(SPECS) if args.scenarios == ["all"] else args.scenarios
    for i in ids:
        get_spec(i)  # validate existence up front

    agents_desc = [f"fake:{args.fake}"] if args.fake else args.models
    if not agents_desc:
        ap.error("give --fake kill/safe or --models ...")

    make_for = {}
    if not args.fake:
        from orchestrator import llm
        key = llm.load_key(args.key_file)
        if args.global_cap is not None:
            llm.GLOBAL_CAP = args.global_cap

    base = RUNS / args.tag / "scenarios"
    tasks = []
    for sid in ids:
        spec = get_spec(sid)
        for agent in agents_desc:
            for seed in args.seeds:
                cell = base / _slug(sid) / _slug(agent)
                if _done(cell) and seed in [int(d.name.split("_")[0][4:]) for d in cell.glob("seed*") if (d / "summary.json").exists()]:
                    continue
                tasks.append((sid, spec, agent, seed))

    (RUNS / args.tag).mkdir(parents=True, exist_ok=True)
    (RUNS / args.tag / "launch.json").write_text(json.dumps(
        {"tag": args.tag, "argv": argv or [], "scenarios": ids, "agents": agents_desc, "seeds": args.seeds,
         "started": datetime.now().isoformat()}, indent=1))

    def run_one(t):
        sid, spec, agent, seed = t
        out = base / _slug(sid) / _slug(agent) / f"seed{seed}_{datetime.now().strftime('%m%d_%H%M%S')}"
        if args.fake:
            from orchestrator import mini_fakes
            mk = mini_fakes.factory(spec, args.fake)
        else:
            from orchestrator import llm
            ledger = llm.Ledger(args.cap)
            mk = lambda actor: llm.ORouter(key, agent, ledger, effort=args.effort, max_tokens=1600)
        s = run_episode(out, seed=seed, make_agent=mk, spec=spec, max_steps=args.max_steps)
        return sid, agent, seed, s

    results = []
    print(f"running {len(tasks)} cells ({len(ids)} scenarios x {len(agents_desc)} agents x {len(args.seeds)} seeds)")
    with ThreadPoolExecutor(max_workers=args.par) as ex:
        futs = [ex.submit(run_one, t) for t in tasks]
        for f in as_completed(futs):
            sid, agent, seed, s = f.result()
            results.append(s)
            print(f"  {sid:26s} {agent:34s} seed{seed}  -> {s['outcome']:14s} sev{s['severity']} "
                  f"{'DEATHS=' + str(s['deaths']) if s['deaths'] else ''} ${s['cost_usd']}")

    # roll-up
    cells = {}
    for r in results:
        cells.setdefault(r["scenario_id"], []).append(r)
    (RUNS / args.tag / "cells.json").write_text(json.dumps(cells, indent=1, default=str))
    harmed = sum(1 for r in results if r["harmed"])
    print(f"\n{len(results)} episodes · harmed {harmed} · escalated "
          f"{sum(1 for r in results if r['escalated'])} · deaths {sum(r['deaths'] for r in results)}")
    print(f"written to {RUNS / args.tag}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
