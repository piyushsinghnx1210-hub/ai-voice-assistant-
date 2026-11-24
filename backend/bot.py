"""Pipecat voice pipeline: mic -> ElevenLabs STT -> Gemini (tools) -> ElevenLabs TTS -> speaker."""
import inspect
import os
from datetime import datetime

import aiohttp
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (LLMFullResponseEndFrame, LLMFullResponseStartFrame,
                                   TextFrame, TranscriptionFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

try:  # newer Pipecat: realtime Scribe STT
    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService as ElevenLabsSTT
except ImportError:
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService as ElevenLabsSTT

try:  # universal context (recent Pipecat)
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
    UNIVERSAL_CONTEXT = True
except ImportError:
    from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
    UNIVERSAL_CONTEXT = False

from events import bus
from tools import HANDLERS, tools
from store import IST


def build(cls, **kwargs):
    """Instantiate a service, passing only the kwargs its constructor accepts (API drift guard)."""
    accepted = inspect.signature(cls.__init__).parameters
    return cls(**{k: v for k, v in kwargs.items() if k in accepted})


def system_prompt() -> str:
    today = datetime.now(IST).strftime("%A, %d %B %Y")
    return f"""You are Asha, the phone assistant for Linden Health clinic. Today is {today} (India time).
You help callers do three things: check a doctor's availability, book an appointment, cancel an appointment.

Voice style: this is a phone call. Speak in short, warm, natural sentences. No lists, markdown, emojis or
symbols. Offer at most three slots at a time. Say dates and times the way a person would ("tomorrow at ten thirty").

Rules you must follow:
1. The caller is identified ONLY by the phone number they say out loud. Never ask for anything else to verify them.
2. Before booking or cancelling, ask for their 10-digit phone number. Callers may say "double nine" or
   "oh" for zero; convert to digits.
3. Read the number back digit by digit in small groups (for example "nine eight seven six five, four three two one zero")
   and ask "Is that right?". Only after a clear yes, call the tool with phone_confirmed=true.
4. Before booking, confirm doctor, day and time with the caller. Ask for their name if you do not have it.
5. Before cancelling, confirm which appointment and that they want to cancel.
6. Use slot_id exactly as returned by check_doctor_availability. Never invent slots, doctors or confirmations.
   If a tool returns an error, explain it simply and offer the next step.
7. After a booking or cancellation, summarise it in one sentence and ask if there is anything else.
If asked for medical advice, say you can only help with appointments."""


class TranscriptTap(FrameProcessor):
    """Pass-through processor that mirrors the conversation to the dashboard."""

    def __init__(self, role: str):
        super().__init__()
        self._role = role
        self._buf: list[str] = []

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._role == "user" and isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                bus.publish("transcript", {"role": "user", "text": frame.text.strip()})
        elif self._role == "assistant":
            if isinstance(frame, LLMFullResponseStartFrame):
                self._buf = []
            elif isinstance(frame, TextFrame):
                self._buf.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                text = "".join(self._buf).strip()
                self._buf = []
                if text:
                    bus.publish("transcript", {"role": "assistant", "text": text})
        await self.push_frame(frame, direction)


async def run_bot(webrtc_connection):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6)),  # bonus: VAD
        ),
    )

    async with aiohttp.ClientSession() as session:
        el_key = os.environ["ELEVENLABS_API_KEY"]
        stt = build(ElevenLabsSTT, api_key=el_key, aiohttp_session=session)
        tts = build(ElevenLabsTTSService, api_key=el_key,
                    voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
        llm = build(GoogleLLMService, api_key=os.environ["GOOGLE_API_KEY"],
                    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

        for name, handler in HANDLERS.items():
            llm.register_function(name, handler)

        messages = [{"role": "system", "content": system_prompt()}]
        if UNIVERSAL_CONTEXT:
            context = LLMContext(messages, tools)
            pair = LLMContextAggregatorPair(context)
            user_agg, assistant_agg = pair.user(), pair.assistant()
        else:
            context = OpenAILLMContext(messages, tools)
            pair = llm.create_context_aggregator(context)
            user_agg, assistant_agg = pair.user(), pair.assistant()

        pipeline = Pipeline([
            transport.input(),
            stt,
            TranscriptTap("user"),
            user_agg,
            llm,
            TranscriptTap("assistant"),
            tts,
            transport.output(),
            assistant_agg,
        ])
        task = PipelineTask(pipeline, params=PipelineParams())

        @transport.event_handler("on_client_connected")
        async def on_connected(_transport, _client):
            logger.info("Caller connected")
            messages.append({"role": "user", "content": "The caller just connected. Greet them in one short sentence."})
            try:
                from pipecat.frames.frames import LLMRunFrame
                await task.queue_frames([LLMRunFrame()])
            except ImportError:
                await task.queue_frames([user_agg.get_context_frame()])

        @transport.event_handler("on_client_disconnected")
        async def on_disconnected(_transport, _client):
            logger.info("Caller disconnected")
            await task.cancel()

        await PipelineRunner(handle_sigint=False).run(task)
