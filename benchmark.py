"""
Benchmark OpenCode's free-tier models on the pricer task: given a product
description, guess its retail price.

Task and dataset are from ed-donner/pricer (https://github.com/ed-donner/pricer),
which compares frontier LLMs and TypeSafe's Jev against the same Amazon-derived
items. This script swaps those for OpenCode's free-tier models instead, calling
a locally running `opencode serve` instance directly over HTTP.

Why not litellm/OpenRouter like the original notebook? These free models are
only reachable through OpenCode's own agent runtime — stripping the tool
definitions to get a bare/fast completion gets rejected with a 403
FreeTierError ("OpenCode's free tier can only be used from within OpenCode").
A custom system-prompt override is fine; disabling tools is what trips it.
That also means latency here includes real agent-context overhead and isn't
directly comparable to the original notebook's sub-second OpenRouter timings.
Cost is a clean $0 across the board either way (free tier).

Prerequisites:
    - `opencode serve --port 4096` running locally with a provider configured
      (OPENCODE_API_KEY env var, or whatever your opencode auth setup uses)

Usage:
    python3 benchmark.py --size 30 --workers 4
"""

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from sklearn.metrics import mean_squared_error, r2_score

from pricer_bench.items import Item

BASE_URL = "http://127.0.0.1:4096"
PROVIDER_ID = "opencode"

# ling-3.0-flash-fin-free is deliberately excluded: every call to it returns
# "Not Found: Cannot find any route matching [POST]..." — a routing/delisting
# error, not a transient one, confirmed by testing it in isolation multiple times.
FREE_MODELS = [
    "ling-3.1-flash-free",
    "fledge-alpha-free",
    "longcat-2.5-preview-free",
    "space-bunny-free",
    "mimo-v2.6-flash-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "muse-spark-1.3-contributor-free",
]

SYSTEM_PROMPT = (
    "You are a price estimation assistant. Given a product description, "
    "respond with your best estimate of its price in US dollars. "
    "Respond with only the number, no dollar sign, no explanation."
)

RESULTS_DIR = Path(__file__).parent / "results"


def messages_for(item: Item) -> str:
    return f"Estimate the price of this product. Respond with the price, no explanation\n\n{item.summary}"


def post_process(value) -> float:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "")
        match = re.search(r"[-+]?\d*\.\d+|\d+", value)
        return float(match.group()) if match else 0.0
    return float(value) if value is not None else 0.0


def _attempt(model_id: str, item: Item, session: requests.Session, timeout: float):
    sid = session.post(f"{BASE_URL}/session", json={"title": f"bench-{model_id}"}, timeout=timeout).json()["id"]
    started = time.perf_counter()
    try:
        resp = session.post(
            f"{BASE_URL}/session/{sid}/message",
            json={
                "model": {"providerID": PROVIDER_ID, "modelID": model_id},
                "system": SYSTEM_PROMPT,
                "parts": [{"type": "text", "text": messages_for(item)}],
            },
            timeout=timeout,
        ).json()
    except requests.exceptions.RequestException as exc:
        return 0.0, 0.0, time.perf_counter() - started, str(exc)

    latency = time.perf_counter() - started
    info = resp.get("info", {})
    error = info.get("error")
    if error:
        return 0.0, 0.0, latency, error.get("data", {}).get("message", str(error))

    text = next((p["text"] for p in resp.get("parts", []) if p.get("type") == "text"), None)
    cost = info.get("cost", 0.0) or 0.0
    return post_process(text), cost, latency, None


def call_model(model_id: str, item: Item, session: requests.Session, timeout: float = 150.0, retries: int = 1):
    """Retry once on transient errors (timeouts/connection resets); permanent errors like
    'Not Found: Cannot find any route' are not retried since a retry can't fix them."""
    guess, cost, latency, err = _attempt(model_id, item, session, timeout)
    attempt = 0
    while err and attempt < retries and "Not Found" not in err:
        attempt += 1
        guess, cost, latency, err = _attempt(model_id, item, session, timeout)
    return guess, cost, latency, err


def run_model(model_id: str, data: list[Item], workers: int) -> dict:
    session = requests.Session()
    rows = []

    def task(item):
        return call_model(model_id, item, session)

    print(f"\n=== {model_id} ({len(data)} items, {workers} workers) ===")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for item, (guess, cost, latency, err) in zip(data, ex.map(task, data)):
            if err:
                print(f"[FAIL ${item.price:.0f} truth, err={err[:60]}] ", end="", flush=True)
                rows.append(
                    {
                        "title": item.title,
                        "truth": item.price,
                        "guess": None,
                        "error": None,
                        "cost": None,
                        "latency": latency,
                        "failed": err,
                    }
                )
                continue
            error = abs(guess - item.price)
            print(f"${error:,.0f} ", end="", flush=True)
            rows.append(
                {
                    "title": item.title,
                    "truth": item.price,
                    "guess": guess,
                    "error": error,
                    "cost": cost,
                    "latency": latency,
                    "failed": None,
                }
            )
    elapsed = time.perf_counter() - started

    ok_rows = [r for r in rows if r["failed"] is None]
    failures = len(rows) - len(ok_rows)
    print(f"\n{model_id}: {len(ok_rows)} ok, {failures} failed, {elapsed:,.1f}s wall clock")

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / f"{model_id}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    if not ok_rows:
        return {"model": model_id, "ok": 0, "failed": failures, "avg_error": None}

    truths = [r["truth"] for r in ok_rows]
    guesses = [r["guess"] for r in ok_rows]
    errors = [r["error"] for r in ok_rows]
    avg_error = sum(errors) / len(errors)
    mse = mean_squared_error(truths, guesses)
    r2 = r2_score(truths, guesses) * 100 if len(truths) > 1 else float("nan")
    avg_cost = sum(r["cost"] for r in ok_rows) / len(ok_rows)
    avg_latency = sum(r["latency"] for r in ok_rows) / len(ok_rows)

    fig, ax = plt.subplots(figsize=(6, 6))
    max_val = max(max(truths), max(guesses)) * 1.05
    colors = [
        "green" if e < 40 or e / t < 0.2 else "orange" if e < 80 or e / t < 0.4 else "red"
        for e, t in zip(errors, truths)
    ]
    ax.scatter(truths, guesses, c=colors, s=20)
    ax.plot([0, max_val], [0, max_val], "--", color="deepskyblue")
    ax.set_xlim(0, max_val)
    ax.set_ylim(0, max_val)
    ax.set_xlabel("Actual Price ($)")
    ax.set_ylabel("Predicted Price ($)")
    ax.set_title(f"{model_id}\nError: ${avg_error:,.2f}  MSE: {mse:,.0f}  r2: {r2:.1f}%")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / f"{model_id}.png", dpi=120)
    plt.close(fig)

    return {
        "model": model_id,
        "ok": len(ok_rows),
        "failed": failures,
        "avg_error": avg_error,
        "mse": mse,
        "r2": r2,
        "avg_cost": avg_cost,
        "avg_latency": avg_latency,
        "wall_clock": elapsed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=30, help="number of test items per model")
    parser.add_argument("--workers", type=int, default=4, help="concurrent requests per model")
    parser.add_argument("--models", nargs="*", default=FREE_MODELS, help="model IDs to benchmark")
    args = parser.parse_args()

    print("Loading test split from ed-donner/items_full ...")
    _, _, test = Item.from_hub("ed-donner/items_full")
    data = test[: args.size]
    print(f"Using first {len(data)} test items.")

    results = [run_model(model_id, data, args.workers) for model_id in args.models]

    summary_path = RESULTS_DIR / "summary.csv"
    with open(summary_path, "w", newline="") as f:
        fieldnames = ["model", "ok", "failed", "avg_error", "mse", "r2", "avg_cost", "avg_latency", "wall_clock"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print("\n" + "=" * 90)
    print(f"{'Model':<36} {'OK':>4} {'Fail':>5} {'AvgErr':>9} {'MSE':>10} {'r2%':>7} {'AvgLatency':>11}")
    print("-" * 90)
    for r in sorted(results, key=lambda r: (r["avg_error"] is None, r["avg_error"])):
        if r["avg_error"] is None:
            print(f"{r['model']:<36} {r['ok']:>4} {r['failed']:>5}  all failed")
            continue
        print(
            f"{r['model']:<36} {r['ok']:>4} {r['failed']:>5} "
            f"${r['avg_error']:>7,.2f} {r['mse']:>10,.0f} {r['r2']:>6.1f}% "
            f"{r['avg_latency']:>10,.1f}s"
        )
    print("=" * 90)
    print(f"\nSummary written to {summary_path}")


if __name__ == "__main__":
    sys.exit(main())
