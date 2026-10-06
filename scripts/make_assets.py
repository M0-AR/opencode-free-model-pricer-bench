"""
Generate README/preview.html visual assets directly from the committed
results/*.csv files — no fabricated numbers, just different renderings
of the same data already in this repo.

Produces:
    results/assets/hero_comparison.png   — avg error per model, bar chart
    results/assets/prediction_buildup.gif — scatter plot filling in point by
                                             point for one model, so you can
                                             see predictions land in real time

Usage:
    python3 scripts/make_assets.py
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import PillowWriter

RESULTS_DIR = Path(__file__).parent.parent / "results"
ASSETS_DIR = RESULTS_DIR / "assets"
GIF_MODEL = "fledge-alpha-free"  # best-sampled, well-behaved result in the committed run


def load_summary():
    with open(RESULTS_DIR / "summary.csv") as f:
        return list(csv.DictReader(f))


def load_items(model_id):
    with open(RESULTS_DIR / f"{model_id}.csv") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r["failed"] == ""]


def make_hero_chart(summary):
    summary = sorted(summary, key=lambda r: float(r["avg_error"]))
    models = [r["model"] for r in summary]
    errors = [float(r["avg_error"]) for r in summary]
    ok = [int(r["ok"]) for r in summary]
    colors = ["#2ecc71" if o == 30 else "#f39c12" if o >= 15 else "#e74c3c" for o in ok]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    bars = ax.barh(models, errors, color=colors)
    for bar, o in zip(bars, ok):
        ax.text(bar.get_width() + 2, bar.get_y() + bar.get_height() / 2, f"{o}/30 ok", va="center", fontsize=9)
    ax.set_xlabel("Average absolute error ($) — lower is better")
    ax.set_title("OpenCode free-tier models on the pricer task (n=30)")
    ax.invert_yaxis()
    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, color="#2ecc71", label="30/30 completed"),
        plt.Rectangle((0, 0), 1, 1, color="#f39c12", label="15-29/30 completed"),
        plt.Rectangle((0, 0), 1, 1, color="#e74c3c", label="<15/30 completed"),
    ]
    ax.legend(handles=legend_handles, loc="lower right", fontsize=8, title="Bar color = completion rate")
    fig.tight_layout()
    ASSETS_DIR.mkdir(exist_ok=True)
    fig.savefig(ASSETS_DIR / "hero_comparison.png", dpi=140)
    plt.close(fig)
    print("wrote hero_comparison.png")


def make_buildup_gif(model_id):
    rows = load_items(model_id)
    truths = [float(r["truth"]) for r in rows]
    guesses = [float(r["guess"]) for r in rows]
    errors = [float(r["error"]) for r in rows]
    max_val = max(truths + guesses) * 1.05

    fig, ax = plt.subplots(figsize=(5.5, 5.5))

    def draw(frame):
        ax.clear()
        n = frame + 1
        colors = [
            "#2ecc71" if e < 40 or e / t < 0.2 else "#f39c12" if e < 80 or e / t < 0.4 else "#e74c3c"
            for e, t in zip(errors[:n], truths[:n])
        ]
        ax.scatter(truths[:n], guesses[:n], c=colors, s=40, zorder=3)
        ax.plot([0, max_val], [0, max_val], "--", color="#3498db", linewidth=1.5, zorder=1)
        ax.set_xlim(0, max_val)
        ax.set_ylim(0, max_val)
        ax.set_xlabel("Actual price ($)")
        ax.set_ylabel("Model's guess ($)")
        avg = sum(errors[:n]) / n
        ax.set_title(f"{model_id} — item {n}/{len(truths)} — avg error so far: ${avg:,.2f}")
        fig.tight_layout()

    writer = PillowWriter(fps=2)
    ASSETS_DIR.mkdir(exist_ok=True)
    gif_path = ASSETS_DIR / "prediction_buildup.gif"
    with writer.saving(fig, gif_path, dpi=110):
        for frame in range(len(truths)):
            draw(frame)
            writer.grab_frame()
        for _ in range(4):  # hold the final frame so the GIF doesn't instantly loop
            writer.grab_frame()
    plt.close(fig)
    print(f"wrote prediction_buildup.gif ({len(truths)} items, {model_id})")


if __name__ == "__main__":
    make_hero_chart(load_summary())
    make_buildup_gif(GIF_MODEL)
