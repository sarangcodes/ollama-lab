"""Thin async client for the local Ollama HTTP API. Everything is fetched live on each call."""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = f"http://{OLLAMA}"

GB = 1024**3
_TIMEOUT = httpx.Timeout(connect=5, read=900, write=30, pool=5)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=OLLAMA, timeout=_TIMEOUT)


async def version() -> str | None:
    try:
        async with client() as c:
            return (await c.get("/api/version")).json().get("version")
    except httpx.HTTPError:
        return None


async def show(c: httpx.AsyncClient, name: str) -> dict[str, Any]:
    r = await c.post("/api/show", json={"model": name})
    r.raise_for_status()
    return r.json()


def _context_length(info: dict[str, Any]) -> int | None:
    for k, v in (info or {}).items():
        if k.endswith(".context_length"):
            return v
    return None


async def running() -> list[dict[str, Any]]:
    """Loaded models with how much of each sits in GPU memory."""
    async with client() as c:
        models = (await c.get("/api/ps")).json().get("models", [])
    out = []
    for m in models:
        size, vram = m.get("size", 0), m.get("size_vram", 0)
        out.append({
            "name": m["name"],
            "loaded_gb": round(size / GB, 2),
            "gpu_gb": round(vram / GB, 2),
            "gpu_pct": round(100 * vram / size, 1) if size else None,
            "context_length": m.get("context_length"),
            "expires_at": m.get("expires_at"),
        })
    return out


async def models() -> list[dict[str, Any]]:
    """Installed models with size, parameter count, quantisation, capabilities and load state."""
    async with client() as c:
        tags = (await c.get("/api/tags")).json().get("models", [])
        out = []
        for t in tags:
            d = t.get("details", {})
            try:
                s = await show(c, t["name"])
            except httpx.HTTPError:
                s = {}
            info = s.get("model_info", {})
            params = info.get("general.parameter_count")
            params_b = round(params / 1e9, 2) if params else _parse_params(d.get("parameter_size"))
            out.append({
                "name": t["name"],
                # MLX/safetensors models leave family and size blank, so fall back to the name and param count.
                "family": d.get("family") or t["name"].split(":")[0].split("/")[-1],
                "parameter_size": d.get("parameter_size") or (f"{params_b:g}B" if params_b else None),
                "params_b": params_b,
                "quant": d.get("quantization_level"),
                "format": d.get("format"),
                "disk_gb": round(t.get("size", 0) / GB, 2),
                "capabilities": s.get("capabilities", []),
                "max_context": _context_length(info),
                "modified_at": t.get("modified_at"),
            })
    loaded = {m["name"]: m for m in await running()}
    for m in out:
        m["loaded"] = loaded.get(m["name"])
    out.sort(key=lambda m: (m["params_b"] or 0, m["name"]))
    return out


def _parse_params(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().upper()
    try:
        if s.endswith("B"):
            return float(s[:-1])
        if s.endswith("M"):
            return float(s[:-1]) / 1000
    except ValueError:
        pass
    return None


def think_value(name: str, capabilities: list[str], think: bool) -> bool | str | None:
    """Ollama's `think` field: omitted for models without thinking; gpt-oss takes a level, not on/off."""
    if "thinking" not in capabilities:
        return None
    if name.startswith("gpt-oss"):
        return "medium" if think else "low"
    return think


async def unload(name: str) -> None:
    async with client() as c:
        await c.post("/api/generate", json={"model": name, "keep_alive": 0})


async def unload_all() -> None:
    for m in await running():
        await unload(m["name"])


async def chat_stream(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Stream /api/chat, yielding Ollama's chunks plus wall-clock timings on the final one."""
    payload = {**payload, "stream": True}
    t0 = time.perf_counter()
    first = None
    async with client() as c:
        async with c.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")
                raise RuntimeError(f"Ollama {r.status_code}: {body[:300]}")
            async for line in r.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise RuntimeError(chunk["error"])
                msg = chunk.get("message", {})
                if first is None and (msg.get("content") or msg.get("thinking")):
                    first = time.perf_counter()
                if chunk.get("done"):
                    chunk["_wall_s"] = time.perf_counter() - t0
                    chunk["_ttft_s"] = (first - t0) if first else None
                yield chunk


def stats_from_final(final: dict[str, Any]) -> dict[str, Any]:
    """Turn Ollama's nanosecond counters into the KPIs we show."""
    ns = 1e9
    pe_n, pe_d = final.get("prompt_eval_count") or 0, final.get("prompt_eval_duration") or 0
    ev_n, ev_d = final.get("eval_count") or 0, final.get("eval_duration") or 0
    return {
        "load_s": round((final.get("load_duration") or 0) / ns, 3),
        "ttft_s": round(final["_ttft_s"], 3) if final.get("_ttft_s") else None,
        "wall_s": round(final.get("_wall_s", 0), 3),
        "prompt_tokens": pe_n,
        "prompt_tok_s": round(pe_n / (pe_d / ns), 1) if pe_d else None,
        "gen_tokens": ev_n,
        "gen_tok_s": round(ev_n / (ev_d / ns), 1) if ev_d else None,
        "done_reason": final.get("done_reason"),
    }


async def chat(name: str, messages: list[dict], options: dict, think: bool | str | None,
               keep_alive: str = "10m") -> dict[str, Any]:
    """Non-interactive chat used by the benchmark. Returns text, thinking and stats."""
    payload: dict[str, Any] = {"model": name, "messages": messages, "options": options, "keep_alive": keep_alive}
    if think is not None:
        payload["think"] = think
    content, thinking, final = [], [], {}
    async for chunk in chat_stream(payload):
        msg = chunk.get("message", {})
        content.append(msg.get("content", ""))
        thinking.append(msg.get("thinking", ""))
        if chunk.get("done"):
            final = chunk
    return {"content": "".join(content), "thinking": "".join(thinking), **stats_from_final(final)}
