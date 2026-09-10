"""CLI to run a grid of MedAgentBench-style episodes.

  python3 -m orchestrator.mab_run --tag fake --fake kill                # zero-API pipeline smoke
  python3 -m orchestrator.mab_run --tag smoke --models openai/gpt-5.6-sol anthropic/claude-sonnet-5 \
      --families pain_zero sedation anticoag --misconfig on off --seeds 0 1 --n-agents 2 --cap 8 --key-file .env

Layout: runs/<tag>/<model>/<family>/<misconfig>/seed<k>/ (skipped if summary.json exists).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gym.mab_tasks import FAMILIES
from orchestrator import llm, mab_fakes
from orchestrator.mab_episode import run_episode

ROOT = Path(__file__).resolve().parents[1]


def slug(m):
    return m.replace("/", "__").replace(":", "_")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--families", nargs="*", default=list(FAMILIES))
    ap.add_argument("--misconfig", nargs="*", default=["on"], choices=["on", "off"])
    ap.add_argument("--seeds", nargs="*", type=int, default=[0])
    ap.add_argument("--n-agents", type=int, default=2)
    ap.add_argument("--max-steps", type=int, default=10)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--cap", type=float, default=5.0)
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--fake", choices=["kill", "safe"], default=None)
    ap.add_argument("--safety-level", choices=["none", "basic", "strict", "strict_hard"], default="none")
    args = ap.parse_args(argv)

    root = ROOT / "runs" / args.tag
    root.mkdir(parents=True, exist_ok=True)
    ledger = llm.Ledger(args.cap)
    key = None if args.fake else llm.load_key(args.key_file)
    models = args.models or (["fake/assistant"] if args.fake else [])
    if not models:
        sys.exit("--models required unless --fake")
    (root / "config.json").write_text(json.dumps({**vars(args), "started": time.time()}, indent=1, default=str))
    cells = [(m, f, mc, s) for m in models for f in args.families for mc in args.misconfig for s in args.seeds]

    def make_factory(model, family):
        if args.fake:
            mk = mab_fakes.factory(family, args.fake, args.n_agents)
            return lambda aid: mk(aid)
        return lambda aid: llm.ORouter(key, model, ledger, effort=args.effort, max_tokens=1400)

    def job(cell):
        m, f, mc, s = cell
        out = root / slug(m) / f / mc / f"seed{s}"
        if (out / "summary.json").exists():
            return cell, "skip"
        if out.exists():
            shutil.rmtree(out)
        mk = make_factory(m, f)
        try:
            summ = run_episode(out, family=f, seed=s, misconfig=(mc == "on"), make_agent=mk,
                               n_agents=args.n_agents, max_steps=args.max_steps, prompt_safety=args.safety_level)
            return cell, f"done ${summ['cost_usd']:.3f} {summ['statuses']}"
        except llm.BudgetExceeded as e:
            return cell, f"BUDGET {e}"
        except Exception as e:
            return cell, f"ERROR {type(e).__name__}: {e}"

    print(f"{len(cells)} cells -> {root}", flush=True)
    with ThreadPoolExecutor(max_workers=args.par) as pool:
        futs = {pool.submit(job, c): c for c in cells}
        for fut in as_completed(futs):
            cell, msg = fut.result()
            print(f"{cell[0]} {cell[1]} {cell[2]} seed{cell[3]}: {msg}", flush=True)
            (root / "ledger.json").write_text(json.dumps(ledger.snapshot(), indent=1))
    print(json.dumps(ledger.snapshot()), flush=True)


if __name__ == "__main__":
    main()
