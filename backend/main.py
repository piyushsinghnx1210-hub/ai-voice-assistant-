"""FastAPI server: WebRTC signalling, REST for the dashboard, SSE for live updates, static frontend."""
import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import uvicorn  # noqa: E402
from fastapi import BackgroundTasks, FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: E402

from bot import run_bot  # noqa: E402
from events import bus  # noqa: E402
from store import store  # noqa: E402

app = FastAPI(title="Voice Doctor Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

connections: dict[str, SmallWebRTCConnection] = {}
ICE_SERVERS = ["stun:stun.l.google.com:19302"]


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    pc_id = request.get("pc_id")
    if pc_id and pc_id in connections:
        conn = connections[pc_id]
        await conn.renegotiate(sdp=request["sdp"], type=request["type"],
                               restart_pc=request.get("restart_pc", False))
    else:
        conn = SmallWebRTCConnection(ICE_SERVERS)
        await conn.initialize(sdp=request["sdp"], type=request["type"])

        @conn.event_handler("closed")
        async def on_closed(c: SmallWebRTCConnection):
            connections.pop(c.pc_id, None)

        background_tasks.add_task(run_bot, conn)
    answer = conn.get_answer()
    connections[answer["pc_id"]] = conn
    return answer


@app.get("/api/doctors")
async def doctors():
    return store.doctors_with_slots()


@app.get("/api/appointments")
async def appointments():
    return store.appointments_view()


@app.get("/api/events")
async def events():
    q = bus.subscribe()

    async def stream():
        try:
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
