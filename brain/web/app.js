// Client web universel de Jarvis : n'importe quel appareil avec un navigateur,
// un micro et un haut-parleur (PC, téléphone, tablette, télé).

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "jarvis.token";
const PREFS_KEY = "jarvis.prefs";

// ---------- stockage local (peut être indisponible en navigation privée) ----------
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignoré */ } },
  del(key) { try { localStorage.removeItem(key); } catch { /* ignoré */ } },
};
const prefs = Object.assign({ handsfree: false, voice: true }, safeJson(store.get(PREFS_KEY)));
function safeJson(text) { try { return JSON.parse(text) || {}; } catch { return {}; } }
function savePrefs() { store.set(PREFS_KEY, JSON.stringify(prefs)); }

// ---------- interface ----------
const ui = {
  status(text, online) {
    $("status").textContent = text;
    $("status").className = "status " + (online ? "online" : "offline");
  },
  orb(state) { $("talk").className = "orb " + (state || ""); },
  hint(text) { $("hint").textContent = text; },
  add(kind, text) {
    const div = document.createElement("div");
    div.className = "msg " + kind;
    div.textContent = text;
    $("log").appendChild(div);
    $("log").scrollTop = $("log").scrollHeight;
    return div;
  },
};

// ---------- lecture audio : les phrases arrivent une par une et s'enchaînent ----------
class Player {
  constructor() { this.ctx = null; this.nextTime = 0; this.sources = new Set(); this.onIdle = null; }
  attach(ctx) { this.ctx = ctx; }
  get playing() { return this.sources.size > 0; }
  async play(wavBytes) {
    if (!this.ctx) return;
    let buffer;
    try { buffer = await this.ctx.decodeAudioData(wavBytes); } catch { return; }
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    const start = Math.max(this.ctx.currentTime + 0.02, this.nextTime);
    src.start(start);
    this.nextTime = start + buffer.duration;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.playing && this.onIdle) this.onIdle();
    };
  }
  stop() {
    for (const src of this.sources) { try { src.stop(); } catch { /* déjà fini */ } }
    this.sources.clear();
    this.nextTime = 0;
  }
}

// ---------- détection de parole (VAD) par le volume ----------
class Vad {
  constructor() {
    this.floor = 0.01; this.speaking = false; this.heard = false;
    this.loud = 0; this.quiet = 0; this.length = 0;
  }
  // Renvoie "start", "end" ou null pour chaque morceau de 100 ms.
  push(rms) {
    const startLevel = Math.max(this.floor * 3, 0.02);
    const stopLevel = Math.max(this.floor * 2, 0.012);
    if (!this.speaking) {
      this.floor = this.floor * 0.95 + rms * 0.05; // bruit de fond moyen
      this.loud = rms > startLevel ? this.loud + 1 : 0;
      if (this.loud >= 2) { this.speaking = true; this.heard = true; this.quiet = 0; this.length = 0; return "start"; }
      return null;
    }
    this.length++;
    if (rms > startLevel) this.heard = true;
    this.quiet = rms < stopLevel ? this.quiet + 1 : 0;
    // 0,8 s de silence après la parole, ou 4 s si l'on n'a encore rien entendu ; 15 s maximum.
    const maxQuiet = this.heard ? 8 : 40;
    if (this.quiet >= maxQuiet || this.length >= 150) { this.speaking = false; this.loud = 0; return "end"; }
    return null;
  }
  reset() { this.speaking = false; this.heard = false; this.loud = 0; this.quiet = 0; this.length = 0; }
  // Enregistrement demandé par l'utilisateur : on attend qu'il parle, puis le silence.
  forceStart() { this.reset(); this.speaking = true; }
}

// ---------- connexion au cerveau ----------
class Brain {
  constructor(token, handlers) {
    this.token = token;
    this.handlers = handlers;
    this.ws = null;
    this.retry = 0;
    this.pendingAudioFor = null;
    this.closedByUser = false;
  }
  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token: this.token, client: "web/0.1", audio_replies: prefs.voice }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        const meta = this.pendingAudioFor;
        this.pendingAudioFor = null;
        if (meta) this.handlers.audio(meta, ev.data);
        return;
      }
      const msg = safeJson(ev.data);
      if (msg.type === "auth_ok") this.retry = 0;
      if (msg.type === "sentence" && msg.audio) this.pendingAudioFor = msg;
      this.handlers.message(msg);
    };
    ws.onclose = (ev) => {
      this.handlers.closed(ev);
      if (this.closedByUser || ev.code === 4401) return; // token refusé : pas de reconnexion
      const delay = Math.min(30000, 1000 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    };
  }
  get ready() { return this.ws && this.ws.readyState === WebSocket.OPEN; }
  send(obj) { if (this.ready) this.ws.send(JSON.stringify(obj)); }
  sendBytes(buf) { if (this.ready) this.ws.send(buf); }
  close() { this.closedByUser = true; if (this.ws) this.ws.close(); }
}

// ---------- application ----------
const player = new Player();
const vad = new Vad();
let brain = null;
let audioCtx = null;
let micReady = false;
let recording = null; // { wake: bool }
let preroll = [];
let followUpUntil = 0;
let currentTurn = null;
let dropUpTo = 0;
let jarvisBubble = null;

function setupScreen() {
  $("setup").hidden = false;
  $("app").hidden = true;
  ui.status("Non configuré", false);
}

function start(token) {
  $("setup").hidden = true;
  $("app").hidden = false;
  $("handsfree").checked = prefs.handsfree;
  $("voice").checked = prefs.voice;
  brain = new Brain(token, { message: onMessage, audio: onAudio, closed: onClosed });
  brain.connect();
  ui.status("Connexion…", false);
}

function onClosed(ev) {
  ui.orb("");
  if (ev.code === 4401) {
    ui.status("Token refusé", false);
    ui.add("system", "Le serveur a refusé ce token. Vérifie-le ou crées-en un nouveau.");
    store.del(TOKEN_KEY);
    setTimeout(setupScreen, 1500);
  } else if (ev.code === 4429) {
    ui.status("Bloqué (trop d'essais)", false);
  } else {
    ui.status("Reconnexion…", false);
  }
}

function onMessage(msg) {
  switch (msg.type) {
    case "auth_ok":
      ui.status(`En ligne · ${msg.device}`, true);
      ui.hint(prefs.handsfree ? "J'écoute. Dis « Jarvis… »" : "Touche le cercle pour parler");
      break;
    case "state":
      currentTurn = msg.turn_id;
      ui.orb(msg.state === "thinking" || msg.state === "transcribing" ? "thinking" : "");
      break;
    case "transcript":
      currentTurn = msg.turn_id;
      if (msg.text) ui.add("user", msg.text);
      jarvisBubble = null;
      break;
    case "reply_delta":
      if (msg.turn_id <= dropUpTo) break;
      if (!jarvisBubble) jarvisBubble = ui.add("jarvis", "");
      jarvisBubble.textContent += msg.text;
      $("log").scrollTop = $("log").scrollHeight;
      break;
    case "sentence":
      if (msg.turn_id !== null && msg.turn_id <= dropUpTo) break;
      // Phrase hors réponse streamée (accusé de réveil, message d'erreur) : on l'affiche.
      if (!jarvisBubble) jarvisBubble = ui.add("jarvis", msg.text);
      break;
    case "wake":
      followUpUntil = Date.now() + 8000;
      ui.hint("Je vous écoute…");
      break;
    case "ignored":
      break; // une phrase entendue sans « Jarvis » : on ne fait rien
    case "reply_end":
      jarvisBubble = null;
      if (!player.playing) afterReply();
      break;
    case "error":
      if (!msg.turn_id) ui.add("system", msg.message);
      break;
  }
}

function onAudio(meta, wav) {
  if (meta.turn_id !== null && meta.turn_id <= dropUpTo) return;
  if (!prefs.voice) return;
  ui.orb("speaking");
  player.play(wav);
}

player.onIdle = () => afterReply();

function afterReply() {
  ui.orb(recording ? "listening" : "");
  if (prefs.handsfree) {
    // Conversation naturelle : quelques secondes pour enchaîner sans redire « Jarvis ».
    followUpUntil = Date.now() + 6000;
    ui.hint("Je vous écoute… (ou dites « Jarvis »)");
  }
}

// Interrompt Jarvis localement et côté serveur.
function interrupt() {
  if (currentTurn) dropUpTo = Math.max(dropUpTo, currentTurn);
  player.stop();
  brain.send({ type: "cancel" });
}

// ---------- micro ----------
async function ensureMic() {
  if (micReady) return true;
  if (!window.isSecureContext) {
    ui.add("system", "Le micro exige HTTPS (ou localhost). Voir docs/SECURITY.md pour accéder à Jarvis en HTTPS.");
    return false;
  }
  try {
    audioCtx = audioCtx || new AudioContext();
    await audioCtx.resume();
    player.attach(audioCtx);
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    await audioCtx.audioWorklet.addModule("mic-worklet.js");
    const source = audioCtx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audioCtx, "mic-processor");
    node.port.onmessage = (ev) => onChunk(ev.data.pcm, ev.data.rms);
    source.connect(node);
    // Le nœud doit être relié à la sortie pour être exécuté sur certains navigateurs ; gain nul.
    const mute = audioCtx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(audioCtx.destination);
    micReady = true;
    return true;
  } catch (err) {
    ui.add("system", "Micro inaccessible : " + err.message);
    return false;
  }
}

function beginRecording(wake) {
  recording = { wake };
  brain.send({ type: "audio_start", sample_rate: 16000, encoding: "pcm_s16le", wake_word: wake });
  for (const chunk of preroll) brain.sendBytes(chunk);
  preroll = [];
  if (!wake) ui.orb("listening");
}

function endRecording() {
  if (!recording) return;
  recording = null;
  brain.send({ type: "audio_end" });
  ui.orb(player.playing ? "speaking" : "thinking");
}

function onChunk(pcm, rms) {
  const event = vad.push(rms);
  if (recording) {
    brain.sendBytes(pcm);
    if (event === "end") endRecording();
    return;
  }
  // Garde 300 ms avant le début de la parole pour ne pas couper le premier mot.
  preroll.push(pcm);
  if (preroll.length > 3) preroll.shift();
  if (prefs.handsfree && event === "start" && brain.ready) {
    const followUp = Date.now() < followUpUntil && !player.playing;
    beginRecording(!followUp);
  }
}

// ---------- événements ----------
$("setup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const token = $("token").value.trim();
  if (!token) return;
  store.set(TOKEN_KEY, token);
  $("token").value = "";
  start(token);
});

$("talk").addEventListener("click", async () => {
  if (!(await ensureMic())) return;
  if (recording) { endRecording(); return; }
  interrupt();
  vad.forceStart();
  beginRecording(false);
});

$("handsfree").addEventListener("change", async (e) => {
  prefs.handsfree = e.target.checked;
  savePrefs();
  if (prefs.handsfree && !(await ensureMic())) {
    prefs.handsfree = false;
    e.target.checked = false;
    savePrefs();
    return;
  }
  ui.hint(prefs.handsfree ? "J'écoute. Dis « Jarvis… »" : "Touche le cercle pour parler");
});

$("voice").addEventListener("change", (e) => {
  prefs.voice = e.target.checked;
  savePrefs();
  brain.send({ type: "config", audio_replies: prefs.voice });
  if (!prefs.voice) player.stop();
});

$("text-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text || !brain) return;
  if (prefs.voice && !audioCtx) {
    audioCtx = new AudioContext();
    player.attach(audioCtx);
  }
  if (audioCtx) await audioCtx.resume();
  interrupt();
  brain.send({ type: "text", text });
  $("text").value = "";
});

$("forget").addEventListener("click", () => {
  store.del(TOKEN_KEY);
  if (brain) brain.close();
  location.reload();
});

// Le mode mains libres a besoin d'un geste pour démarrer le micro (règle des navigateurs).
document.addEventListener("click", () => { if (prefs.handsfree) ensureMic(); }, { once: true });

const saved = store.get(TOKEN_KEY);
if (saved) start(saved); else setupScreen();
