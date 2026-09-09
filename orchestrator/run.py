"""CLI: run a grid of episodes.

  python3 -m orchestrator.run --tag smoke --models anthropic/claude-sonnet-5 --families early_opioid allergy_rx \
      --pressures neutral kpi --seeds 0 1 --cap 8 --key-file ~/keys.env
  python3 -m orchestrator.run --tag fake --fake bad          # zero-API smoke of the whole pipeline

Layout: runs/<tag>/<model_slug>/<family>/<pressure>/<register>/seed<k>/  (skipped if summary.json exists)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gym.scenarios import FAMILIES
from gym.prompts import PRESSURES
from orchestrator import llm
from orchestrator.episode import run_episode
from orchestrator import fakes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATIENT_MODEL = "openai/gpt-5.6-luna"


def slug(model):
    return model.replace("/", "__").replace(":", "_")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--patient-model", default=DEFAULT_PATIENT_MODEL)
    ap.add_argument("--families", nargs="*", default=list(FAMILIES))
    ap.add_argument("--pressures", nargs="*", default=list(PRESSURES))
    ap.add_argument("--registers", nargs="*", default=["implicit"])
    ap.add_argument("--seeds", nargs="*", type=int, default=[0])
    ap.add_argument("--persona", default="insistent")
    ap.add_argument("--coach", action="store_true")
    ap.add_argument("--n-agents", type=int, default=None)
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--cap", type=float, default=5.0, help="hard USD cap for this run")
    ap.add_argument("--par", type=int, default=4)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--fake", choices=["bad", "good"], default=None, help="zero-API scripted models")
    args = ap.parse_args(argv)

    root = ROOT / "runs" / args.tag
    root.mkdir(parents=True, exist_ok=True)
    ledger = llm.Ledger(args.cap)
    key = None if args.fake else llm.load_key(args.key_file)
    models = args.models or (["fake/assistant"] if args.fake else [])
    if not models:
        sys.exit("--models required unless --fake")
    (root / "config.json").write_text(json.dumps({**vars(args), "started": time.time()}, indent=1, default=str))

    cells = [(m, f, p, r, s) for m in models for f in args.families for p in args.pressures
             for r in args.registers for s in args.seeds]

    def make_factories(model, family):
        if args.fake:
            return (lambda aid: fakes.assistant(family, args.fake)), (lambda aid: fakes.patient(family))
        mk_a = lambda aid: llm.ORouter(key, model, ledger, effort=args.effort, max_tokens=1200)  # noqa: E731
        mk_p = lambda aid: llm.ORouter(key, args.patient_model, ledger, effort="low", max_tokens=400)  # noqa: E731
        return mk_a, mk_p

    def job(cell):
        m, f, p, r, s = cell
        out = root / slug(m) / f / p / r / f"seed{s}"
        if (out / "summary.json").exists():
            return cell, "skip"
        if out.exists():  # half-finished: remove and redo
            import shutil
            shutil.rmtree(out)
        mk_a, mk_p = make_factories(m, f)
        try:
            summary = run_episode(out, family=f, seed=s, pressure=p, register=r, make_assistant=mk_a, make_patient=mk_p,
                                  n_agents=args.n_agents, persona=args.persona, coach=args.coach, max_turns=args.max_turns)
            return cell, f"done ${summary['cost_usd']:.3f} {summary['statuses']}"
        except llm.BudgetExceeded as e:
            return cell, f"BUDGET {e}"
        except Exception as e:
            return cell, f"ERROR {type(e).__name__}: {e}"

    print(f"{len(cells)} cells -> {root}", flush=True)
    with ThreadPoolExecutor(max_workers=args.par) as pool:
        futs = {pool.submit(job, c): c for c in cells}
        for fut in as_completed(futs):
            cell, msg = fut.result()
            print(" ".join(cell[:4]) + f" seed{cell[4]}: {msg}", flush=True)
            (root / "ledger.json").write_text(json.dumps(ledger.snapshot(), indent=1))
    print(json.dumps(ledger.snapshot()), flush=True)


if __name__ == "__main__":
    main()
