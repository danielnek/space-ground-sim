// Simulated ground element: a ground station with mission control functions.
// Port of spacesim/ground_element.py.
//
// Responsibilities:
//   * uplink telecommands to the space element and track their acknowledgements
//   * receive and decode telemetry
//   * check telemetry against limits and raise alarms
//   * detect lost downlink packets from gaps in sequence counters

import { Mode, Opcode, decodeAck, decodeHousekeeping, encodeTelecommand } from "./messages.js";
import { Apid, MAX_SEQUENCE, PacketError, PacketType, decodePacket, encodePacket } from "./packets.js";

export const DEFAULT_LIMITS = Object.freeze([
  Object.freeze({ parameter: "batterySocPct", label: "Battery", low: 30.0, high: 100.0 }),
  Object.freeze({ parameter: "temperatureC", label: "Temperature", low: -10.0, high: 40.0 }),
]);

export class GroundElement {
  constructor({ sendUplink, limits = DEFAULT_LIMITS, onAlarm = () => {} }) {
    this.sendUplink = sendUplink;
    this.limits = limits;
    this.onAlarm = onAlarm;
    this.stats = { telemetryReceived: 0, packetsLost: 0, packetsCorrupt: 0, alarms: [] };
    this.latest = null;
    this.acks = new Map();
    this._uplinkSeq = 0;
    this._lastSeq = new Map();
    this._inAlarm = new Set();
    this._waiters = new Map();
  }

  // ---- uplink ----
  /** Uplink a telecommand and return its sequence count. */
  sendCommand(opcode, argument = 0.0) {
    const seq = this._uplinkSeq;
    this._uplinkSeq = (seq + 1) & MAX_SEQUENCE;
    const payload = encodeTelecommand({ opcode, argument });
    this.sendUplink(encodePacket({ packetType: PacketType.TELECOMMAND, apid: Apid.COMMAND, sequenceCount: seq, payload }));
    return seq;
  }

  /** Send a command and resolve with its ack, or null if none arrives within timeoutMs. */
  command(opcode, argument = 0.0, timeoutMs = 2000) {
    const seq = this.sendCommand(opcode, argument);
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this._waiters.delete(seq);
        resolve(this.acks.get(seq) ?? null);
      }, timeoutMs);
      this._waiters.set(seq, (ack) => {
        clearTimeout(timer);
        resolve(ack);
      });
    });
  }

  // ---- downlink ----
  handlePacket(data) {
    let packet;
    try {
      packet = decodePacket(data);
    } catch (err) {
      if (!(err instanceof PacketError)) throw err;
      this.stats.packetsCorrupt += 1;
      return;
    }
    if (packet.packetType !== PacketType.TELEMETRY) return;
    this.#trackSequence(packet);
    try {
      if (packet.apid === Apid.HOUSEKEEPING) this.#onHousekeeping(decodeHousekeeping(packet.payload));
      else if (packet.apid === Apid.COMMAND_ACK) this.#onAck(decodeAck(packet.payload));
    } catch (err) {
      if (!(err instanceof PacketError)) throw err;
      this.stats.packetsCorrupt += 1;
    }
  }

  #trackSequence(packet) {
    const last = this._lastSeq.get(packet.apid);
    if (last !== undefined) {
      const gap = (packet.sequenceCount - last - 1) & MAX_SEQUENCE;
      if (gap > 0 && gap < MAX_SEQUENCE / 2) this.stats.packetsLost += gap;
    }
    this._lastSeq.set(packet.apid, packet.sequenceCount);
  }

  #onHousekeeping(tm) {
    this.stats.telemetryReceived += 1;
    this.latest = tm;
    this.checkLimits(tm);
  }

  #onAck(ack) {
    this.acks.set(ack.commandSequence, ack);
    this._waiters.get(ack.commandSequence)?.(ack);
    this._waiters.delete(ack.commandSequence);
  }

  /** Raise an alarm when a parameter leaves its limits, once per excursion. */
  checkLimits(tm) {
    const newAlarms = [];
    for (const limit of this.limits) {
      const value = tm[limit.parameter];
      const out = !(limit.low <= value && value <= limit.high);
      if (out && !this._inAlarm.has(limit.parameter)) {
        this._inAlarm.add(limit.parameter);
        newAlarms.push(
          `t=${tm.missionTimeS.toFixed(0)}s ALARM ${limit.parameter}=${value.toFixed(1)} outside [${limit.low}, ${limit.high}]`,
        );
      } else if (!out && this._inAlarm.has(limit.parameter)) {
        this._inAlarm.delete(limit.parameter);
      }
    }
    this.stats.alarms.push(...newAlarms);
    newAlarms.forEach((a) => this.onAlarm(a));
    return newAlarms;
  }
}

/** Parse a console-style command such as "mode science" into [opcode, argument], or null. */
export function parseConsoleCommand(line) {
  const words = line.toLowerCase().split(/\s+/).filter(Boolean);
  const text = words.join(" ");
  if (text === "noop") return [Opcode.NOOP, 0.0];
  if (text === "reset") return [Opcode.RESET_COUNTERS, 0.0];
  if (words.length === 2 && words[0] === "heater" && (words[1] === "on" || words[1] === "off")) {
    return [words[1] === "on" ? Opcode.HEATER_ON : Opcode.HEATER_OFF, 0.0];
  }
  if (words.length === 2 && words[0] === "mode" && words[1].toUpperCase() in Mode && Mode.isValid(Mode[words[1].toUpperCase()])) {
    return [Opcode.SET_MODE, Mode[words[1].toUpperCase()]];
  }
  return null;
}
