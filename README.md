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

## Set it up on your Mac

Needs an Apple Silicon Mac (M1 or later) and [Homebrew](https://brew.sh). About 15 minutes plus model downloads.

**1. Install Ollama and uv** (uv runs the Python app and installs its packages for you):

```bash
brew install ollama uv git
brew services start ollama        # runs Ollama in the background, also after a restart
```

**2. Get the code and start the app:**

```bash
git clone https://github.com/sarangcodes/ollama-lab.git
cd ollama-lab
uv run uvicorn app.server:app --port 8777
```

Leave that Terminal window open, then go to **http://127.0.0.1:8777**. The bar at the top should show your chip, RAM
and "GPU cap". If it says Ollama isn't running, run `brew services restart ollama`.

**3. Download models** in the **Models** tab (or `ollama pull <name>` in another Terminal window).
Pick by how much memory your Mac has:

| Mac memory | Start with | Largest worth trying | Total download |
|---|---|---|---|
| 8 GB | `qwen3.5:0.8b`, `qwen3.5:2b` | `qwen3.5:4b` | ~8 GB |
| **16 GB** | `qwen3.5:0.8b`, `qwen3.5:2b`, `qwen3.5:4b`, `gemma4:e2b` | `qwen3.5:9b`, `deepseek-r1:8b` | ~30 GB |
| 24 GB | the 16 GB list + `gemma4:e4b`, `gemma4:12b` | `deepseek-r1:14b`, `gpt-oss:20b` | ~75 GB |
| 32 GB+ | the 24 GB list | `qwen3.5:27b`, `gemma4:26b` | ~110 GB |

On 16 GB, `gemma4:12b` is a good way to see what happens when a model is too big: it partly spills onto the CPU and
slows down a lot. Remove a model with `ollama rm <name>`.

**4. Try it.** In **Playground**, tick one or more models, click a preset question (Sisters, Jugs, Fake paper…) and
Send. Tick several sizes to compare them side by side. Speed and memory stats appear under each reply.

**5. Benchmark.** In **Benchmark**, tick the models and press Start. Each model takes 1–15 minutes depending on size.
Then open **Results**. For fair numbers:

- Close the browser tabs, IDEs and other apps you don't need (they share the same memory).
- Plug in the charger. A MacBook Air has no fan and slows down during long runs, so expect the last models to be a
  little slower than if run alone.
- Don't chat in the Playground while a benchmark runs: it competes for the same chip and the benchmark unloads
  other models.

**6. Share your results.** Stop the app (Ctrl+C) and send the file `data/results.db`. It holds only benchmark numbers
and the models' answers to the test questions.

**Update later:** `git pull` in the `ollama-lab` folder, then start the app again.

### Running it day to day

```bash
cd ollama-lab && uv run uvicorn app.server:app --port 8777
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

- macOS lets the GPU use about two thirds of unified memory by default (≈10.7 GB on a 16 GB Mac, ≈16 GB on 24 GB). Models bigger than
  that spill onto the CPU. You can raise the cap until the next reboot, leaving a few GB for macOS, e.g.
  `sudo sysctl iogpu.wired_limit_mb=12288` on 16 GB or `=19456` on 24 GB.
- Writing speed is limited by memory speed: about 100 GB/s on base M1–M3 chips, so a 4 GB model writes at most
  ~25 tokens/s. Pro and Max chips are 2–5× faster with the same model.
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
