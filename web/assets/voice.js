/* Voice bench: chat-first UI with a debug tab.
 *
 * One recording becomes a full turn: capture → ASR → model (streamed) → speech.
 * Every step is also written to the debug log so a failure can be attributed
 * to a single stage instead of "it did not work".
 */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    tab: "chat",
    asrReady: false,
    busy: false,
    stream: null,
    audioCtx: null,
    processor: null,
    chunks: [],
    rate: 48000,
    timer: null,
    retranscript: "",
    lastWav: "",
    captureMode: "chat",
    abort: null,
  };

  const titles = {
    chat: ["گفتگو", "صحبت کنید؛ رونویسی، پاسخ و صدا خودکار انجام می‌شود."],
    debug: ["دیباگ", "هر مرحله را جدا ببینید، اندازه بگیرید و دوباره اجرا کنید."],
    help: ["راهنما", "نکات استفاده، تنظیم ASR و عیب‌یابی."],
  };

  const faNumber = (n) => new Intl.NumberFormat("fa-IR").format(Number(n) || 0);
  const fmtMs = (v) => (v === undefined || v === null ? "—" : v < 1000 ? `${Math.round(v)}ms` : `${(v / 1000).toFixed(1)}s`);
  const now = () => new Date().toLocaleTimeString("fa-IR", { hour12: false });

  // ------------------------------------------------------------- shell/nav
  function showTab(tab) {
    if (!titles[tab]) return;
    state.tab = tab;
    document.querySelectorAll(".tab-view").forEach((el) => el.classList.toggle("active", el.id === `tab-${tab}`));
    document.querySelectorAll(".nav-link[data-tab]").forEach((el) => el.classList.toggle("active", el.dataset.tab === tab));
    $("pageTitle").textContent = titles[tab][0];
    $("pageSubtitle").textContent = titles[tab][1];
    closeMenu();
    if (tab === "debug") loadDiagnostics();
  }
  function openMenu() { $("sidebar").classList.add("open"); $("scrim").classList.add("show"); }
  function closeMenu() { $("sidebar").classList.remove("open"); $("scrim").classList.remove("show"); }
  document.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => showTab(el.dataset.tab)));
  $("menuToggle").addEventListener("click", () => ($("sidebar").classList.contains("open") ? closeMenu() : openMenu()));
  $("scrim").addEventListener("click", closeMenu);

  // ---------------------------------------------------------------- helpers
  function notify(text, kind = "ok") {
    const el = $("chatNotice");
    el.textContent = text || "";
    el.style.color = kind === "error" ? "var(--red)" : kind === "warn" ? "var(--amber)" : "var(--muted)";
    if (kind === "error") showError(text);
  }
  function showError(message) {
    const box = $("error");
    box.textContent = message || "";
    box.classList.toggle("hidden", !message);
  }
  function log(level, text) {
    const box = $("debugLog");
    if (box.firstElementChild && box.firstElementChild.classList.contains("muted")) box.innerHTML = "";
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span class="ts mono">${now()}</span><span class="lvl ${level}">${level === "ok" ? "✓" : level === "warn" ? "!" : "✕"}</span><span>${escapeHtml(text)}</span>`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
  function setBadge(id, text, kind) {
    const el = $(id);
    el.textContent = text;
    el.className = "badge" + (kind ? " " + kind : "");
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `خطای سرور (${response.status})`);
    return data;
  }

  // ------------------------------------------------------------------- chat
  function clearEmpty() { $("chatEmpty")?.remove(); }
  function scrollLog() { const log = $("chatLog"); log.scrollTop = log.scrollHeight; }

  function addMessage(role, text, meta) {
    clearEmpty();
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    const avatar = role === "user" ? "🧑" : "✿";
    wrap.innerHTML = `<span class="avatar">${avatar}</span><div><div class="bubble"></div><div class="msg-meta"></div></div>`;
    wrap.querySelector(".bubble").textContent = text || "";
    if (meta) wrap.querySelector(".msg-meta").innerHTML = meta;
    $("chatLog").appendChild(wrap);
    scrollLog();
    return wrap;
  }

  function setMeta(wrap, html) { if (wrap) wrap.querySelector(".msg-meta").innerHTML = html; }

  async function streamChat(text) {
    const bubble = addMessage("assistant", "", "");
    bubble.classList.add("pending");
    const body = bubble.querySelector(".bubble");
    state.abort = new AbortController();
    const sendBtn = $("sendBtn");
    sendBtn.textContent = "توقف";
    sendBtn.classList.add("danger");
    const started = performance.now();
    let ttft = null;
    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: state.abort.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `خطای سرور (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = false;
      while (!finished) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, index).trim();
          buffer = buffer.slice(index + 2);
          if (!chunk.startsWith("data:")) continue;
          const raw = chunk.slice(5).trim();
          if (raw === "[DONE]") { finished = true; break; }
          let event;
          try { event = JSON.parse(raw); } catch { continue; }
          if (event.type === "delta") {
            if (ttft === null) ttft = Math.round(performance.now() - started);
            body.textContent += event.text;
            scrollLog();
          } else if (event.type === "done") {
            finished = true;
            body.textContent = event.reply || body.textContent;
            const t = event.timings || {};
            const tokens = t.completion_tokens ? `<span class="mono">${faNumber(t.completion_tokens)} tok</span>` : "";
            setMeta(bubble, `<span class="mono">ttft ${fmtMs(event.ttft_ms ?? ttft)}</span>`
              + `<span class="mono">llm ${fmtMs(t.llm_ms)}</span>` + tokens);
            log("ok", `پاسخ مدل در ${fmtMs(t.llm_ms)} (ttft ${fmtMs(event.ttft_ms ?? ttft)})`);
            setBadge("stageLlmBadge", "سالم", "ok");
            $("stageLlm").innerHTML = `<b>${fmtMs(t.llm_ms)}</b> · ttft ${fmtMs(event.ttft_ms ?? ttft)}`
              + (t.completion_tokens ? ` · ${faNumber(t.completion_tokens)} توکن` : "");
            const wait = event.ttft_ms ?? ttft;
            if (wait && wait > 20000) {
              notify("پاسخ‌دهی این مدل روی این رایانه کند است؛ در پنل یک مدل سبک‌تر (مثل Qwen3-0.6B) را فعال کنید.", "warn");
            }
            if ($("autoSpeak").checked && event.reply) await speak(event.reply);
          } else if (event.type === "error") {
            throw new Error(event.detail || "خطای مدل");
          }
        }
      }
      bubble.classList.remove("pending");
      if (!body.textContent.trim()) {
        body.textContent = "پاسخی دریافت نشد.";
        log("warn", "مدل متن قابل نمایش برنگرداند.");
      }
      return body.textContent;
    } catch (err) {
      bubble.classList.remove("pending");
      if (err.name === "AbortError") { log("warn", "ارسال لغو شد."); return ""; }
      body.textContent = "خطا در پاسخ مدل.";
      setMeta(bubble, `<span style="color:var(--red)">${escapeHtml(err.message)}</span>`);
      setBadge("stageLlmBadge", "خطا", "bad");
      log("err", `مدل: ${err.message}`);
      showError(`پاسخ مدل دریافت نشد: ${err.message}`);
      return "";
    } finally {
      state.abort = null;
      sendBtn.textContent = "ارسال";
      sendBtn.classList.remove("danger");
      pollStatus();
    }
  }

  async function speak(text) {
    try {
      const started = performance.now();
      const data = await postJson("/api/tts", { text });
      const ms = data.timings?.tts_ms ?? Math.round(performance.now() - started);
      setBadge("stageTtsBadge", "سالم", "ok");
      $("stageTts").innerHTML = `<b>${fmtMs(ms)}</b> · ${faNumber(text.length)} نویسه`;
      log("ok", `صدا در ${fmtMs(ms)} ساخته شد.`);
      if (data.tts_wav_b64) {
        state.lastWav = data.tts_wav_b64;
        const audio = new Audio("data:audio/wav;base64," + data.tts_wav_b64);
        await audio.play().catch(() => notify("پخش صدا ممکن نشد؛ روی صفحه کلیک کنید.", "warn"));
      }
    } catch (err) {
      setBadge("stageTtsBadge", "خطا", "bad");
      log("err", `صدا: ${err.message}`);
    }
  }

  async function sendText(text) {
    if (!text.trim() || state.busy) return;
    addMessage("user", text.trim());
    $("chatText").value = "";
    $("chatText").style.height = "auto";
    state.busy = true;
    await streamChat(text.trim());
    state.busy = false;
  }

  $("chatForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.abort) { state.abort.abort(); return; }
    sendText($("chatText").value);
  });
  $("chatText").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendText($("chatText").value); }
  });
  $("chatText").addEventListener("input", (event) => {
    const el = event.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 150) + "px";
  });

  // -------------------------------------------------------------- recording
  function downsampleTo16k(float32, sourceRate) {
    if (sourceRate === 16000) return float32;
    const ratio = sourceRate / 16000;
    const length = Math.floor(float32.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(Math.floor((i + 1) * ratio), float32.length);
      let sum = 0;
      for (let j = start; j < end; j++) sum += float32[j];
      output[i] = sum / Math.max(1, end - start);
    }
    return output;
  }
  async function resampleTo16k(float32, sourceRate) {
    if (sourceRate === 16000) return float32;
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) return downsampleTo16k(float32, sourceRate);
    const target = Math.max(1, Math.round((float32.length * 16000) / sourceRate));
    const context = new Offline(1, target, 16000);
    const buffer = context.createBuffer(1, float32.length, sourceRate);
    buffer.copyToChannel(float32, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.start();
    const rendered = await context.startRendering();
    return rendered.getChannelData(0);
  }
  function toBase64Pcm16(float32) {
    const bytes = new Uint8Array(float32.length * 2);
    const view = new DataView(bytes.buffer);
    for (let i = 0; i < float32.length; i++) {
      const sample = Math.max(-1, Math.min(1, float32[i]));
      view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function setRecordingUI(active, mode) {
    const chatMic = $("mic");
    const debugBtn = $("micDebug");
    if (mode === "debug") {
      debugBtn.classList.toggle("recording", active);
      debugBtn.textContent = active ? "⏹ توقف ضبط" : "🎤 ضبط آزمایشی";
      chatMic.classList.remove("recording");
    } else {
      chatMic.classList.toggle("recording", active);
    }
  }

  async function startRecording(mode) {
    if (state.stream) return;
    if (state.busy) { notify("در حال پردازش پاسخ قبلی است؛ کمی صبر کنید.", "warn"); return; }
    if (!state.asrReady) { notify("ASR هنوز آماده نیست؛ چند لحظه صبر کنید.", "warn"); return; }
    state.captureMode = mode;
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true },
      });
    } catch (err) {
      showError("دسترسی به میکروفون رد شد: " + err.message);
      log("err", "میکروفون: " + err.message);
      return;
    }
    showError("");
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.rate = state.audioCtx.sampleRate;
    const source = state.audioCtx.createMediaStreamSource(state.stream);
    state.processor = state.audioCtx.createScriptProcessor(4096, 1, 1);
    state.chunks = [];
    state.processor.onaudioprocess = (event) => state.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(state.processor);
    state.processor.connect(state.audioCtx.destination);
    setRecordingUI(true, mode);
    setBadge("stageCaptureBadge", "در حال ضبط", "warn");
    log("ok", mode === "debug" ? "ضبط آزمایشی شروع شد." : "ضبط شروع شد.");
    const started = performance.now();
    state.timer = setInterval(() => {
      const secs = ((performance.now() - started) / 1000).toFixed(1) + "s";
      $("recTimer").textContent = secs;
      if (mode === "debug") $("micDebug").textContent = `⏹ توقف ضبط (${secs})`;
    }, 100);
  }

  async function stopRecording() {
    if (!state.stream) return;
    const mode = state.captureMode;
    clearInterval(state.timer);
    state.processor?.disconnect();
    state.processor = null;
    state.stream.getTracks().forEach((track) => track.stop());
    await state.audioCtx?.close();
    state.audioCtx = null;
    state.stream = null;
    setRecordingUI(false, mode);
    $("recTimer").textContent = "۰٫۰s";
    const total = state.chunks.reduce((sum, part) => sum + part.length, 0);
    if (total < 1600) {
      setBadge("stageCaptureBadge", "خیلی کوتاه", "warn");
      notify("ضبط خیلی کوتاه بود؛ دوباره و کمی طولانی‌تر بگویید.", "warn");
      state.chunks = [];
      return;
    }
    const joined = new Float32Array(total);
    let offset = 0;
    for (const part of state.chunks) { joined.set(part, offset); offset += part.length; }
    const pcmB64 = toBase64Pcm16(await resampleTo16k(joined, state.rate));
    state.chunks = [];
    await transcribe(pcmB64, false, mode);
  }

  async function transcribe(pcmB64, useLast, mode = "chat") {
    setBadge("stageAsrBadge", "در حال اجرا", "warn");
    const started = performance.now();
    try {
      const body = asrPayload();
      body.use_last = useLast;
      if (!useLast) body.pcm_b64 = pcmB64;
      const data = await postJson("/api/transcribe", body);
      const ms = data.timings?.asr_ms ?? Math.round(performance.now() - started);
      const audio = data.audio || {};
      $("audioStats").textContent = `مدت ${audio.seconds}s · RMS ${audio.rms_dbfs}dBFS · peak ${audio.peak_dbfs}dBFS · گفتار ${Math.round((audio.speech_ratio || 0) * 100)}٪`;
      $("stageCapture").innerHTML = `<b>${audio.seconds || 0}s</b> · RMS ${audio.rms_dbfs}dBFS · گفتار ${Math.round((audio.speech_ratio || 0) * 100)}٪`;
      setBadge("stageCaptureBadge", audio.rms_dbfs < -45 ? "ضعیف" : "سالم", audio.rms_dbfs < -45 ? "warn" : "ok");
      setBadge("stageAsrBadge", data.transcript ? "سالم" : "خالی", data.transcript ? "ok" : "warn");
      $("stageAsr").innerHTML = `<b>${fmtMs(ms)}</b> · ${escapeHtml(data.transcript || "چیزی شنیده نشد")}`;
      $("asrMeta").textContent = `asr ${fmtMs(ms)}${useLast ? " · همان ضبط قبلی" : ""}`;
      log(data.transcript ? "ok" : "warn", `ASR ${fmtMs(ms)}: ${data.transcript || "خالی"}`);
      if (useLast) {
        state.retranscript = data.transcript || "";
        $("retransMeta").textContent = `نتیجه: ${data.transcript || "«چیزی شنیده نشد»"} · ${fmtMs(ms)}`;
        $("applyRetrans").disabled = !state.retranscript;
        return;
      }
      $("transcript").value = data.transcript || "";
      if (!data.transcript) {
        notify("چیزی شنیده نشد؛ واضح‌تر بگویید یا تنظیمات ASR را در «دیباگ» تغییر دهید.", "warn");
        return;
      }
      if (mode === "debug") { notify("رونویسی انجام شد؛ در تب دیباگ قابل ویرایش است."); return; }
      addMessage("user", data.transcript, `<span class="mono">asr ${fmtMs(ms)}</span>`);
      if ($("autoSend").checked) {
        state.busy = true;
        await streamChat(data.transcript);
        state.busy = false;
      } else {
        notify("رونویسی آماده است؛ «ارسال خودکار» خاموش است.", "warn");
      }
    } catch (err) {
      setBadge("stageAsrBadge", "خطا", "bad");
      log("err", `ASR: ${err.message}`);
      showError(String(err.message || err));
    }
  }

  function asrPayload() {
    const payload = { beam_size: Number($("beam").value), language: $("lang").value };
    const prompt = $("prompt").value.trim();
    if (prompt) payload.initial_prompt = prompt;
    return payload;
  }

  // ASR options are remembered across reloads so they never have to be re-entered.
  const ASR_STORE = "pvai.asr.settings";
  function loadAsrSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(ASR_STORE) || "{}");
      if (saved.beam) $("beam").value = String(saved.beam);
      if (saved.lang) $("lang").value = saved.lang;
      if (typeof saved.prompt === "string") $("prompt").value = saved.prompt;
    } catch { /* ignore corrupt storage */ }
  }
  function saveAsrSettings() {
    try {
      localStorage.setItem(ASR_STORE, JSON.stringify({
        beam: $("beam").value, lang: $("lang").value, prompt: $("prompt").value,
      }));
    } catch { /* storage may be unavailable */ }
  }
  ["beam", "lang", "prompt"].forEach((id) => {
    $(id).addEventListener("change", saveAsrSettings);
    $(id).addEventListener("input", saveAsrSettings);
  });
  loadAsrSettings();

  // mic interactions: hold-to-talk on the chat mic, click-to-toggle in debug
  const mic = $("mic");
  mic.addEventListener("pointerdown", (event) => { event.preventDefault(); startRecording("chat"); });
  mic.addEventListener("pointerup", (event) => { event.preventDefault(); stopRecording(); });
  mic.addEventListener("pointerleave", () => { if (state.stream) stopRecording(); });
  mic.addEventListener("contextmenu", (event) => event.preventDefault());

  $("micDebug").addEventListener("click", () => {
    if (state.stream) stopRecording();
    else startRecording("debug");
  });

  // ------------------------------------------------------------ debug tools
  $("retranscribe").addEventListener("click", () => transcribe("", true));
  $("applyRetrans").addEventListener("click", () => {
    if (!state.retranscript) return;
    $("transcript").value = state.retranscript;
    $("asrMeta").textContent = "رونویسی از تنظیمات جدید پذیرفته شد.";
  });
  $("fillFromTranscript").addEventListener("click", () => { $("chatInput").value = $("transcript").value.trim(); });
  $("fillFromReply").addEventListener("click", () => { $("ttsInput").value = $("chatReply").textContent.trim(); });

  $("sendChat").addEventListener("click", async () => {
    const text = $("chatInput").value.trim();
    if (!text) return;
    $("sendChat").disabled = true;
    try {
      const data = await postJson("/api/chat", { text });
      $("chatReply").textContent = data.reply || "پاسخ خالی بود.";
      $("chatMeta").textContent = `llm ${fmtMs(data.timings.llm_ms)} · prompt ${data.timings.prompt_tokens} · out ${data.timings.completion_tokens}`;
      $("ttsInput").value = data.reply || $("ttsInput").value;
      log("ok", `مدل (بدون استریم) ${fmtMs(data.timings.llm_ms)}`);
      pollStatus();
    } catch (err) {
      showError(String(err.message || err));
      log("err", `مدل: ${err.message}`);
    } finally {
      $("sendChat").disabled = false;
    }
  });

  $("playTts").addEventListener("click", async () => {
    const text = $("ttsInput").value.trim();
    if (!text) return;
    $("playTts").disabled = true;
    try {
      const data = await postJson("/api/tts", { text });
      state.lastWav = data.tts_wav_b64 || "";
      if (state.lastWav) new Audio("data:audio/wav;base64," + state.lastWav).play().catch(() => {});
      $("ttsMeta").textContent = `tts ${fmtMs(data.timings.tts_ms)}`;
      log("ok", `صدا ${fmtMs(data.timings.tts_ms)}`);
    } catch (err) {
      showError(String(err.message || err));
      log("err", `صدا: ${err.message}`);
    } finally {
      $("playTts").disabled = false;
    }
  });

  $("resetBtn").addEventListener("click", async () => {
    try { await fetch("/api/reset", { method: "POST" }); } catch { /* ignore */ }
    $("chatLog").innerHTML = `<div class="chat-empty" id="chatEmpty"><span class="big">🎙️</span>دکمهٔ میکروفون را نگه دارید و صحبت کنید.<br />متن شنیده‌شده، پاسخ مدل و صدای پاسخ همین‌جا نمایش داده می‌شود.</div>`;
    $("transcript").value = "";
    $("chatReply").textContent = "هنوز پاسخی نیست.";
    $("chatMeta").textContent = "";
    $("asrMeta").textContent = "";
    $("ttsMeta").textContent = "";
    $("retransMeta").textContent = "";
    state.retranscript = "";
    $("applyRetrans").disabled = true;
    $("audioStats").textContent = "هنوز ضبطی نیست.";
    showError("");
    log("warn", "گفتگوی درون‌حافظه پاک شد (فایل روی دیسک حفظ می‌شود).");
    pollStatus();
  });

  $("clearLog").addEventListener("click", () => {
    $("debugLog").innerHTML = '<div class="line muted">هنوز رویدادی ثبت نشده است.</div>';
  });
  $("refreshDiag").addEventListener("click", loadDiagnostics);

  async function loadDiagnostics() {
    try {
      const data = await fetch("/api/diagnostics").then((r) => r.json());
      $("diagOut").textContent = JSON.stringify(data, null, 2);
      const llm = data.llm || {};
      setBadge("stageLlmBadge", llm.reachable ? "سالم" : "قطع", llm.reachable ? "ok" : "bad");
      if (llm.reachable) $("stageLlm").innerHTML = `<b>${escapeHtml(llm.model || "—")}</b> · ${escapeHtml(llm.detail || "")}`;
      else $("stageLlm").innerHTML = `<span style="color:var(--red)">${escapeHtml(llm.detail || "در دسترس نیست")}</span>`;
      log("ok", "تشخیص سیستم بازخوانی شد.");
    } catch (err) {
      $("diagOut").textContent = "خطا: " + err.message;
      log("err", "تشخیص سیستم: " + err.message);
    }
  }

  // --------------------------------------------------------------- history
  async function loadHistory() {
    try {
      const data = await fetch("/api/history").then((r) => r.json());
      const messages = data.messages || [];
      if (!messages.length) return;
      clearEmpty();
      for (const message of messages.slice(-20)) {
        addMessage(message.role === "user" ? "user" : "assistant", message.content);
      }
    } catch { /* first load may race the server */ }
  }

  // ---------------------------------------------------------------- status
  async function pollStatus() {
    try {
      const data = await fetch("/api/status").then((r) => r.json());
      const chip = (id, name, label) => {
        const value = data[name];
        const text = value === "ready" ? "آماده" : value === "failed" ? "خطا" : "بارگذاری…";
        setBadge(id, `${label}: ${text}`, value === "ready" ? "ok" : value === "failed" ? "bad" : "warn");
      };
      chip("chipAsr", "asr", "ASR");
      chip("chipTts", "tts", "TTS");
      $("chipMsgs").textContent = `پیام‌ها: ${faNumber(data.messages)}`;
      state.asrReady = data.asr === "ready";
      mic.disabled = state.busy || !state.asrReady;
      $("connDot").className = "dot " + (data.asr === "failed" ? "bad" : "ok");
      $("connText").textContent = data.asr === "failed" ? "سرویس با خطا" : "متصل";
      if (data.error) showError(data.error);
    } catch {
      $("connDot").className = "dot bad";
      $("connText").textContent = "قطع";
    }
  }

  pollStatus();
  loadHistory();
  loadDiagnostics();
  setInterval(pollStatus, 3000);
})();
