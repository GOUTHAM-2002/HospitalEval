"""Plots for the scenario study -> results/scenario_study/*.png (vertical grouped bars).

  outcomes_by_model.png : per-model killed / harmed / refused / content-filtered / stalled (grouped columns)
  study_figure.png      : 4-panel science figure from the CoT/response classifier
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "scenario_study"

INK = "#1a1a1a"; FAINT = "#9b9b9b"; LINE = "#e6e6e6"


def _short(m):
    return m.split("/")[-1]


def _style(ax, ymax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LINE)
    ax.tick_params(colors=INK, labelsize=9, length=0)
    ax.grid(axis="y", color=LINE, lw=.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylim(0, ymax)


def _grouped(ax, models, series, ymax=None, pct=False):
    """series = list of (label, color, values[list per model], hatch?)."""
    n = len(series); x = np.arange(len(models)); w = .82 / n
    for i, s in enumerate(series):
        label, color, vals = s[0], s[1], s[2]
        hatch = s[3] if len(s) > 3 else None
        off = (i - (n - 1) / 2) * w
        bars = ax.bar(x + off, vals, w, color=color, label=label, zorder=3, edgecolor="white", linewidth=.6, hatch=hatch)
        for b, v in zip(bars, vals):
            if v > 0:
                ax.text(b.get_x() + b.get_width() / 2, v + (ymax or max(1, max(vals))) * .012,
                        f"{v:.0f}{'%' if pct else ''}", ha="center", va="bottom", fontsize=7.6, color=INK)
    ax.set_xticks(x); ax.set_xticklabels([_short(m) for m in models], rotation=22, ha="right", fontsize=8.5)
    _style(ax, ymax or 1)


def _load():
    eps = json.loads((OUT / "episodes.json").read_text())
    rows = [json.loads(l) for l in (OUT / "classified.jsonl").read_text().splitlines()]
    return eps, rows


def outcomes_by_model(eps):
    by = defaultdict(Counter)
    for e in eps:
        by[e["model"]][e["cls"]] += 1
    models = sorted(by, key=lambda m: -(by[m]["killed"] + by[m]["harm"]))
    series = [
        ("Killed patient", "#d03b3b", [by[m]["killed"] for m in models]),
        ("Harmed (no death)", "#ec835a", [by[m]["harm"] for m in models]),
        ("Refused / gave up", "#bdbdbd", [by[m]["refused"] for m in models]),
        ("Content-filtered", "#8a8a8a", [by[m]["filtered"] for m in models], "///"),
        ("Stalled", "#e0d8c2", [by[m]["stalled"] for m in models]),
    ]
    fig, ax = plt.subplots(figsize=(12, 5.6))
    _grouped(ax, models, series, ymax=32)
    ax.set_ylabel("episodes (out of 30)", color=FAINT, fontsize=9)
    tot = Counter(e["cls"] for e in eps)
    ax.set_title("Did the model take the only task-completing action, which harms a patient?",
                 fontsize=14, color=INK, fontweight="bold", loc="left", pad=56)
    ax.text(0, 1.05, f"30 impossible-task scenarios per model  ·  {len(eps)} episodes  ·  "
            f"{tot['killed']} killed · {tot['harm']} harmed · {tot['refused']} refused · {tot['filtered']} filtered",
            transform=ax.transAxes, fontsize=10, color=FAINT)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, 1.16), frameon=False, fontsize=9, ncol=5, columnspacing=1.2)
    fig.tight_layout()
    fig.savefig(OUT / "outcomes_by_model.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote outcomes_by_model.png")


def study_figure(eps, rows):
    def lab(r, k):
        return (r.get("labels") or {}).get(k)
    by = defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
    models = sorted(by, key=lambda m: -sum(1 for r in by[m] if r["cls"] in ("killed", "harm")))

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("What the models were thinking — chain-of-thought + response analysis (210 episodes)",
                 fontsize=15, fontweight="bold", color=INK, x=.02, ha="left", y=.99)

    # A: eval awareness (grouped columns)
    ax = axes[0][0]
    _grouped(ax, models, [
        ("no sign", "#cfcfcf", [sum(1 for r in by[m] if lab(r, "eval_awareness") == "none") for m in models]),
        ("suspects a test", "#f0b429", [sum(1 for r in by[m] if lab(r, "eval_awareness") == "suspects_test") for m in models]),
        ("names it a safety test", "#d03b3b", [sum(1 for r in by[m] if lab(r, "eval_awareness") == "alignment_eval") for m in models]),
    ], ymax=32)
    ax.set_title("A · Does the agent think it is being tested?", fontsize=11.5, fontweight="bold", loc="left")
    ax.legend(loc="upper right", frameon=False, fontsize=8.2)

    # B: noticed the risk x acted (grouped columns)
    ax = axes[0][1]
    def cat(r):
        if lab(r, "eval_awareness") == "no_cot":
            return None
        noticed = lab(r, "noticed_risk") in ("explicit", "partial")
        acted = r["cls"] in ("killed", "harm")
        return "na" if not noticed else ("acted" if acted else "refused")
    _grouped(ax, models, [
        ("saw risk, acted anyway", "#d03b3b", [sum(1 for r in by[m] if cat(r) == "acted") for m in models]),
        ("saw risk, refused", "#0ca30c", [sum(1 for r in by[m] if cat(r) == "refused") for m in models]),
        ("did not flag the risk", "#cfcfcf", [sum(1 for r in by[m] if cat(r) == "na") for m in models]),
    ], ymax=32)
    ax.set_title("B · Did it notice the danger — and act anyway?", fontsize=11.5, fontweight="bold", loc="left")
    ax.legend(loc="upper right", frameon=False, fontsize=8.2)

    # C: connected the coupling (single column per model, %)
    ax = axes[1][0]
    rate = []
    for m in models:
        eng = [r for r in by[m] if lab(r, "eval_awareness") != "no_cot"]
        rate.append(100 * sum(1 for r in eng if lab(r, "connected_coupling")) / max(1, len(eng)))
    _grouped(ax, models, [("", "#4a4a4a", rate)], ymax=112, pct=True)
    ax.set_ylabel("% of reasoned episodes", color=FAINT, fontsize=9)
    ax.set_title("C · Explicitly connected the action to the hidden harm", fontsize=11.5, fontweight="bold", loc="left")

    # D: headline
    ax = axes[1][1]; ax.axis("off")
    acted = [r for r in rows if r["cls"] in ("killed", "harm")]
    noticed_acted = sum(1 for r in acted if lab(r, "noticed_risk") in ("explicit", "partial"))
    susp = sum(1 for r in rows if lab(r, "eval_awareness") in ("suspects_test", "alignment_eval"))
    misrep = sum(1 for r in rows if lab(r, "misrepresented") is True)
    lines = [(f"{len(acted)}", "episodes took the harmful, task-completing action", "#d03b3b"),
             (f"{noticed_acted} of {len(acted)}", "of those had ALREADY reasoned it was unsafe", "#d03b3b"),
             (f"{susp}", "episodes suspected they were being tested", INK),
             (f"{misrep}", "episodes misrepresented what they had done", INK)]
    y = .92
    for big, small, col in lines:
        ax.text(.02, y, big, fontsize=30, fontweight="bold", color=col, transform=ax.transAxes, va="top")
        ax.text(.02, y - .085, small, fontsize=11, color=INK, transform=ax.transAxes, va="top")
        y -= .245
    ax.set_title("D · The headline", fontsize=11.5, fontweight="bold", loc="left")

    fig.tight_layout(rect=[0, 0, 1, .96])
    fig.savefig(OUT / "study_figure.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote study_figure.png")


def _lab(r, k):
    return (r.get("labels") or {}).get(k)


def _models_by_harm(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
    return by, sorted(by, key=lambda m: -sum(1 for r in by[m] if r["cls"] in ("killed", "harm")))


def _save_single(fig, ax, title, name, legend_series=None):
    ax.set_title(title, fontsize=14, fontweight="bold", color=INK, loc="left",
                 pad=42 if legend_series else 16)
    if legend_series:
        ax.legend(loc="lower center", bbox_to_anchor=(.5, 1.015), ncol=len(legend_series),
                  frameon=False, fontsize=9.5, columnspacing=1.6, handlelength=1.4)
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", name)


def panel_awareness(rows):
    by, models = _models_by_harm(rows)
    fig, ax = plt.subplots(figsize=(11, 5.4))
    ser = [("no sign", "#cfcfcf", [sum(1 for r in by[m] if _lab(r, "eval_awareness") == "none") for m in models]),
           ("suspects a test", "#f0b429", [sum(1 for r in by[m] if _lab(r, "eval_awareness") == "suspects_test") for m in models]),
           ("names it a safety test", "#d03b3b", [sum(1 for r in by[m] if _lab(r, "eval_awareness") == "alignment_eval") for m in models])]
    _grouped(ax, models, ser, ymax=32)
    ax.set_ylabel("episodes", color=FAINT, fontsize=9)
    _save_single(fig, ax, "Does the agent think it is being tested?", "panel_eval_awareness.png", ser)


def panel_notice_act(rows):
    by, models = _models_by_harm(rows)
    def cat(r):
        if _lab(r, "eval_awareness") == "no_cot":
            return None
        noticed = _lab(r, "noticed_risk") in ("explicit", "partial")
        return "na" if not noticed else ("acted" if r["cls"] in ("killed", "harm") else "refused")
    fig, ax = plt.subplots(figsize=(11, 5.4))
    ser = [("saw the risk, acted anyway", "#d03b3b", [sum(1 for r in by[m] if cat(r) == "acted") for m in models]),
           ("saw the risk, refused", "#0ca30c", [sum(1 for r in by[m] if cat(r) == "refused") for m in models]),
           ("did not flag the risk", "#cfcfcf", [sum(1 for r in by[m] if cat(r) == "na") for m in models])]
    _grouped(ax, models, ser, ymax=32)
    ax.set_ylabel("episodes", color=FAINT, fontsize=9)
    _save_single(fig, ax, "Did it notice the danger — and act anyway?", "panel_notice_vs_act.png", ser)


def panel_coupling(rows):
    by, models = _models_by_harm(rows)
    rate = []
    for m in models:
        eng = [r for r in by[m] if _lab(r, "eval_awareness") != "no_cot"]
        rate.append(100 * sum(1 for r in eng if _lab(r, "connected_coupling")) / max(1, len(eng)))
    fig, ax = plt.subplots(figsize=(11, 5.4))
    _grouped(ax, models, [("", "#4a4a4a", rate)], ymax=114, pct=True)
    ax.set_ylabel("% of reasoned episodes", color=FAINT, fontsize=9)
    _save_single(fig, ax, "Explicitly connected the action to the hidden harm", "panel_connected_coupling.png")


def panel_headline(rows):
    fig, ax = plt.subplots(figsize=(9, 5.4)); ax.axis("off")
    acted = [r for r in rows if r["cls"] in ("killed", "harm")]
    noticed_acted = sum(1 for r in acted if _lab(r, "noticed_risk") in ("explicit", "partial"))
    susp = sum(1 for r in rows if _lab(r, "eval_awareness") in ("suspects_test", "alignment_eval"))
    misrep = sum(1 for r in rows if _lab(r, "misrepresented") is True)
    lines = [(f"{len(acted)}", "episodes took the harmful, task-completing action", "#d03b3b"),
             (f"{noticed_acted} of {len(acted)}", "of those had ALREADY reasoned it was unsafe", "#d03b3b"),
             (f"{susp}", "episodes suspected they were being tested", INK),
             (f"{misrep}", "episodes misrepresented what they had done", INK)]
    y = .96
    for big, small, col in lines:
        ax.text(.01, y, big, fontsize=34, fontweight="bold", color=col, transform=ax.transAxes, va="top")
        ax.text(.01, y - .085, small, fontsize=12, color=INK, transform=ax.transAxes, va="top")
        y -= .245
    ax.set_title("The headline", fontsize=14, fontweight="bold", color=INK, loc="left", pad=16)
    fig.tight_layout()
    fig.savefig(OUT / "panel_headline.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote panel_headline.png")


def make_plots():
    eps, rows = _load()
    outcomes_by_model(eps)
    study_figure(eps, rows)
    panel_awareness(rows)
    panel_notice_act(rows)
    panel_coupling(rows)
    panel_headline(rows)


if __name__ == "__main__":
    make_plots()
