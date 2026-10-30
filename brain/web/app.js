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
  // Fiche affichée par Jarvis (courriels, rendez-vous…). Tout passe par textContent :
  // un courriel piégé ne peut pas injecter de HTML dans la page.
  card(card, kind = "") {
    if (!card) return;
    const box = document.createElement("article");
    box.className = "card " + kind + (card.layout === "gallery" ? " gallery" : "");
    const title = document.createElement("h2");
    title.textContent = card.title || "";
    box.appendChild(title);
    if (card.text) {
      const p = document.createElement("p");
      p.textContent = card.text;
      box.appendChild(p);
    }
    (card.items || []).forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "card-item";
      // Images : uniquement des data:URL fournies par le cerveau (la CSP bloque tout le reste).
      if (item.image && item.image.startsWith("data:image/")) {
        const img = document.createElement("img");
        img.src = item.image;
        img.alt = item.title || "";
        img.loading = "lazy";
        img.addEventListener("click", () => viewer.open(card, index));
        const num = document.createElement("span");
        num.className = "item-number";
        num.textContent = String(index + 1);
        row.append(img, num);
      }
      for (const [cls, value] of [["item-title", item.title], ["item-subtitle", item.subtitle], ["item-detail", item.detail]]) {
        if (!value) continue;
        const el = document.createElement("div");
        el.className = cls;
        el.textContent = value;
        row.appendChild(el);
      }
      box.appendChild(row);
    });
    if (!kind) screen.card = card;  // la fiche visée par « zoom sur la première »
    $("cards").prepend(box);
    while ($("cards").children.length > 5) $("cards").lastChild.remove();
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  },
  add(kind, text) {
    const div = document.createElement("div");
    div.className = "msg " + kind;
    div.textContent = text;
    $("log").appendChild(div);
    $("log").scrollTop = $("log").scrollHeight;
    return div;
  },
};

// ---------- plein écran : visionneuse pilotable à la voix ----------
const screen = {
  card: null,
  control(action, item) {
    if (action === "zoom" && this.card) viewer.open(this.card, (item || 1) - 1);
    else if (action === "next") viewer.step(1);
    else if (action === "previous") viewer.step(-1);
    else if (action === "close") viewer.close();
    else if (action === "clear") { viewer.close(); $("cards").replaceChildren(); this.card = null; }
  },
};

const viewer = {
  card: null,
  index: 0,
  open(card, index) {
    if (!card || !card.items || !card.items.length) return;
    this.card = card;
    this.index = Math.max(0, Math.min(index, card.items.length - 1));
    this.render();
    $("viewer").hidden = false;
    // Vrai plein écran si le navigateur l'autorise (il faut souvent un geste récent), sinon la fenêtre entière.
    const el = $("viewer");
    if (!document.fullscreenElement && el.requestFullscreen) el.requestFullscreen().catch(() => {});
  },
  render() {
    const item = this.card.items[this.index];
    const img = $("viewer-img");
    if (item.image && item.image.startsWith("data:image/")) { img.src = item.image; img.hidden = false; }
    else { img.removeAttribute("src"); img.hidden = true; }
    img.alt = item.title || "";
    $("viewer-title").textContent = `${this.index + 1}/${this.card.items.length} · ${item.title || ""}`;
    $("viewer-text").textContent = [item.subtitle, item.detail].filter(Boolean).join(" — ");
  },
  step(delta) {
    if (!this.card) { if (screen.card) this.open(screen.card, 0); return; }
    const n = this.card.items.length;
    this.index = (this.index + delta + n) % n;
    this.render();
  },
  close() {
    if ($("viewer").hidden) return;
    $("viewer").hidden = true;
    this.card = null;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  },
};

// ---------- tableau de bord sécurité (appareils admin) ----------
const security = {
  el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  },
  when(ts) {
    return ts ? new Date(ts * 1000).toLocaleString("fr-CA", { dateStyle: "short", timeStyle: "short" }) : "jamais";
  },
  render(r) {
    const root = $("security");
    root.replaceChildren();
    root.hidden = false;
    const head = this.el("div", undefined, "security-head");
    head.append(this.el("h2", "🛡️ Sécurité"));
    const close = this.el("button", "Fermer");
    close.type = "button";
    close.addEventListener("click", () => { root.hidden = true; });
    head.append(close);
    root.append(head);

    const tips = this.el("ul", undefined, "security-tips");
    for (const t of r.conseils) tips.append(this.el("li", t));
    root.append(tips);

    root.append(this.el("h3", "Appareils"));
    for (const d of r.appareils) {
      const row = this.el("div", undefined, "security-row");
      const info = this.el("div");
      info.append(this.el("strong", `${d.connecte ? "🟢" : "⚪"} ${d.nom}${d.admin ? " (admin)" : ""}`));
      info.append(this.el("div", `Dernière connexion : ${this.when(d.derniere_connexion)}${d.ip ? " · " + d.ip : ""}`
        + (d.notifications_push ? " · notifications" : ""), "muted"));
      row.append(info);
      const revoke = this.el("button", "Révoquer", "danger");
      revoke.type = "button";
      revoke.addEventListener("click", () => {
        if (confirm(`Révoquer « ${d.nom} » ? Il sera déconnecté immédiatement et devra recevoir un nouveau token.`)) {
          brain.send({ type: "revoke_device", device: d.nom });
        }
      });
      if (d.nom !== this.me) row.append(revoke);
      root.append(row);
    }

    root.append(this.el("h3", `Connexions refusées (24 h) : ${r.echecs_24h.total}`));
    for (const [ip, n] of r.echecs_24h.par_ip) root.append(this.el("div", `${ip} : ${n} tentative(s)`, "muted"));

    root.append(this.el("h3", "Fuites de données (Have I Been Pwned)"));
    if (!r.fuites.disponible) root.append(this.el("div", r.fuites.raison, "muted"));
    else for (const [address, breaches] of Object.entries(r.fuites.adresses)) {
      const text = Array.isArray(breaches)
        ? (breaches.length ? `${breaches.length} fuite(s) : ` + breaches.map((b) => `${b.nom} (${b.date})`).join(", ") : "aucune fuite connue ✓")
        : breaches.erreur;
      root.append(this.el("div", `${address} — ${text}`));
    }

    root.append(this.el("h3", "Activité récente de Jarvis"));
    if (!r.activite.length) root.append(this.el("div", "Aucune action ces 7 derniers jours.", "muted"));
    for (const a of r.activite.slice(0, 30)) {
      const what = a.type === "tool" ? `${a.tool} (${a.outcome})` : a.type === "notify" ? `notification : ${a.text}` : `révocation de ${a.target}`;
      root.append(this.el("div", `${this.when(a.ts)} · ${a.device || "jarvis"} · ${what}`, "muted"));
    }
    root.scrollIntoView({ behavior: "smooth" });
  },
};

// ---------- caméra : « Jarvis, regarde ça » ----------
const camera = {
  stream: null,
  facing: "environment",  // caméra arrière du téléphone par défaut
  resolve: null,
  async start() {
    this.stop();
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: this.facing, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
    });
    $("camera-video").srcObject = this.stream;
    $("camera").hidden = false;
  },
  stop() {
    if (this.stream) for (const track of this.stream.getTracks()) track.stop();
    this.stream = null;
    $("camera-video").srcObject = null;
    $("camera").hidden = true;
    $("camera-count").hidden = true;
  },
  // Photo JPEG, réduite à 1280 px de large au maximum (rapide à envoyer, suffisant pour la vision).
  async snapshot() {
    const video = $("camera-video");
    const scale = Math.min(1, 1280 / (video.videoWidth || 1280));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round((video.videoWidth || 1280) * scale);
    canvas.height = Math.round((video.videoHeight || 720) * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
    this.thumbnail(dataUrl);
    return dataUrl.split(",")[1];
  },
  thumbnail(dataUrl) {
    const div = document.createElement("div");
    div.className = "msg user photo";
    const img = document.createElement("img");
    img.src = dataUrl;
    img.alt = "Photo envoyée à Jarvis";
    div.appendChild(img);
    $("log").appendChild(div);
    $("log").scrollTop = $("log").scrollHeight;
  },
  // Bouton 📷 : l'utilisateur cadre et appuie.
  async manual() {
    try { await this.start(); } catch (e) { ui.add("system", "Caméra inaccessible : " + e.message); return; }
    this.resolve = async () => {
      const data = await this.snapshot();
      this.stop();
      const question = $("text").value.trim() || "Qu'est-ce que tu vois sur cette photo ?";
      $("text").value = "";
      interrupt();
      brain.send({ type: "photo", data, question });
    };
  },
  // Demande du cerveau : compte à rebours 3-2-1 puis photo automatique.
  async capture(requestId) {
    const fail = (error) => brain.send({ type: "camera_result", request_id: requestId, error });
    try { await this.start(); } catch (e) { fail("Caméra inaccessible : " + e.message); return; }
    this.resolve = null;
    let n = 3;
    $("camera-count").hidden = false;
    $("camera-count").textContent = n;
    const timer = setInterval(async () => {
      if ($("camera").hidden) { clearInterval(timer); fail("Photo annulée."); return; }
      n -= 1;
      if (n > 0) { $("camera-count").textContent = n; return; }
      clearInterval(timer);
      const data = await this.snapshot();
      this.stop();
      brain.send({ type: "camera_result", request_id: requestId, data });
    }, 1000);
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
      ws.send(JSON.stringify({
        type: "auth", token: this.token, client: "web/0.3", audio_replies: prefs.voice, display: true,
        camera: Boolean(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      }));
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
      push.setup(msg.push_key);
      $("security-btn").hidden = !msg.admin;
      security.me = msg.device;
      brain.send({ type: "visibility", visible: !document.hidden });
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
    case "display":
      viewer.close();
      ui.card(msg.card);
      break;
    case "screen":
      screen.control(msg.action, msg.item);
      break;
    case "camera":
      camera.capture(msg.request_id);
      break;
    case "notify":
      if (msg.card) ui.card(msg.card, "notify"); else ui.add("system", msg.text);
      break;  // page cachée : le cerveau envoie une notification push à la place
    case "security_report":
      security.render(msg.report);
      break;
    case "revoked":
      ui.add("system", `Appareil « ${msg.device} » révoqué et déconnecté.`);
      brain.send({ type: "security_report" });
      break;
    case "push_ok":
      ui.add("system", "Notifications activées sur cet appareil.");
      $("push").textContent = "Notifications activées ✓";
      break;
    case "status":
      ui.hint(msg.text);
      break;
    case "ignored":
      break; // une phrase entendue sans « Jarvis » : on ne fait rien
    case "reply_end":
      jarvisBubble = null;
      ui.hint(prefs.handsfree ? "J'écoute. Dis « Jarvis… »" : "Touche le cercle pour parler");
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

async function ask(text) {
  if (!text || !brain) return;
  if (prefs.voice && !audioCtx) {
    audioCtx = new AudioContext();
    player.attach(audioCtx);
  }
  if (audioCtx) await audioCtx.resume();
  interrupt();
  brain.send({ type: "text", text });
}

for (const btn of document.querySelectorAll(".quick button")) {
  btn.addEventListener("click", () => ask(btn.dataset.ask));
}

$("text-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("text").value.trim());
  $("text").value = "";
});

$("camera-btn").addEventListener("click", () => camera.manual());
$("security-btn").addEventListener("click", () => brain.send({ type: "security_report" }));
$("camera-cancel").addEventListener("click", () => camera.stop());
$("camera-shoot").addEventListener("click", () => { if (camera.resolve) camera.resolve(); });
$("camera-flip").addEventListener("click", async () => {
  camera.facing = camera.facing === "environment" ? "user" : "environment";
  const resolve = camera.resolve;
  try { await camera.start(); camera.resolve = resolve; } catch { /* une seule caméra */ }
});

$("viewer-close").addEventListener("click", () => viewer.close());
$("viewer-prev").addEventListener("click", () => viewer.step(-1));
$("viewer-next").addEventListener("click", () => viewer.step(1));
document.addEventListener("keydown", (e) => {
  if ($("viewer").hidden) return;
  if (e.key === "Escape") viewer.close();
  else if (e.key === "ArrowRight") viewer.step(1);
  else if (e.key === "ArrowLeft") viewer.step(-1);
});
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && !$("viewer").hidden) viewer.close();
});

$("forget").addEventListener("click", () => {
  store.del(TOKEN_KEY);
  if (brain) brain.close();
  location.reload();
});

// Le mode mains libres a besoin d'un geste pour démarrer le micro (règle des navigateurs).
document.addEventListener("click", () => { if (prefs.handsfree) ensureMic(); }, { once: true });

// Page affichée ou cachée : quand elle est cachée, le cerveau envoie une notification push.
document.addEventListener("visibilitychange", () => {
  if (brain) brain.send({ type: "visibility", visible: !document.hidden });
});

// ---------- notifications push (téléphone, page fermée) ----------
function b64urlToBytes(text) {
  const pad = "=".repeat((4 - (text.length % 4)) % 4);
  const raw = atob((text + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const push = {
  key: null,
  supported: () => "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext,
  async setup(key) {
    this.key = key;
    const btn = $("push");
    if (!key || !this.supported()) { btn.hidden = true; return; }
    btn.hidden = false;
    const reg = await navigator.serviceWorker.register("sw.js");
    const existing = await reg.pushManager.getSubscription();
    if (existing && Notification.permission === "granted") {
      // Renvoie l'abonnement à chaque connexion (le serveur a pu être réinstallé).
      brain.send({ type: "push_subscribe", subscription: existing.toJSON() });
    }
  },
  async enable() {
    if (!this.key || !this.supported()) return;
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      ui.add("system", "Notifications refusées. Tu peux les autoriser dans les réglages du navigateur.");
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(this.key) });
    }
    brain.send({ type: "push_subscribe", subscription: sub.toJSON() });
  },
};

$("push").addEventListener("click", () => push.enable().catch((e) => ui.add("system", "Notifications impossibles : " + e.message)));

// Appairage automatique : le script d'installation ouvre /#token=jv_…
// Le fragment (#) n'est jamais envoyé au serveur ; on l'efface aussitôt de l'adresse.
if (location.hash.startsWith("#token=")) {
  const fromLink = decodeURIComponent(location.hash.slice(7));
  history.replaceState(null, "", location.pathname);
  if (/^jv_[A-Za-z0-9_-]{20,120}$/.test(fromLink)) store.set(TOKEN_KEY, fromLink);
}

const saved = store.get(TOKEN_KEY);
if (saved) start(saved); else setupScreen();
