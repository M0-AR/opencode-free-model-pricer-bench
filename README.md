# OpenCode Free-Tier Model Pricer Bench

Can OpenCode's free-tier models guess the price of a product from its description?
This repeats the evaluation methodology from [ed-donner/pricer](https://github.com/ed-donner/pricer)
— which compares frontier LLMs and TypeSafe's Jev on the same task — but swaps
the model pool for the nine models currently listed as free under the `opencode`
provider.

## The task

Given a product description (title, category, brand, a one-sentence summary),
ask a model: *"Estimate the price of this product. Respond with the price, no
explanation."* Score the guess against the real price from a held-out test set
of ~10,000 Amazon listings.

Dataset, prompt, and metrics (average absolute error, MSE, r²) are taken as-is
from `ed-donner/pricer`'s `lab.ipynb` / `pricer/evaluator.py`, so results here
are directly comparable to that notebook's reported numbers for GPT-4.1-Nano,
GPT-5.6 Luna, and Jev.

## Why a separate script instead of the original notebook

The original notebook calls models through litellm/OpenRouter — a bare chat
completion. OpenCode's free-tier models reject that: a request with tool
definitions stripped out gets a `403 FreeTierError`:

> "OpenCode's free tier can only be used from within OpenCode"

A custom `system` prompt override is fine; it's specifically disabling `tools`
that trips the gate (confirmed by isolating the two in separate test calls).
So `benchmark.py` talks to a locally running `opencode serve` over its HTTP API
with the normal agent tool-calling context intact, just with a system prompt
override to keep responses terse.

**Practical consequence:** every call carries real agent-context overhead
(tens of seconds per call, even for a one-line answer), so latency numbers
here are *not* comparable to the original notebook's sub-second OpenRouter
timings. Cost is a clean $0 across the board either way (free tier), so cost
comparisons aren't meaningful here.

## Results (n=30 per model)

First 30 items of the `ed-donner/items_full` test split, same selection the
original notebook uses by default (`evaluate(predictor, test)` scores the
first `size` items). Raw data backing this table is in `results/summary.csv`
and `results/<model>.csv` (per-item title/truth/guess/error/latency), with
`results/<model>.png` scatter plots.

**This table is from a single run, and that run had significant request
attrition — read the "Run-to-run reliability" section below before trusting
any row with a low OK count.**

| Model | OK/30 | Avg Error | MSE | r² | Avg Latency |
|---|---|---|---|---|---|
| `fledge-alpha-free` | 25 | $47.31 | 5,805 | 78.9% | 56.6s |
| `longcat-2.5-preview-free` | 30 | $64.39 | 9,209 | 64.8% | 58.8s |
| `mimo-v2.6-flash-free` | 30 | $67.93 | 11,325 | 56.7% | 37.3s |
| `muse-spark-1.3-contributor-free` | 12 | $73.39 | 12,004 | 58.2% | 58.5s |
| `ling-3.1-flash-free` | 30 | $75.25 | 11,022 | 57.8% | 72.1s |
| `nemotron-3.5-lightning-free` | 15 | $98.92 | 28,885 | -25.1% | 95.9s |
| `space-bunny-free` | 30 | $113.16 | 34,995 | -33.9% | 38.4s |
| `nemotron-3-ultra-free` | 2 | $24.48 | 829 | -428.9% | 89.7s |

`nemotron-3-ultra-free`'s $24.48 looks great until you notice it's from 2
successful calls out of 30 — not a usable signal, just luck. Don't rank by
this table alone; cross-check the OK column and, ideally, re-run yourself.

## Run-to-run reliability

Three independent sweeps of (mostly) the same 8-9 models, same 30 items,
run hours apart:

| Run | Conditions | Clean (30/30) models | Notes |
|---|---|---|---|
| 1 | Initial network path, 5 workers, 90s timeout, no retry | 2 of 9 | `ling-3.0-flash-fin-free` permanently dead (routing error); several others 20-100% timeout |
| 2 | After a VPN change, 4 workers, 150s timeout, 1 retry | **8 of 8** | Every model 30/30, zero failures — the numbers quoted earlier in this project's development |
| 3 (committed here) | Same as run 2, different time of day | 3 of 8 | Backend-side degradation unrelated to network path — `nemotron-3-ultra-free` alone took 38 minutes for 30 items and got 2 through |

Run 2's clean numbers, for reference (not in `results/`, but worth knowing):
`muse-spark-1.3-contributor-free` $34.84 (r²=87.6%), `nemotron-3-ultra-free`
$50.04 (r²=71.6%), `fledge-alpha-free` $55.27 (r²=73.4%), `mimo-v2.6-flash-free`
$63.56 (r²=59.7%), `longcat-2.5-preview-free` $64.01 (r²=67.2%),
`ling-3.1-flash-free` $70.85 (r²=59.1%), `nemotron-3.5-lightning-free` $93.65
(r²=-58.1%), `space-bunny-free` $126.27 (r²=-24.3%).

**Takeaway: these models' availability and latency fluctuate significantly
run to run, independent of which network path you're on.** Treat any single
run's numbers as a snapshot of that moment, not a stable ranking. If you're
evaluating these models for real use, run this more than once before drawing
conclusions, and watch the OK/30 column as closely as the error column.

For reference, the original notebook reports (n=200, via OpenRouter):

| Model | Avg Error | Cost per 1k | Latency |
|---|---|---|---|
| GPT-4.1-Nano | $68.00 | $0.012 | ~879ms |
| GPT-5.6 Luna (no reasoning) | $54.91 | $0.030 | ~950ms |
| Jev (single pass) | $58.78 | $0.080 | 264ms |
| Jev (two-pass median) | ~$54 | $0.100 | 238ms |

## One rate-limit lesson worth keeping

The first full sweep (also n=30, 5 workers) had heavy attrition — several
models timed out on 20-80% of calls. Switching network paths (a VPN change)
and re-running with a longer per-call timeout, one retry on transient errors,
and slightly lower concurrency (4 workers) brought every model to a clean
30/30. One model, `ling-3.0-flash-fin-free`, is excluded entirely: its
failures were consistently `Not Found: Cannot find any route matching
[POST]...` — a routing/delisting error, not a timeout, confirmed in isolated
retests. It's simply not reachable right now, regardless of network path.

## Reproducing

```bash
pip install -r requirements.txt

# in another terminal, or however you run it:
opencode serve --port 4096

python3 benchmark.py --size 30 --workers 4
```

`--models` takes any subset of the model IDs in `benchmark.py`'s `FREE_MODELS`
list; `--size` controls how many test items to score (the original notebook's
default is 200, which takes considerably longer at these per-call latencies).

## Caveats

- n=30 is enough to rank models, not to pin down exact dollar figures to the
  cent — scale up `--size` for tighter confidence intervals.
- These are free-tier models; availability, routing, and even which models are
  listed as free can change without notice.
- The agent-context tax on latency (see above) means this setup is good for
  an accuracy comparison, not a speed comparison.

## Credits

- Task, dataset, and evaluation methodology: [ed-donner/pricer](https://github.com/ed-donner/pricer)
- Models: whatever's currently listed under the `opencode` provider's free tier
