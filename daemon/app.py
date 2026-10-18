"""Core daemon. Run from /daemon:  uvicorn app:app --host 127.0.0.1 --port 7777"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import load_config
from routing import Hub, Pipeline, as_dict, load_backends

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
log = logging.getLogger("tutor.app")

cfg = load_config()
hub = Hub()
backends = load_backends(cfg)
pipeline = Pipeline(cfg, hub, backends)
voice = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global voice
    loop = asyncio.get_running_loop()
    await pipeline.start()
    if cfg.voice.enabled:
        from voice import VoiceService

        def on_state(state: str) -> None:
            asyncio.run_coroutine_threadsafe(hub.to_ui("pet_state", {"state": state}), loop)

        def on_text(text: str) -> None:  # runs in a voice worker thread
            fut = asyncio.run_coroutine_threadsafe(
                pipeline.handle_reply(text, cfg.default_student, "voice"), loop)
            out = fut.result(timeout=90)
            if out and cfg.voice.speak_replies:
                voice.speak(out["question"])  # spoken answer == Socratic question text

        voice = VoiceService(cfg.voice, on_text, on_state)
        try:
            voice.start_hotkey()
        except Exception:
            log.exception("global hotkey unavailable; REST /api/voice/start|stop still works")
    log.info("daemon up on %s:%s | allowlist=%s", cfg.server.host, cfg.server.port, cfg.allowlist())
    yield
    await pipeline.stop()


app = FastAPI(title="Tutor Pet daemon", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------ websockets
@app.websocket("/ws/editor")
async def ws_editor(ws: WebSocket):
    await ws.accept()
    hub.editors.add(ws)
    log.info("editor connected (%d)", len(hub.editors))
    try:
        while True:
            try:
                msg = await ws.receive_json()
            except ValueError:
                log.warning("non-JSON frame from editor ignored")
                continue
            if isinstance(msg, dict):
                await pipeline.ingest(msg)  # enqueue only; never waits on analysis/LLM
    except WebSocketDisconnect:
        pass
    finally:
        hub.editors.discard(ws)
        log.info("editor disconnected")


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    hub.ui.add(ws)
    await ws.send_json({"channel": "pet_state", "payload": {"state": "idle"}})
    if pipeline._mode:
        await ws.send_json({"channel": "mode", "payload": {"mode": pipeline._mode}})
    try:
        while True:
            await ws.receive_text()  # UI uses REST for requests; this just detects disconnects
    except WebSocketDisconnect:
        pass
    finally:
        hub.ui.discard(ws)


# ------------------------------------------------------------------ REST
class ReplyReq(BaseModel):
    text: str
    student_id: str | None = None
    file: str | None = None
    content: str | None = None
    language: str | None = None
    cursor: dict | None = None
    selection: dict | None = None


class InspectorReq(BaseModel):
    enabled: bool


class PauseReq(BaseModel):
    paused: bool


class RunTestsReq(BaseModel):
    file: str
    language: str
    student_id: str | None = None


class CommandReq(BaseModel):
    command: str  # open_lesson | open_file | show_hint | accept_reply
    path: str | None = None
    file: str | None = None
    line: int | None = None
    text: str | None = None


def _backend_name(b: object) -> str:
    if hasattr(b, "__name__"):
        return b.__name__
    return b.__class__.__name__


@app.get("/api/health")
async def health():
    falkor_ok = False
    try:
        g = getattr(backends, "graph", None)
        if g and hasattr(g, "_client") and g._client:
            falkor_ok = bool(g._client.connection.ping())
        elif g and hasattr(g, "_get_graph"):
            falkor_ok = g._get_graph() is not None
    except Exception:
        falkor_ok = False

    if not falkor_ok:
        raise HTTPException(
            status_code=503,
            detail="FalkorDB is required! Socratic Code-Tutor Pet cannot operate without an active FalkorDB instance on localhost:6379."
        )

    return {
        "ok": True,
        "falkordb": "connected",
        "paused": pipeline.paused,
        "editors": len(hub.editors),
        "uis": len(hub.ui),
        "inspector": pipeline.inspector,
        "voice": voice is not None,
        "backends": {k: _backend_name(getattr(backends, k)) for k in ("analysis", "tutor", "graph")},
    }


@app.post("/api/reply")
async def reply(req: ReplyReq):
    return await pipeline.handle_reply(
        req.text,
        req.student_id or cfg.default_student,
        file=req.file,
        content=req.content,
        language=req.language,
        cursor=req.cursor,
        selection=req.selection,
    )


@app.get("/api/progress")
async def progress(student_id: str | None = None):
    sid = student_id or cfg.default_student
    try:
        data = await asyncio.to_thread(backends.graph.get_progress, sid, mode=pipeline._mode)
    except TypeError:
        data = await asyncio.to_thread(backends.graph.get_progress, sid)
    return as_dict(data)


@app.get("/api/graph")
async def graph(student_id: str | None = None):
    return as_dict(await asyncio.to_thread(backends.graph.get_graph, student_id or cfg.default_student))


@app.get("/api/review")
async def review(design_id: str | None = None):
    return pipeline.get_review(design_id)


@app.post("/api/inspector")
async def inspector(req: InspectorReq):
    pipeline.inspector = req.enabled
    return {"enabled": pipeline.inspector}


@app.post("/api/pause")
async def pause(req: PauseReq):
    pipeline.paused = req.paused
    log.info("paused=%s", req.paused)
    if req.paused:
        pipeline._batches.clear()
        for t in pipeline._timers.values():
            t.cancel()
    return {"paused": pipeline.paused}


def _need_voice():
    if voice is None:
        raise HTTPException(503, "voice disabled: set voice.enabled=true in config.yaml")
    return voice


@app.post("/api/voice/start")
async def voice_start():
    await asyncio.to_thread(_need_voice().start_recording)
    return {"recording": True}


@app.post("/api/voice/stop")
async def voice_stop():
    await asyncio.to_thread(_need_voice().stop_recording)
    return {"recording": False}


@app.post("/api/run_tests")
async def run_tests(req: RunTestsReq):
    if not cfg.is_allowed(req.file):
        raise HTTPException(403, "file is outside the allowlisted lessons folders")
    lesson = pipeline.find_lesson(req.file)
    if not lesson:
        raise HTTPException(404, "no lesson.yaml found above this file")
    langs = lesson.get("languages") or []
    if langs and req.language not in langs:
        raise HTTPException(400, f"lesson '{lesson['id']}' supports {langs}, not '{req.language}'")
    sid = req.student_id or cfg.default_student
    await hub.to_ui("pet_state", {"state": "thinking"})
    result = as_dict(await asyncio.to_thread(backends.analysis.run_tests, lesson["id"], req.language, lesson["_dir"]))
    result["all_passed"] = bool(result.get("results")) and all(r.get("passed") for r in result["results"])
    await pipeline.after_tests(sid, req.file, req.language, result)
    return result


@app.post("/api/editor/command")
async def editor_command(req: CommandReq):
    """Lets any module (tutor, UI) drive VS Code. Paths must be allowlisted."""
    target = req.path or req.file
    if req.command in ("open_lesson", "open_file") and not (target and cfg.is_allowed(target)):
        raise HTTPException(403, "path outside allowlist")
    if not hub.editors:
        raise HTTPException(409, "no editor connected")
    await hub.to_editor(req.command, **req.model_dump(exclude={"command"}, exclude_none=True))
    return {"sent": req.command, "editors": len(hub.editors)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)
