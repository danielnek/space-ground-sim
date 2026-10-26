// Telemetry and telecommand payloads. Port of spacesim/messages.py: layouts are identical.
// All fields are big-endian; floats other than mission time are 32-bit on the wire.

import { PacketError } from "./packets.js";

function makeEnum(values) {
  const names = Object.fromEntries(Object.entries(values).map(([name, value]) => [value, name]));
  return Object.freeze({ ...values, nameOf: (value) => names[value], isValid: (value) => value in names });
}

export const Mode = makeEnum({ SAFE: 0, NOMINAL: 1, SCIENCE: 2 });
export const Opcode = makeEnum({ NOOP: 0, SET_MODE: 1, HEATER_ON: 2, HEATER_OFF: 3, RESET_COUNTERS: 4 });
export const AckStatus = makeEnum({ ACCEPTED: 0, REJECTED: 1 });

const TELECOMMAND_SIZE = 5; // >Bf
const ACK_HEADER_SIZE = 3; // >HB
const HOUSEKEEPING_SIZE = 35; // >dfffff??BHH

const viewOf = (bytes) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

export function encodeTelecommand({ opcode, argument = 0 }) {
  const out = new Uint8Array(TELECOMMAND_SIZE);
  const view = viewOf(out);
  view.setUint8(0, opcode);
  view.setFloat32(1, argument);
  return out;
}

export function decodeTelecommand(payload) {
  if (payload.length !== TELECOMMAND_SIZE) throw new PacketError(`bad telecommand payload: size ${payload.length}`);
  const view = viewOf(payload);
  const opcode = view.getUint8(0);
  if (!Opcode.isValid(opcode)) throw new PacketError(`bad telecommand payload: ${opcode} is not a valid Opcode`);
  return { opcode, argument: view.getFloat32(1) };
}

export function encodeAck({ commandSequence, status, reason = "" }) {
  const text = new TextEncoder().encode(reason);
  const out = new Uint8Array(ACK_HEADER_SIZE + text.length);
  const view = viewOf(out);
  view.setUint16(0, commandSequence);
  view.setUint8(2, status);
  out.set(text, ACK_HEADER_SIZE);
  return out;
}

export function decodeAck(payload) {
  if (payload.length < ACK_HEADER_SIZE) throw new PacketError(`bad ack payload: size ${payload.length}`);
  const view = viewOf(payload);
  const status = view.getUint8(2);
  if (!AckStatus.isValid(status)) throw new PacketError(`bad ack payload: ${status} is not a valid AckStatus`);
  let reason;
  try {
    reason = new TextDecoder("utf-8", { fatal: true }).decode(payload.subarray(ACK_HEADER_SIZE));
  } catch {
    throw new PacketError("bad ack payload: reason is not valid UTF-8");
  }
  return { commandSequence: view.getUint16(0), status, reason };
}

export function encodeHousekeeping(tm) {
  const out = new Uint8Array(HOUSEKEEPING_SIZE);
  const view = viewOf(out);
  view.setFloat64(0, tm.missionTimeS);
  view.setFloat32(8, tm.batterySocPct);
  view.setFloat32(12, tm.batteryVoltageV);
  view.setFloat32(16, tm.temperatureC);
  view.setFloat32(20, tm.latitudeDeg);
  view.setFloat32(24, tm.longitudeDeg);
  view.setUint8(28, tm.sunlit ? 1 : 0);
  view.setUint8(29, tm.heaterOn ? 1 : 0);
  view.setUint8(30, tm.mode);
  view.setUint16(31, tm.commandsAccepted & 0xffff);
  view.setUint16(33, tm.commandsRejected & 0xffff);
  return out;
}

export function decodeHousekeeping(payload) {
  if (payload.length !== HOUSEKEEPING_SIZE) throw new PacketError(`bad housekeeping payload: size ${payload.length}`);
  const view = viewOf(payload);
  const mode = view.getUint8(30);
  if (!Mode.isValid(mode)) throw new PacketError(`bad housekeeping payload: ${mode} is not a valid Mode`);
  return {
    missionTimeS: view.getFloat64(0),
    batterySocPct: view.getFloat32(8),
    batteryVoltageV: view.getFloat32(12),
    temperatureC: view.getFloat32(16),
    latitudeDeg: view.getFloat32(20),
    longitudeDeg: view.getFloat32(24),
    sunlit: view.getUint8(28) !== 0,
    heaterOn: view.getUint8(29) !== 0,
    mode,
    commandsAccepted: view.getUint16(31),
    commandsRejected: view.getUint16(33),
  };
}
