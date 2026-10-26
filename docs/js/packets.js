// CCSDS-style space packet encoding. Port of spacesim/packets.py: the wire format is identical.
//
// Wire format (big-endian):
//   word 1 (16 bits): version(3) | type(1) | sec-hdr flag(1) | APID(11)
//   word 2 (16 bits): sequence flags(2) | sequence count(14)
//   length (16 bits): number of bytes after the header, minus one (payload + CRC)
//   payload, then CRC-16/CCITT-FALSE over header + payload

export const HEADER_SIZE = 6;
export const CRC_SIZE = 2;
export const MAX_APID = 0x7ff;
export const MAX_SEQUENCE = 0x3fff;

export class PacketError extends Error {
  constructor(message) {
    super(message);
    this.name = "PacketError";
  }
}

export const PacketType = Object.freeze({ TELEMETRY: 0, TELECOMMAND: 1 });

/** Application process identifiers used by this mission. */
export const Apid = Object.freeze({ HOUSEKEEPING: 0x064, COMMAND: 0x0c8, COMMAND_ACK: 0x0c9 });

/** CRC-16/CCITT-FALSE, the checksum used by CCSDS packets. */
export function crc16ccitt(data, crc = 0xffff) {
  for (const byte of data) {
    crc ^= byte << 8;
    for (let i = 0; i < 8; i++) {
      crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
      crc &= 0xffff;
    }
  }
  return crc;
}

/** @returns {Uint8Array} */
export function encodePacket({ packetType, apid, sequenceCount, payload }) {
  if (!(apid >= 0 && apid <= MAX_APID)) throw new PacketError(`APID out of range: ${apid}`);
  const out = new Uint8Array(HEADER_SIZE + payload.length + CRC_SIZE);
  const view = new DataView(out.buffer);
  view.setUint16(0, (packetType << 12) | apid);
  view.setUint16(2, (0b11 << 14) | (sequenceCount & MAX_SEQUENCE));
  view.setUint16(4, payload.length + CRC_SIZE - 1);
  out.set(payload, HEADER_SIZE);
  view.setUint16(out.length - CRC_SIZE, crc16ccitt(out.subarray(0, out.length - CRC_SIZE)));
  return out;
}

/** @returns {{packetType: number, apid: number, sequenceCount: number, payload: Uint8Array}} */
export function decodePacket(data) {
  if (data.length < HEADER_SIZE + CRC_SIZE) throw new PacketError(`packet too short: ${data.length} bytes`);
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const word1 = view.getUint16(0);
  const word2 = view.getUint16(2);
  const expected = HEADER_SIZE + view.getUint16(4) + 1;
  if (data.length !== expected) throw new PacketError(`length mismatch: header says ${expected}, got ${data.length}`);
  const body = data.subarray(0, data.length - CRC_SIZE);
  if (crc16ccitt(body) !== view.getUint16(data.length - CRC_SIZE)) throw new PacketError("CRC check failed");
  return {
    packetType: (word1 >> 12) & 0x1,
    apid: word1 & MAX_APID,
    sequenceCount: word2 & MAX_SEQUENCE,
    payload: body.slice(HEADER_SIZE),
  };
}

export function toHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
