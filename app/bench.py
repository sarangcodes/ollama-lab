"""Benchmark runner: memory fit, load time, speed and accuracy for each model.

Run from the web UI, or headless:
    uv run python -m app.bench qwen3.5:0.8b qwen3.5:4b
    uv run python -m app.bench --all --think
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import evals, ollama, store, system

SPEED_PROMPT = "Explain in detail how a household refrigerator keeps food cold. Cover the refrigerant cycle step by step."


@dataclass
class Settings:
    num_ctx: int = 8192
    think: bool = False
    speed_runs: int = 3
    gen_tokens: int = 256
    long_prompt_tokens: int = 4000
    run_evals: bool = True
    min_tok_s_for_evals: float = 3.0  # below this, evals would take too long to be worth it
    unload_after: bool = True


@dataclass
class Job:
    id: str
    models: list[str]
    settings: Settings
    status: str = "queued"  # queued | running | done | failed | cancelled
    current: str | None = None
    step: str = ""
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    result_ids: list[int] = field(default_factory=list)
    cancelled: bool = False

    def say(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        if len(self.log) > 500:
            self.log = self.log[-500:]

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "models": self.models, "settings": self.settings.__dict__, "status": self.status,
                "current": self.current, "step": self.step, "progress": round(self.progress, 3),
                "log": self.log[-200:], "result_ids": self.result_ids}


def _haystack(target_tokens: int, nonce: str) -> tuple[str, str]:
    """Filler text of roughly target_tokens tokens with one fact hidden 60% of the way in."""
    rnd = random.Random(nonce)
    nouns = ["turbine", "warehouse", "orchard", "satellite", "bakery", "harbour", "library", "reactor", "vineyard"]
    adjs = ["northern", "quiet", "automated", "older", "coastal", "renovated", "busy", "remote"]
    code = str(rnd.randint(100000, 999999))
    sentences, n = [], max(10, target_tokens // 36)  # about 36 tokens per sentence
    for i in range(n):
        sentences.append(f"Log entry {i + 1}: the {rnd.choice(adjs)} {rnd.choice(nouns)} in sector "
                         f"{rnd.randint(1, 99)} reported {rnd.randint(10, 9999)} units on day {rnd.randint(1, 365)}, "
                         f"which the team marked as {rnd.choice(['normal', 'unusual', 'pending review', 'resolved'])}.")
    sentences.insert(int(n * 0.6), f"Important: the secret access code for the vault is {code}.")
    return f"[session {nonce}]\n" + " ".join(sentences), code


async def bench_model(name: str, meta: dict, s: Settings, job: Job, base: float, span: float) -> tuple[dict, dict]:
    caps = meta.get("capabilities", [])
    think = ollama.think_value(name, caps, s.think)
    eval_think = think
    opts = {"num_ctx": s.num_ctx, "temperature": 0, "seed": 42}
    m: dict[str, Any] = {"model": name, "family": meta.get("family"), "params_b": meta.get("params_b"),
                         "parameter_size": meta.get("parameter_size"), "quant": meta.get("quant"),
                         "disk_gb": meta.get("disk_gb"), "think": think}
    details: dict[str, Any] = {}

    def step(label: str, frac: float):
        job.step = label
        job.progress = base + span * frac
        job.say(f"{name}: {label}")

    # 1. Cold load: unload everything so the load time and memory placement are clean.
    step("unloading other models", 0.0)
    await ollama.unload_all()
    await asyncio.sleep(1)
    with system.Sampler() as sampler:
        step("cold load", 0.02)
        t0 = time.perf_counter()
        warm = await ollama.chat(name, [{"role": "user", "content": "Say OK."}], {**opts, "num_predict": 1}, None)
        m["cold_load_s"] = round(warm["load_s"] or (time.perf_counter() - t0), 2)
        ps = next((p for p in await ollama.running() if p["name"] == name), None)
        if ps:
            m.update(loaded_gb=ps["loaded_gb"], gpu_gb=ps["gpu_gb"], gpu_pct=ps["gpu_pct"])
        job.say(f"{name}: loaded {m.get('loaded_gb')} GB, {m.get('gpu_pct')}% on GPU, {m['cold_load_s']} s")

        # 2. Generation speed (thinking off so every model writes the same kind of text).
        speed_think = ollama.think_value(name, caps, False)
        runs = []
        for i in range(s.speed_runs):
            if job.cancelled:
                raise asyncio.CancelledError
            step(f"speed run {i + 1}/{s.speed_runs}", 0.05 + 0.15 * i / s.speed_runs)
            r = await ollama.chat(name, [{"role": "user", "content": f"({uuid.uuid4().hex[:6]}) {SPEED_PROMPT}"}],
                                  {**opts, "num_predict": s.gen_tokens, "temperature": 0.7}, speed_think)
            runs.append(r)
        m["gen_tok_s"] = statistics.median(r["gen_tok_s"] or 0 for r in runs)
        m["ttft_s"] = statistics.median(r["ttft_s"] or 0 for r in runs)
        details["speed_runs"] = [{k: r[k] for k in ("ttft_s", "gen_tok_s", "gen_tokens", "prompt_tok_s", "wall_s")}
                                 for r in runs]
        details["speed_sample"] = runs[-1]["content"][:1500]
        job.say(f"{name}: {m['gen_tok_s']} tok/s, first token {m['ttft_s']} s")

        # 3. Long prompt: prompt-processing speed + can it find one fact in a few thousand tokens.
        step(f"long prompt (~{s.long_prompt_tokens} tokens)", 0.22)
        text, code = _haystack(min(s.long_prompt_tokens, int(s.num_ctx * 0.85)), uuid.uuid4().hex[:8])
        q = f"{text}\n\nWhat is the secret access code for the vault? Reply with only the code."
        lr = await ollama.chat(name, [{"role": "user", "content": q}], {**opts, "num_predict": 64}, speed_think)
        m["prompt_tok_s"] = lr["prompt_tok_s"]
        m["long_prompt_tokens"] = lr["prompt_tokens"]
        m["long_ttft_s"] = lr["ttft_s"]
        m["needle_found"] = code in lr["content"]
        details["needle"] = {"code": code, "reply": lr["content"][:300], "prompt_tokens": lr["prompt_tokens"]}
        job.say(f"{name}: read {lr['prompt_tokens']} prompt tokens at {lr['prompt_tok_s']} tok/s, "
                f"needle {'found' if m['needle_found'] else 'missed'}")

        # 4. Accuracy on the built-in test set.
        if s.run_evals and (m["gen_tok_s"] or 0) >= s.min_tok_s_for_evals:
            items, out = evals.ITEMS, []
            t_eval = time.perf_counter()
            for i, item in enumerate(items):
                if job.cancelled:
                    raise asyncio.CancelledError
                step(f"test {i + 1}/{len(items)} ({item.category})", 0.3 + 0.68 * i / len(items))
                r = await ollama.chat(name, [{"role": "user", "content": evals.build_prompt(item)}],
                                      {**opts, "num_predict": 4096 if s.think else 1024}, eval_think)
                ok, ans = evals.grade(item, r["content"])
                out.append({"id": item.id, "category": item.category, "ok": ok, "answer": ans[:200],
                            "expected": item.expected, "wall_s": r["wall_s"], "gen_tokens": r["gen_tokens"],
                            "thinking_chars": len(r["thinking"]), "done_reason": r["done_reason"]})
            m["eval_s"] = round(time.perf_counter() - t_eval, 1)
            m["accuracy"] = round(100 * sum(o["ok"] for o in out) / len(out), 1)
            m["by_category"] = {c: round(100 * sum(o["ok"] for o in out if o["category"] == c)
                                         / sum(1 for o in out if o["category"] == c), 1) for c in evals.CATEGORIES}
            m["avg_answer_s"] = round(statistics.mean(o["wall_s"] for o in out), 2)
            details["evals"] = out
            job.say(f"{name}: accuracy {m['accuracy']}% ({sum(o['ok'] for o in out)}/{len(out)})")
        else:
            m["accuracy"] = None
            m["eval_skipped"] = "too slow" if s.run_evals else "off"
            job.say(f"{name}: tests skipped ({m['eval_skipped']})")

    m.update(sampler.result())
    m["verdict"] = verdict(m)
    if s.unload_after:
        await ollama.unload(name)
    return m, details


def verdict(m: dict) -> str:
    tok = m.get("gen_tok_s") or 0
    if m.get("gpu_pct") is not None and m["gpu_pct"] < 99.5:
        return "Too slow (spills to CPU)" if tok < 5 else "Spills to CPU"
    if (m.get("swap_growth_gb") or 0) > 1.0:
        return "Fits, but swaps"
    if tok >= 25:
        return "Fast"
    if tok >= 10:
        return "Usable"
    if tok >= 4:
        return "Slow"
    return "Too slow"


async def run_job(job: Job) -> None:
    job.status = "running"
    run_id = job.id
    try:
        installed = {m["name"]: m for m in await ollama.models()}
        job.say(f"Benchmark {run_id}: {len(job.models)} model(s), context {job.settings.num_ctx}, "
                f"thinking {'on' if job.settings.think else 'off'}")
        n = len(job.models)
        for i, name in enumerate(job.models):
            if job.cancelled:
                break
            job.current = name
            if name not in installed:
                job.say(f"{name}: not installed, skipped")
                continue
            try:
                metrics, details = await bench_model(name, installed[name], job.settings, job, i / n, 1 / n)
            except asyncio.CancelledError:
                break
            except Exception as e:  # keep going with the next model
                job.say(f"{name}: FAILED: {e}")
                metrics, details = {"model": name, "error": str(e), "verdict": "Failed",
                                    "params_b": installed[name].get("params_b"),
                                    "disk_gb": installed[name].get("disk_gb")}, {}
                try:
                    await ollama.unload(name)
                except Exception:
                    pass
            job.result_ids.append(store.save(run_id, name, job.settings.__dict__, metrics, details))
        job.status = "cancelled" if job.cancelled else "done"
        job.progress = 1.0 if not job.cancelled else job.progress
        job.say(f"Benchmark {job.status}")
    except Exception as e:
        job.status = "failed"
        job.say(f"Benchmark failed: {e}")
    finally:
        job.current, job.step = None, ""


def _print_table(rows: list[dict]) -> None:
    cols = [("model", 22), ("params_b", 7), ("loaded_gb", 9), ("gpu_pct", 7), ("cold_load_s", 9),
            ("ttft_s", 7), ("prompt_tok_s", 10), ("gen_tok_s", 9), ("accuracy", 8), ("verdict", 24)]
    cols = [(c, max(w, len(c))) for c, w in cols]
    print("".join(c.ljust(w + 2) for c, w in cols))
    for r in rows:
        print("".join(str(r.get(c) if r.get(c) is not None else "-")[:w].ljust(w + 2) for c, w in cols))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("models", nargs="*")
    p.add_argument("--all", action="store_true", help="every installed model, smallest first")
    p.add_argument("--think", action="store_true", help="let thinking models think during the tests")
    p.add_argument("--ctx", type=int, default=8192)
    p.add_argument("--no-evals", action="store_true")
    args = p.parse_args()

    async def go():
        names = [m["name"] for m in await ollama.models()] if args.all else args.models
        if not names:
            p.error("name some models or pass --all")
        job = Job(uuid.uuid4().hex[:8], names, Settings(num_ctx=args.ctx, think=args.think, run_evals=not args.no_evals))
        printed = 0

        async def tail():
            nonlocal printed
            while True:
                for line in job.log[printed:]:
                    print(line, flush=True)
                printed = len(job.log)
                await asyncio.sleep(0.3)

        t = asyncio.create_task(tail())
        await run_job(job)
        await asyncio.sleep(0.4)
        t.cancel()
        print()
        _print_table([store.get(i)["metrics"] for i in job.result_ids])

    asyncio.run(go())


if __name__ == "__main__":
    main()
