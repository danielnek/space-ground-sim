// Simulated space element: a small satellite in low Earth orbit. Port of spacesim/space_element.py.
//
// Two layers, as in the Python version:
//   SpacecraftModel - pure simulation of orbit, power and thermal state (no networking)
//   SpaceElement    - node that receives telecommand packets and downlinks telemetry packets

import { AckStatus, Mode, Opcode, decodeTelecommand, encodeAck, encodeHousekeeping } from "./messages.js";
import { Apid, PacketError, PacketType, decodePacket, encodePacket } from "./packets.js";

export const ORBIT_PERIOD_S = 5400.0; // ~90 minute LEO orbit
export const INCLINATION_DEG = 51.6;
export const EARTH_ROTATION_DEG_PER_S = 360.0 / 86164.0;
export const BATTERY_CAPACITY_WH = 80.0;
export const SOLAR_POWER_W = 40.0;
export const HEATER_POWER_W = 6.0;
export const MODE_LOAD_W = { [Mode.SAFE]: 8.0, [Mode.NOMINAL]: 15.0, [Mode.SCIENCE]: 28.0 };
export const SAFE_MODE_SOC_PCT = 20.0; // autonomous fallback threshold
export const SCIENCE_MIN_SOC_PCT = 40.0;

const rad = (deg) => (deg * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
/** Python-style modulo: result takes the sign of the divisor. */
const mod = (a, n) => ((a % n) + n) % n;

export class SpacecraftModel {
  constructor() {
    this.missionTimeS = 0.0;
    this.batterySocPct = 80.0;
    this.temperatureC = 15.0;
    this.mode = Mode.NOMINAL;
    this.heaterOn = false;
    this.commandsAccepted = 0;
    this.commandsRejected = 0;
    this.events = [];
  }

  // ---- orbit ----
  get orbitPhaseRad() {
    return (2 * Math.PI * mod(this.missionTimeS, ORBIT_PERIOD_S)) / ORBIT_PERIOD_S;
  }

  get sunlit() {
    // Simple eclipse model: roughly a third of each orbit is in shadow.
    return Math.cos(this.orbitPhaseRad) > -0.45;
  }

  groundTrack() {
    const theta = this.orbitPhaseRad;
    const inc = rad(INCLINATION_DEG);
    const lat = deg(Math.asin(Math.sin(inc) * Math.sin(theta)));
    let lon = deg(Math.atan2(Math.cos(inc) * Math.sin(theta), Math.cos(theta)));
    lon -= EARTH_ROTATION_DEG_PER_S * this.missionTimeS;
    lon = mod(lon + 180.0, 360.0) - 180.0;
    return [lat, lon];
  }

  // ---- power and thermal ----
  get batteryVoltageV() {
    return 6.0 + (2.4 * this.batterySocPct) / 100.0; // 2S Li-ion pack
  }

  loadW() {
    return MODE_LOAD_W[this.mode] + (this.heaterOn ? HEATER_POWER_W : 0.0);
  }

  /** Advance the simulation by dtS seconds of mission time. */
  step(dtS) {
    this.missionTimeS += dtS;

    const generated = this.sunlit ? SOLAR_POWER_W : 0.0;
    const netWh = ((generated - this.loadW()) * dtS) / 3600.0;
    this.batterySocPct += (100.0 * netWh) / BATTERY_CAPACITY_WH;
    this.batterySocPct = Math.min(100.0, Math.max(0.0, this.batterySocPct));

    const targetC = (this.sunlit ? 20.0 : -15.0) + (this.heaterOn ? 18.0 : 0.0);
    this.temperatureC += (targetC - this.temperatureC) * Math.min(1.0, dtS / 600.0);

    if (this.batterySocPct < SAFE_MODE_SOC_PCT && this.mode !== Mode.SAFE) {
      this.mode = Mode.SAFE;
      this.events.push(`t=${this.missionTimeS.toFixed(0)}s autonomous SAFE mode (low battery)`);
    }
  }

  // ---- commanding ----
  /** @returns {[number, string]} ack status and reason */
  execute(command) {
    const [status, reason] = this.#apply(command);
    if (command.opcode === Opcode.RESET_COUNTERS && status === AckStatus.ACCEPTED) {
      return [status, reason]; // the reset itself is not counted
    }
    if (status === AckStatus.ACCEPTED) this.commandsAccepted += 1;
    else this.commandsRejected += 1;
    return [status, reason];
  }

  #apply({ opcode, argument }) {
    switch (opcode) {
      case Opcode.NOOP:
        return [AckStatus.ACCEPTED, ""];
      case Opcode.SET_MODE: {
        const requested = Math.trunc(argument);
        if (!Mode.isValid(requested)) return [AckStatus.REJECTED, `unknown mode ${argument}`];
        if (requested === Mode.SCIENCE && this.batterySocPct < SCIENCE_MIN_SOC_PCT) {
          return [AckStatus.REJECTED, "battery too low for SCIENCE"];
        }
        this.mode = requested;
        return [AckStatus.ACCEPTED, ""];
      }
      case Opcode.HEATER_ON:
        this.heaterOn = true;
        return [AckStatus.ACCEPTED, ""];
      case Opcode.HEATER_OFF:
        this.heaterOn = false;
        return [AckStatus.ACCEPTED, ""];
      case Opcode.RESET_COUNTERS:
        this.commandsAccepted = this.commandsRejected = 0;
        return [AckStatus.ACCEPTED, ""];
      default:
        return [AckStatus.REJECTED, `unsupported opcode ${opcode}`];
    }
  }

  housekeeping() {
    const [latitudeDeg, longitudeDeg] = this.groundTrack();
    return {
      missionTimeS: this.missionTimeS,
      batterySocPct: this.batterySocPct,
      batteryVoltageV: this.batteryVoltageV,
      temperatureC: this.temperatureC,
      latitudeDeg,
      longitudeDeg,
      sunlit: this.sunlit,
      heaterOn: this.heaterOn,
      mode: this.mode,
      commandsAccepted: this.commandsAccepted,
      commandsRejected: this.commandsRejected,
    };
  }
}

/** Small seeded PRNG (mulberry32) so packet loss is reproducible in tests. */
export function makeRng(seed) {
  if (seed === undefined) return Math.random;
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Node that hosts the spacecraft model. It receives telecommand packets through receive()
 * and downlinks telemetry and acknowledgements by calling sendDownlink(bytes).
 */
export class SpaceElement {
  constructor({ sendDownlink, timeScale = 60.0, tickS = 0.1, downlinkLoss = 0.0, seed, onEvent = () => {} }) {
    this.model = new SpacecraftModel();
    this.sendDownlink = sendDownlink;
    this.timeScale = timeScale;
    this.tickS = tickS;
    this.downlinkLoss = downlinkLoss;
    this.onEvent = onEvent;
    this._rng = makeRng(seed);
    this._sequence = { [Apid.HOUSEKEEPING]: 0, [Apid.COMMAND_ACK]: 0 };
    this._sinceHk = tickS; // downlink housekeeping on the first tick
  }

  /** Advance by dtRealS real seconds; downlinks housekeeping every tickS. */
  tick(dtRealS) {
    this.model.step(dtRealS * this.timeScale);
    while (this.model.events.length) this.onEvent(this.model.events.shift());
    this._sinceHk += dtRealS;
    if (this._sinceHk >= this.tickS) {
      this._sinceHk = 0;
      this.#downlink(Apid.HOUSEKEEPING, encodeHousekeeping(this.model.housekeeping()));
    }
  }

  /** Handle an uplinked packet (raw bytes). */
  receive(data) {
    let packet, command;
    try {
      packet = decodePacket(data);
      if (packet.packetType !== PacketType.TELECOMMAND || packet.apid !== Apid.COMMAND) {
        throw new PacketError("not a telecommand");
      }
      command = decodeTelecommand(packet.payload);
    } catch (err) {
      if (!(err instanceof PacketError)) throw err;
      this.onEvent(`dropped uplink packet: ${err.message}`);
      this.model.commandsRejected += 1;
      return;
    }
    const [status, reason] = this.model.execute(command);
    this.#downlink(Apid.COMMAND_ACK, encodeAck({ commandSequence: packet.sequenceCount, status, reason }));
  }

  #downlink(apid, payload) {
    const seq = this._sequence[apid];
    this._sequence[apid] = (seq + 1) & 0x3fff;
    if (this._rng() < this.downlinkLoss) return; // simulated RF packet loss
    this.sendDownlink(encodePacket({ packetType: PacketType.TELEMETRY, apid, sequenceCount: seq, payload }));
  }
}
