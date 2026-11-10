"""Web server: prompt playground, benchmark runner and results, all on http://127.0.0.1:8777."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bench, evals, ollama, store, system

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Ollama Lab")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

JOBS: dict[str, bench.Job] = {}
_TASKS: set[asyncio.Task] = set()


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    return {"ollama": await ollama.version(), "host": ollama.OLLAMA, "system": system.snapshot(),
            "machine": store.machine()["label"],
            "running": await ollama.running() if await ollama.version() else []}


@app.get("/api/models")
async def models():
    try:
        return await ollama.models()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ollama not reachable at {ollama.OLLAMA}: {e}")


class ChatIn(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    options: dict[str, Any] = Field(default_factory=dict)
    think: bool = False
    keep_alive: str = "10m"


@app.post("/api/chat")
async def chat(body: ChatIn):
    """Streams NDJSON events: {type: thinking|content|stats|error}."""
    async with ollama.client() as c:
        try:
            caps = (await ollama.show(c, body.model)).get("capabilities", [])
        except httpx.HTTPError as e:
            raise HTTPException(404, f"Unknown model {body.model}: {e}")
    payload: dict[str, Any] = {"model": body.model, "messages": body.messages,
                               "options": {k: v for k, v in body.options.items() if v not in (None, "")},
                               "keep_alive": body.keep_alive}
    think = ollama.think_value(body.model, caps, body.think)
    if think is not None:
        payload["think"] = think

    async def events():
        try:
            async for chunk in ollama.chat_stream(payload):
                msg = chunk.get("message", {})
                if msg.get("thinking"):
                    yield json.dumps({"type": "thinking", "text": msg["thinking"]}) + "\n"
                if msg.get("content"):
                    yield json.dumps({"type": "content", "text": msg["content"]}) + "\n"
                if chunk.get("done"):
                    stats = ollama.stats_from_final(chunk)
                    ps = next((p for p in await ollama.running() if p["name"] == body.model), None)
                    if ps:
                        stats.update(loaded_gb=ps["loaded_gb"], gpu_pct=ps["gpu_pct"])
                    stats["think"] = think
                    yield json.dumps({"type": "stats", **stats}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


class PullIn(BaseModel):
    model: str


@app.post("/api/pull")
async def pull(body: PullIn):
    """Download a model, streaming Ollama's progress lines."""
    async def events():
        try:
            async with ollama.client() as c:
                async with c.stream("POST", "/api/pull", json={"model": body.model, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        if line:
                            yield line + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/unload")
async def unload(model: str | None = None):
    await (ollama.unload(model) if model else ollama.unload_all())
    return {"running": await ollama.running()}


class BenchIn(BaseModel):
    models: list[str]
    num_ctx: int = 8192
    think: bool = False
    speed_runs: int = 3
    run_evals: bool = True


@app.post("/api/bench")
async def start_bench(body: BenchIn):
    if any(j.status in ("queued", "running") for j in JOBS.values()):
        raise HTTPException(409, "A benchmark is already running")
    if not body.models:
        raise HTTPException(400, "Pick at least one model")
    s = bench.Settings(num_ctx=body.num_ctx, think=body.think, speed_runs=max(1, min(body.speed_runs, 10)),
                       run_evals=body.run_evals)
    job = bench.Job(uuid.uuid4().hex[:8], body.models, s)
    JOBS[job.id] = job
    task = asyncio.create_task(bench.run_job(job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job.view()


@app.get("/api/bench")
async def list_jobs():
    return [j.view() for j in JOBS.values()]


@app.get("/api/bench/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "No such job")
    return JOBS[job_id].view()


@app.post("/api/bench/{job_id}/cancel")
async def cancel_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "No such job")
    JOBS[job_id].cancelled = True
    JOBS[job_id].say("Cancelling after the current step…")
    return JOBS[job_id].view()


@app.get("/api/results")
async def results(history: bool = False):
    return store.history() if history else store.latest_per_model()


@app.get("/api/results/export")
async def export_results():
    data = store.export_all()
    slug = "".join(ch if ch.isalnum() else "-" for ch in data["exported_by"]["label"].lower()).strip("-")
    slug = "-".join(p for p in slug.split("-") if p)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="ollama-lab-{slug}.json"'})


@app.post("/api/results/import")
async def import_results(data: dict[str, Any]):
    try:
        return store.import_all(data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/results/{result_id}")
async def result(result_id: int):
    r = store.get(result_id)
    if not r:
        raise HTTPException(404, "No such result")
    return r


@app.delete("/api/results/{result_id}")
async def delete_result(result_id: int):
    store.delete(result_id)
    return {"ok": True}


@app.get("/api/evals")
async def eval_items():
    return [{"id": i.id, "category": i.category, "prompt": i.prompt, "expected": i.expected} for i in evals.ITEMS]
