# Linden Health · Voice AI Doctor Appointment Assistant

A caller talks to **Asha**, a voice agent that can check a doctor's availability, book and cancel appointments.
The caller is identified only by the phone number they say aloud, and Asha reads it back before booking or cancelling.

- **Voice pipeline:** Pipecat (Python), SmallWebRTC transport, Silero VAD
- **STT / TTS:** ElevenLabs
- **LLM:** Google Gemini (`gemini-2.5-flash`, free quota) via `GOOGLE_API_KEY`
- **Frontend:** plain HTML/CSS/JS dashboard, served by the same FastAPI server
- **Data:** in-memory mock doctors/slots (resets on restart)

## Run it

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env                                  # then add GOOGLE_API_KEY and ELEVENLABS_API_KEY
python backend/main.py
```

Open http://localhost:7860 and click **Try call** (allow the microphone; use headphones to avoid echo).
First start downloads the Silero VAD model, so give it a few seconds.

If pip resolves a Pipecat version whose import paths differ, run `pip freeze | grep pipecat` once it works and pin that version in `requirements.txt`.

## Demo script (3 min video)

1. Click **Try call**. Asha greets you.
2. "Do you have any cardiology slots tomorrow?" (dashboard shows the same slots under Doctors)
3. "Book the ten o'clock one." Give your name, then say your number. Asha reads it back; say yes.
4. The booking card appears in the phone and the new row flashes under **Appointments**.
5. Optional: call again, say `9876543210`, and cancel the seeded demo appointment.

## How it works

```
browser mic ──WebRTC──> SmallWebRTC ──> Silero VAD ──> ElevenLabs STT ──> Gemini ──> ElevenLabs TTS ──WebRTC──> speaker
                                                                            │
                                         check_doctor_availability / book_appointment / cancel_appointment
                                                                            │
                                                                    in-memory store ──> SSE ──> dashboard
```

- `backend/bot.py`: the pipeline, system prompt, and transcript tap
- `backend/tools.py`: the three function-calling tools; booking and cancelling are refused unless `phone_confirmed` is true, which the prompt only allows after the read-back
- `backend/store.py`: mock doctors, slot generation, phone normalisation, booking rules
- `backend/main.py`: WebRTC offer endpoint, REST (`/api/doctors`, `/api/appointments`), SSE (`/api/events`)
- `frontend/`: dashboard, phone-style call screen (ring, timer, mute, end, waveform, confirmation card, sounds, mobile layout)

## Pipeline description (draft, rewrite in your own words before submitting)

Browser audio goes to a Pipecat pipeline over WebRTC. Silero VAD detects when the caller stops talking, ElevenLabs
turns speech into text, and Gemini decides what to say or which tool to call. Tool results come from an in-memory
clinic store; Gemini turns them into a reply, ElevenLabs speaks it back. Tools publish events to the dashboard
over SSE, so the transcript and appointments update live.
