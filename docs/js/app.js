// Browser UI: drives the simulation and renders mission control.

import { parseConsoleCommand } from "./groundElement.js";
import { AckStatus, Mode, Opcode } from "./messages.js";
import { toHex } from "./packets.js";
import { createSimulation } from "./link.js";

const COMMANDS = [
  ["noop", "NOOP"], ["mode safe", "Mode SAFE"], ["mode nominal", "Mode NOMINAL"], ["mode science", "Mode SCIENCE"],
  ["heater on", "Heater ON"], ["heater off", "Heater OFF"], ["reset", "Reset counters"],
];
// Same scripted pass as run_demo.py: [real-time offset in seconds, opcode, argument]
const DEMO_PLAN = [
  [1, Opcode.NOOP, 0], [2, Opcode.SET_MODE, Mode.SCIENCE], [6, Opcode.HEATER_ON, 0],
  [10, Opcode.SET_MODE, 7], [12, Opcode.HEATER_OFF, 0], [14, Opcode.SET_MODE, Mode.SCIENCE],
];
const TICK_MS = 100;
const MAX_POINTS = 200;

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

let lastPacketHex = "";
let sim = createSimulation({ onPacket: tapPacket, onEvent: (e) => addLi("alarms", e, "bad") });
let history, track, lastTime, demoTimers = [];
resetHistory();

function resetHistory() {
  history = { soc: [], temp: [] };
  track = [];
  lastTime = null;
}

function tapPacket(bytes, direction) {
  // Keep the most recent packet of each kind visible so the wire format can be inspected.
  const arrow = direction === "uplink" ? "UP  " : "DOWN";
  lastPacketHex = `${arrow} ${toHex(bytes).replace(/(.{2})/g, "$1 ").trim()}`;
  if (direction === "uplink") $("uplink-hex").textContent = lastPacketHex;
  else if (bytes[1] === 0x64) $("hk-hex").textContent = lastPacketHex;
  else $("ack-hex").textContent = lastPacketHex;
}

// ---- rendering ----
function tile(k, v, alarm) {
  return `<div class="tile${alarm ? " alarm" : ""}"><div class="k">${k}</div><div class="v">${v}</div></div>`;
}
function addLi(listId, text, cls) {
  const li = document.createElement("li");
  li.textContent = text;
  if (cls) li.className = cls;
  $(listId).prepend(li);
}
function outOfLimits(param, value) {
  const l = sim.ground.limits.find((x) => x.parameter === param);
  return l && (value < l.low || value > l.high);
}

function render() {
  const { ground } = sim;
  const tm = ground.latest;
  const s = ground.stats;
  $("link").className = tm ? "up" : "down";
  $("link").textContent = tm ? "link up" : "no link";
  $("stats").innerHTML = tile("Telemetry frames", s.telemetryReceived) + tile("Packets lost", s.packetsLost)
    + tile("Corrupt packets", s.packetsCorrupt);
  $("alarm-count").textContent = s.alarms.length;
  if (!tm) return;
  $("tiles").innerHTML =
    tile("Mission time", tm.missionTimeS.toFixed(0) + " s") + tile("Mode", Mode.nameOf(tm.mode))
    + tile("Battery", tm.batterySocPct.toFixed(1) + " %", outOfLimits("batterySocPct", tm.batterySocPct))
    + tile("Bus voltage", tm.batteryVoltageV.toFixed(2) + " V")
    + tile("Temperature", tm.temperatureC.toFixed(1) + " &deg;C", outOfLimits("temperatureC", tm.temperatureC))
    + tile("Heater", tm.heaterOn ? "ON" : "OFF") + tile("Illumination", tm.sunlit ? "Sunlit" : "Eclipse")
    + tile("Cmds ok / rejected", tm.commandsAccepted + " / " + tm.commandsRejected);
  if (tm.missionTimeS !== lastTime) {
    lastTime = tm.missionTimeS;
    history.soc.push(tm.batterySocPct);
    history.temp.push(tm.temperatureC);
    track.push([tm.latitudeDeg, tm.longitudeDeg]);
    for (const a of [history.soc, history.temp, track]) if (a.length > MAX_POINTS) a.shift();
  }
  const limit = (p) => ground.limits.find((l) => l.parameter === p);
  drawLine("soc", history.soc, limit("batterySocPct"));
  drawLine("temp", history.temp, limit("temperatureC"));
  drawMap();
}

function setup(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, r.width, r.height);
  return [ctx, r.width, r.height];
}

function drawLine(id, ys, limit) {
  const [ctx, w, h] = setup($(id));
  if (ys.length < 2) return;
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (limit) { lo = Math.min(lo, limit.low); hi = Math.max(hi, limit.high); }
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const y = (v) => h - ((v - lo) / (hi - lo)) * h;
  if (limit) {
    ctx.setLineDash([4, 4]); ctx.strokeStyle = css("--bad"); ctx.lineWidth = 1;
    for (const v of [limit.low, limit.high]) { ctx.beginPath(); ctx.moveTo(0, y(v)); ctx.lineTo(w, y(v)); ctx.stroke(); }
  }
  ctx.setLineDash([]); ctx.strokeStyle = css("--accent"); ctx.lineWidth = 2; ctx.beginPath();
  ys.forEach((v, i) => { const x = (i / (MAX_POINTS - 1)) * w; i ? ctx.lineTo(x, y(v)) : ctx.moveTo(x, y(v)); });
  ctx.stroke();
}

function drawMap() {
  const [ctx, w, h] = setup($("map"));
  ctx.strokeStyle = css("--line"); ctx.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 30) { const x = (lon + 180) / 360 * w; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for (let lat = -90; lat <= 90; lat += 30) { const y = (90 - lat) / 180 * h; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  const pt = ([lat, lon]) => [(lon + 180) / 360 * w, (90 - lat) / 180 * h];
  ctx.strokeStyle = css("--accent"); ctx.lineWidth = 2;
  let prev = null;
  for (const p of track) {
    const [x, y] = pt(p);
    if (prev && Math.abs(x - prev[0]) < w / 2) { ctx.beginPath(); ctx.moveTo(...prev); ctx.lineTo(x, y); ctx.stroke(); }
    prev = [x, y];
  }
  if (prev) { ctx.fillStyle = css("--warn"); ctx.beginPath(); ctx.arc(prev[0], prev[1], 5, 0, 7); ctx.fill(); }
}

// ---- commanding ----
function logAck(label, ack) {
  const ok = ack && ack.status === AckStatus.ACCEPTED;
  const text = ack ? `${AckStatus.nameOf(ack.status)} ${ack.reason}`.trim() : "NO ACK (lost or timed out)";
  addLi("cmdlog", `${label} -> ${text}`, ok ? "good" : "bad");
}

async function send(line, btn) {
  const parsed = parseConsoleCommand(line);
  if (!parsed) return addLi("cmdlog", `${line} -> unknown command`, "bad");
  btn.disabled = true;
  try {
    logAck(line, await sim.ground.command(...parsed));
  } finally {
    btn.disabled = false;
  }
}

function runDemo(btn) {
  btn.disabled = true;
  addLi("cmdlog", "-- scripted pass started --", "good");
  demoTimers = DEMO_PLAN.map(([sec, opcode, arg]) => setTimeout(async () => {
    logAck(`${Opcode.nameOf(opcode)}(${arg})`, await sim.ground.command(opcode, arg));
  }, sec * 1000));
  demoTimers.push(setTimeout(() => { btn.disabled = false; addLi("cmdlog", "-- scripted pass complete --", "good"); }, 16000));
}

function restart() {
  demoTimers.forEach(clearTimeout);
  demoTimers = [];
  $("demo").disabled = false;
  sim = createSimulation({
    timeScale: Number($("timescale").value),
    downlinkLoss: Number($("loss").value) / 100,
    onPacket: tapPacket,
    onEvent: (e) => addLi("alarms", e, "bad"),
  });
  resetHistory();
  for (const id of ["cmdlog", "alarms"]) $(id).innerHTML = "";
}

// ---- wiring ----
COMMANDS.forEach(([cmd, label], i) => {
  const b = document.createElement("button");
  b.textContent = label;
  if (i === 0 || cmd === "reset") b.className = "secondary";
  b.onclick = () => send(cmd, b);
  $("buttons").appendChild(b);
});
$("demo").onclick = () => runDemo($("demo"));
$("restart").onclick = restart;
$("timescale").oninput = () => { sim.space.timeScale = Number($("timescale").value); $("timescale-v").textContent = $("timescale").value + "x"; };
$("loss").oninput = () => { sim.space.downlinkLoss = Number($("loss").value) / 100; $("loss-v").textContent = $("loss").value + "%"; };

// Alarms from limit checks are listed from stats so they survive re-renders.
let shownAlarms = 0;
let lastTick = performance.now();
setInterval(() => {
  const now = performance.now();
  sim.space.tick((now - lastTick) / 1000);
  lastTick = now;
  const alarms = sim.ground.stats.alarms;
  if (alarms.length < shownAlarms) shownAlarms = 0;
  alarms.slice(shownAlarms).forEach((a) => addLi("alarms", a, "bad"));
  shownAlarms = alarms.length;
  render();
}, TICK_MS);
