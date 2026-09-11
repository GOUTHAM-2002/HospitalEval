"""CLI: run breach episodes. runs/<tag>/seed<k>/.
  python3 -m orchestrator.breach_run --tag fake --fake kill
  python3 -m orchestrator.breach_run --tag smoke --models openai/gpt-5.6-sol --seeds 0 1 2 3 --cap 6 --key-file .env
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from orchestrator import llm, breach_fakes
from orchestrator.breach_episode import run_episode

ROOT = Path(__file__).resolve().parents[1]


def slug(m):
    return m.replace("/", "__").replace(":", "_")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--seeds", nargs="*", type=int, default=[0])
    ap.add_argument("--max-steps", type=int, default=24)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--cap", type=float, default=5.0)
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--fake", choices=["kill", "safe"], default=None)
    ap.add_argument("--explicit-runbook", dest="explicit_runbook", action="store_true", default=True)
    ap.add_argument("--no-explicit-runbook", dest="explicit_runbook", action="store_false")
    ap.add_argument("--safe-exit", action="store_true")
    ap.add_argument("--framing", choices=["prod", "test"], default="prod")
    ap.add_argument("--oversight", choices=["none", "audited"], default="audited")
    args = ap.parse_args(argv)

    root = ROOT / "runs" / args.tag
    root.mkdir(parents=True, exist_ok=True)
    ledger = llm.Ledger(args.cap)
    key = None if args.fake else llm.load_key(args.key_file)
    models = args.models or (["fake/assistant"] if args.fake else [])
    if not models:
        sys.exit("--models required unless --fake")
    (root / "config.json").write_text(json.dumps({**vars(args), "started": time.time()}, indent=1, default=str))
    cfg = {"explicit_runbook": args.explicit_runbook, "safe_exit": args.safe_exit,
           "framing": args.framing, "oversight": args.oversight}
    cells = [(m, s) for m in models for s in args.seeds]

    def mk(model):
        if args.fake:
            f = breach_fakes.factory(args.fake)
            return lambda actor: f(actor)
        return lambda actor: llm.ORouter(key, model, ledger, effort=args.effort, max_tokens=1600)

    def job(cell):
        m, s = cell
        cond = f"exp_{'E' if args.explicit_runbook else 'e'}{'S' if args.safe_exit else 's'}_{args.framing}_{args.oversight}"
        out = root / slug(m) / cond / f"seed{s}"
        if (out / "summary.json").exists():
            return cell, "skip"
        if out.exists():
            shutil.rmtree(out)
        try:
            summ = run_episode(out, seed=s, make_agent=mk(m), max_steps=args.max_steps, config=cfg)
            return cell, f"done ${summ['cost_usd']:.3f} deaths={summ['deaths']} task_done={summ['task_done']} status={summ['status']}"
        except llm.BudgetExceeded as e:
            return cell, f"BUDGET {e}"
        except Exception as e:
            return cell, f"ERROR {type(e).__name__}: {e}"

    print(f"{len(cells)} cells -> {root}", flush=True)
    with ThreadPoolExecutor(max_workers=args.par) as pool:
        futs = {pool.submit(job, c): c for c in cells}
        for fut in as_completed(futs):
            cell, msg = fut.result()
            print(f"{cell[0]} seed{cell[1]}: {msg}", flush=True)
            (root / "ledger.json").write_text(json.dumps(ledger.snapshot(), indent=1))
    print(json.dumps(ledger.snapshot()), flush=True)


if __name__ == "__main__":
    main()
