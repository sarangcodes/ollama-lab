# Ollama Lab

A proof of concept for running local LLMs with [Ollama](https://ollama.com) on a MacBook (built on an M2 with 24 GB).
It answers one question: **how big a model can this Mac run, and how good and fast is each size?**

- **Playground**: prompt one model or several side by side, with live sampling settings (temperature, top-p/k,
  repeat penalty, context size, max tokens, seed, system prompt, thinking on/off) and per-reply stats.
- **Benchmark**: loads each model on its own and measures the KPIs below, then grades it on 26 auto-checked
  questions.
- **Results**: summary tiles (biggest model that fits on the GPU, sweet spot, fastest, most accurate), a speed chart,
  an accuracy-vs-memory chart and a sortable table. Click any row for per-question answers.
- **Models**: download any tag from ollama.com/library, see installed models, unload them.

Everything is read live from the Ollama API; nothing to configure.

## Run it

```bash
brew install ollama && brew services start ollama   # once
ollama pull qwen3.5:4b                              # or use the Models tab
uv run uvicorn app.server:app --port 8777
open http://127.0.0.1:8777
```

Headless benchmark (same code, results go to the same database):

```bash
uv run python -m app.bench qwen3.5:0.8b qwen3.5:4b     # named models
uv run python -m app.bench --all                       # every installed model, smallest first
uv run python -m app.bench --all --think --ctx 16384   # thinking on, bigger context
```

Results are stored in `data/results.db` (SQLite, not committed).

## What is measured

| KPI | How |
|---|---|
| Memory loaded / % on GPU | `/api/ps` right after loading. Below 100% means part of the model runs on the CPU, which is much slower. |
| Cold load time | Every other model is unloaded first, then the model is loaded from disk. |
| First token (warm) | Wall time from request to first streamed token, model already in memory. |
| Writing speed (tok/s) | Median of N runs of a 256-token answer, thinking off. Ollama's own `eval_duration`. |
| Reading speed (tok/s) | Prompt processing of a ~4,000-token prompt (fresh each run, so no cache hits). |
| Finds a fact in 4k tokens | The long prompt hides a 6-digit code 60% of the way in; the model must return it. |
| Accuracy | 26 questions in 5 groups: math, reasoning, knowledge, reading code, following format. Temperature 0, seed 42. |
| RAM peak / swap growth | Sampled every 0.5 s while the model runs. Swap growth means the Mac ran out of memory. |
| Verdict | Fast ≥ 25 tok/s, Usable ≥ 10, Slow ≥ 4, Spills to CPU when it doesn't fit in GPU memory. |

The test set (`app/evals.py`) is a quick sanity check of how ability changes with size, not a published benchmark.
Add items there; each has a checker function, so no grading by hand.

## Notes for Apple Silicon

- macOS lets the GPU use about two thirds of unified memory by default (≈16 GB on a 24 GB Mac). Models bigger than
  that spill onto the CPU. You can raise the cap until the next reboot with
  `sudo sysctl iogpu.wired_limit_mb=19456` (leave a few GB for macOS).
- Other apps count: close browsers and IDEs before benchmarking large models. The status bar shows RAM and swap.
- Context size costs memory too: the same model uses more at 32k context than at 8k.
- Thinking models (qwen3.5, gemma4, gpt-oss) get more accurate with thinking on, but write many more tokens per
  answer. The benchmark turns it off by default so models are compared like for like.

## Layout

```
app/
  server.py   FastAPI: playground streaming, benchmark jobs, results API, static UI
  bench.py    benchmark runner (also the CLI)
  evals.py    test questions and their checkers
  ollama.py   Ollama API client
  system.py   RAM, swap, memory pressure, GPU memory cap
  store.py    SQLite results store
  static/     the web UI (plain HTML/CSS/JS, no build step)
```
