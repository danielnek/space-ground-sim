import assert from "node:assert/strict";
import { test } from "node:test";

import { GroundElement, parseConsoleCommand } from "../../docs/js/groundElement.js";
import { createSimulation } from "../../docs/js/link.js";
import { AckStatus, Mode, Opcode, encodeHousekeeping } from "../../docs/js/messages.js";
import { Apid, PacketType, encodePacket } from "../../docs/js/packets.js";
import {
  INCLINATION_DEG, ORBIT_PERIOD_S, SpaceElement, SpacecraftModel, makeRng,
} from "../../docs/js/spaceElement.js";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0)); // let queued microtasks deliver

// ---- SpacecraftModel ----
test("latitude bounded by inclination, longitude in [-180, 180)", () => {
  const model = new SpacecraftModel();
  for (let i = 0; i < 200; i++) {
    model.step(ORBIT_PERIOD_S / 200);
    const [lat, lon] = model.groundTrack();
    assert.ok(Math.abs(lat) <= INCLINATION_DEG + 1e-6);
    assert.ok(lon >= -180 && lon < 180);
  }
});

test("orbit has sunlit and eclipse phases", () => {
  const model = new SpacecraftModel();
  const states = new Set();
  for (let i = 0; i < 100; i++) {
    model.step(ORBIT_PERIOD_S / 100);
    states.add(model.sunlit);
  }
  assert.deepEqual([...states].sort(), [false, true]);
});

test("battery drains in eclipse", () => {
  const model = new SpacecraftModel();
  model.missionTimeS = ORBIT_PERIOD_S / 2;
  const before = model.batterySocPct;
  model.step(60);
  assert.ok(model.batterySocPct < before);
});

test("autonomous SAFE mode on low battery", () => {
  const model = new SpacecraftModel();
  Object.assign(model, { batterySocPct: 21.0, mode: Mode.SCIENCE, missionTimeS: ORBIT_PERIOD_S / 2 });
  model.step(300);
  assert.equal(model.mode, Mode.SAFE);
  assert.ok(model.events.length > 0);
});

test("heater warms spacecraft", () => {
  const cold = new SpacecraftModel();
  const warm = new SpacecraftModel();
  warm.heaterOn = true;
  cold.step(600);
  warm.step(600);
  assert.ok(warm.temperatureC > cold.temperatureC);
});

test("SET_MODE accepted and counted", () => {
  const model = new SpacecraftModel();
  const [status] = model.execute({ opcode: Opcode.SET_MODE, argument: Mode.SAFE });
  assert.equal(status, AckStatus.ACCEPTED);
  assert.equal(model.mode, Mode.SAFE);
  assert.equal(model.commandsAccepted, 1);
});

test("SCIENCE rejected when battery low", () => {
  const model = new SpacecraftModel();
  model.batterySocPct = 30.0;
  const [status, reason] = model.execute({ opcode: Opcode.SET_MODE, argument: Mode.SCIENCE });
  assert.equal(status, AckStatus.REJECTED);
  assert.match(reason, /battery/);
  assert.equal(model.commandsRejected, 1);
});

test("invalid mode rejected with reason", () => {
  const [status, reason] = new SpacecraftModel().execute({ opcode: Opcode.SET_MODE, argument: 42 });
  assert.equal(status, AckStatus.REJECTED);
  assert.equal(reason, "unknown mode 42");
});

test("RESET_COUNTERS clears counters", () => {
  const model = new SpacecraftModel();
  model.commandsAccepted = 5;
  model.commandsRejected = 2;
  model.execute({ opcode: Opcode.RESET_COUNTERS, argument: 0 });
  assert.deepEqual([model.commandsAccepted, model.commandsRejected], [0, 0]);
});

// ---- GroundElement logic ----
const hk = (temp) => ({
  missionTimeS: 0, batterySocPct: 80, batteryVoltageV: 7.9, temperatureC: temp, latitudeDeg: 0, longitudeDeg: 0,
  sunlit: true, heaterOn: false, mode: Mode.NOMINAL, commandsAccepted: 0, commandsRejected: 0,
});
const limits = [{ parameter: "temperatureC", low: -10, high: 40 }];

test("alarm raised once per excursion", () => {
  const ground = new GroundElement({ sendUplink: () => {}, limits });
  assert.equal(ground.checkLimits(hk(-20)).length, 1);
  assert.deepEqual(ground.checkLimits(hk(-25)), []);
  ground.checkLimits(hk(10));
  assert.equal(ground.checkLimits(hk(50)).length, 1);
});

test("sequence gap counts lost packets", () => {
  const ground = new GroundElement({ sendUplink: () => {}, limits });
  for (const seq of [0, 1, 4, 5]) {
    ground.handlePacket(encodePacket({ packetType: PacketType.TELEMETRY, apid: Apid.HOUSEKEEPING, sequenceCount: seq, payload: encodeHousekeeping(hk(15)) }));
  }
  assert.equal(ground.stats.packetsLost, 2);
  assert.equal(ground.stats.telemetryReceived, 4);
});

test("corrupt packet counted", () => {
  const ground = new GroundElement({ sendUplink: () => {}, limits });
  ground.handlePacket(Uint8Array.from([0, 1, 0x67, 0x61]));
  assert.equal(ground.stats.packetsCorrupt, 1);
});

test("console parser", () => {
  assert.deepEqual(parseConsoleCommand("mode science"), [Opcode.SET_MODE, 2]);
  assert.deepEqual(parseConsoleCommand("HEATER  off"), [Opcode.HEATER_OFF, 0]);
  assert.equal(parseConsoleCommand("fly to mars"), null);
  assert.equal(parseConsoleCommand("mode nameOf"), null);
});

// ---- end to end over the in-memory link ----
test("telemetry flows to ground", async () => {
  const { ground, space } = createSimulation({ timeScale: 60, downlinkLoss: 0 });
  for (let i = 0; i < 3; i++) space.tick(0.1);
  await settle();
  assert.equal(ground.stats.telemetryReceived, 3);
  assert.ok(ground.latest);
});

test("command is executed and acknowledged", async () => {
  const { ground, space } = createSimulation({ downlinkLoss: 0 });
  const ack = await ground.command(Opcode.HEATER_ON);
  assert.equal(ack.status, AckStatus.ACCEPTED);
  space.tick(0.1);
  await settle();
  assert.equal(ground.latest.heaterOn, true);
});

test("invalid command is rejected", async () => {
  const { ground } = createSimulation({ downlinkLoss: 0 });
  const ack = await ground.command(Opcode.SET_MODE, 42);
  assert.equal(ack.status, AckStatus.REJECTED);
  assert.equal(ack.reason, "unknown mode 42");
});

test("command times out when the ack is lost", async () => {
  const { ground } = createSimulation({ downlinkLoss: 1 });
  assert.equal(await ground.command(Opcode.NOOP, 0, 20), null);
});

test("garbage uplink is dropped and counted as rejected", () => {
  const events = [];
  const space = new SpaceElement({ sendDownlink: () => {}, onEvent: (e) => events.push(e) });
  space.receive(Uint8Array.from([1, 2, 3]));
  assert.equal(space.model.commandsRejected, 1);
  assert.match(events[0], /dropped uplink packet/);
});

test("seeded packet loss is reproducible and roughly matches the loss rate", () => {
  const draw = () => { const r = makeRng(42); return Array.from({ length: 1000 }, r); };
  assert.deepEqual(draw(), draw());
  const lost = draw().filter((x) => x < 0.05).length;
  assert.ok(lost > 20 && lost < 90, `lost ${lost}`);
});
