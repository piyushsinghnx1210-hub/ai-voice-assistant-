(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const getJSON = (url) => fetch(url).then((r) => r.json());

  /* ---------- dashboard data ---------- */
  let doctors = [];
  let appointments = [];
  let selectedDay = null;
  let flashId = null;

  const dayLabel = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  const time12 = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
  const phoneFmt = (p) => `${p.slice(0, 5)} ${p.slice(5)}`;

  function renderDoctors() {
    const days = [...new Set(doctors.flatMap((d) => d.slots.map((s) => s.date)))].sort();
    if (!selectedDay || !days.includes(selectedDay)) selectedDay = days[0] || null;
    $("#dayTabs").innerHTML = days.map((d) =>
      `<button class="tab" role="tab" data-day="${d}" aria-selected="${d === selectedDay}">${esc(dayLabel(d))}</button>`).join("");
    $("#doctorList").innerHTML = doctors.map((d) => {
      const slots = d.slots.filter((s) => s.date === selectedDay);
      return `<article class="doc">
        <div><h3>${esc(d.name)}</h3><div class="meta">${esc(d.speciality)}, ${d.experience_years} yrs</div></div>
        <div class="slots">${slots.length ? slots.map((s) => `<span class="slot">${time12(s.time)}</span>`).join("") : `<span class="none">No slots this day</span>`}</div>
      </article>`;
    }).join("");
  }

  function renderAppointments() {
    const list = $("#apptList");
    if (!appointments.length) { list.innerHTML = `<li class="empty">No appointments yet. Start a call to book one.</li>`; return; }
    list.innerHTML = appointments.map((a) => `
      <li class="stub ${a.status === "cancelled" ? "cancelled" : ""} ${a.id === flashId ? "flash" : ""}" data-id="${a.id}">
        <div class="who">${esc(a.patient_name)}</div><div class="when">${esc(a.label)}</div>
        <div class="sub">${esc(a.doctor_name)}, ${esc(a.speciality)}<br>${phoneFmt(a.phone)}</div>
        <div class="state">${a.status === "booked" ? "Booked" : "Cancelled"}<br><span class="sub">${a.id}</span></div>
      </li>`).join("");
  }

  async function refresh() {
    [doctors, appointments] = await Promise.all([getJSON("/api/doctors"), getJSON("/api/appointments")]);
    renderDoctors(); renderAppointments();
  }

  $("#dayTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-day]"); if (!b) return;
    selectedDay = b.dataset.day; renderDoctors();
  });

  /* ---------- live transcript + events (SSE) ---------- */
  function addLine(role, text) {
    for (const sel of ["#transcript", "#pTranscript"]) {
      const box = $(sel);
      const el = document.createElement("div");
      el.className = `line ${role}`; el.textContent = text;
      box.appendChild(el); box.scrollTop = box.scrollHeight;
    }
  }
  const clearTranscript = () => { $("#transcript").innerHTML = ""; $("#pTranscript").innerHTML = ""; };

  let cardTimer;
  function showCard(action, a) {
    const card = $("#pCard");
    card.className = `p-card ${action === "cancelled" ? "cancelled" : ""}`;
    card.innerHTML = `<b>${action === "booked" ? "Appointment booked" : "Appointment cancelled"}</b>${esc(a.doctor_name)}<br>${esc(a.label)}`;
    card.hidden = false; clearTimeout(cardTimer);
    cardTimer = setTimeout(() => (card.hidden = true), 9000);
  }

  function connectEvents() {
    const es = new EventSource("/api/events");
    es.addEventListener("transcript", (e) => { const d = JSON.parse(e.data); addLine(d.role, d.text); });
    es.addEventListener("appointment", async (e) => {
      const d = JSON.parse(e.data);
      flashId = d.appointment.id; showCard(d.action, d.appointment);
      await refresh(); setTimeout(() => { flashId = null; }, 3000);
    });
  }

  /* ---------- sounds ---------- */
  let actx; let ringTimer;
  const audioCtx = () => (actx ||= new (window.AudioContext || window.webkitAudioContext)());
  function tone(freq, start, dur, vol = 0.08) {
    const c = audioCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = freq; g.gain.setValueAtTime(0, c.currentTime + start);
    g.gain.linearRampToValueAtTime(vol, c.currentTime + start + 0.02);
    g.gain.linearRampToValueAtTime(0, c.currentTime + start + dur);
    o.connect(g).connect(c.destination); o.start(c.currentTime + start); o.stop(c.currentTime + start + dur + 0.05);
  }
  const ringOnce = () => { tone(440, 0, 0.4); tone(480, 0, 0.4); tone(440, 0.55, 0.4); tone(480, 0.55, 0.4); };
  const startRing = () => { ringOnce(); ringTimer = setInterval(ringOnce, 2400); };
  const stopRing = () => clearInterval(ringTimer);
  const endSound = () => { tone(520, 0, 0.18); tone(390, 0.2, 0.18); tone(290, 0.4, 0.3); };

  /* ---------- call ---------- */
  let pc, micStream, remoteStream, timerId, callStart, ringStart, active = false, muted = false;
  const phone = $("#phoneWrap"), pState = $("#pState"), pTimer = $("#pTimer");

  function setState(label, cls) { pState.textContent = label; phone.firstElementChild.className = `phone ${cls || ""}`; }
  const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  const iceComplete = (conn) => new Promise((res) => {
    if (conn.iceGatheringState === "complete") return res();
    const done = () => { if (conn.iceGatheringState === "complete") { conn.removeEventListener("icegatheringstatechange", done); res(); } };
    conn.addEventListener("icegatheringstatechange", done); setTimeout(res, 2500);
  });

  async function startCall() {
    if (active) return;
    active = true; muted = false; $("#tryCall").disabled = true;
    clearTranscript(); $("#pCard").hidden = true; pTimer.textContent = "00:00";
    setMuteUI(); phone.hidden = false; setState("Calling", "ringing"); startRing(); ringStart = Date.now();
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
      micStream.getAudioTracks().forEach((t) => pc.addTrack(t, micStream));
      pc.ontrack = (e) => { remoteStream = e.streams[0]; $("#remoteAudio").srcObject = remoteStream; hookAnalyser("remote", remoteStream); };
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") onConnected();
        if (["failed", "closed"].includes(pc.connectionState)) endCall("Connection lost");
      };
      hookAnalyser("mic", micStream);
      await pc.setLocalDescription(await pc.createOffer());
      await iceComplete(pc);
      const res = await fetch("/api/offer", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }) });
      if (!res.ok) throw new Error("offer failed");
      const answer = await res.json();
      await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
    } catch (err) {
      console.error(err);
      endCall(err.name === "NotAllowedError" ? "Microphone blocked" : "Could not connect");
    }
  }

  function onConnected() {
    if (!active || callStart) return;
    setTimeout(() => {
      if (!active) return;
      stopRing(); tone(660, 0, 0.12); callStart = Date.now();
      setState("Connected", "live"); $("#remoteAudio").play().catch(() => {});
      timerId = setInterval(() => (pTimer.textContent = fmt(Math.floor((Date.now() - callStart) / 1000))), 500);
    }, Math.max(0, 1800 - (Date.now() - ringStart)));
  }

  function endCall(reason = "Call ended") {
    if (!active) return;
    active = false; stopRing(); clearInterval(timerId); endSound();
    const length = callStart ? fmt(Math.floor((Date.now() - callStart) / 1000)) : "";
    callStart = null;
    try { pc && pc.close(); } catch {}
    micStream && micStream.getTracks().forEach((t) => t.stop());
    analysers = {}; pc = micStream = remoteStream = null;
    setState(length ? `${reason} · ${length}` : reason, "ended");
    setTimeout(() => { if (!active) phone.hidden = true; $("#tryCall").disabled = false; }, 2600);
    if (active === false) $("#tryCall").disabled = true;
  }

  function setMuteUI() {
    $("#muteBtn").setAttribute("aria-pressed", muted); $("#muteBtn span").textContent = muted ? "Unmute" : "Mute";
    $("#muteSlash").style.display = muted ? "" : "none";
  }
  $("#muteBtn").addEventListener("click", () => {
    if (!micStream) return; muted = !muted;
    micStream.getAudioTracks().forEach((t) => (t.enabled = !muted)); setMuteUI();
  });
  $("#endBtn").addEventListener("click", () => endCall());
  $("#tryCall").addEventListener("click", () => { audioCtx().resume(); startCall(); });

  /* ---------- waveform ---------- */
  let analysers = {};
  function hookAnalyser(key, stream) {
    const c = audioCtx(), src = c.createMediaStreamSource(stream), an = c.createAnalyser();
    an.fftSize = 128; an.smoothingTimeConstant = 0.75; src.connect(an);
    analysers[key] = { an, data: new Uint8Array(an.frequencyBinCount) };
  }
  const canvas = $("#wave"), g = canvas.getContext("2d");
  function level(a) { a.an.getByteFrequencyData(a.data); return a.data.reduce((s, v) => s + v, 0) / a.data.length / 255; }
  function draw() {
    requestAnimationFrame(draw);
    if (phone.hidden) return;
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const agent = analysers.remote ? level(analysers.remote) : 0;
    const you = analysers.mic && !muted ? level(analysers.mic) : 0;
    const src = agent >= you ? analysers.remote : analysers.mic;
    g.fillStyle = agent >= you ? "#8FD6BC" : "#E8F1EE";
    const bars = 32, gap = 4, bw = (w - gap * (bars - 1)) / bars, t = performance.now() / 600;
    for (let i = 0; i < bars; i++) {
      const v = src && src.data[Math.floor((i / bars) * src.data.length * 0.7)] / 255 || 0;
      const idle = 0.05 + 0.03 * Math.sin(t + i * 0.5);
      const bh = Math.max(3, (Math.max(v, idle)) * h * 0.95);
      g.beginPath(); g.roundRect(i * (bw + gap), (h - bh) / 2, bw, bh, bw / 2); g.fill();
    }
  }

  refresh(); connectEvents(); draw();
})();
