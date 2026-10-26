"""CCSDS-style space packet encoding shared by the space and ground elements.

Wire format (big-endian), loosely based on the CCSDS Space Packet Protocol:

    +-------------------+-------------------+-------------------+---------+--------+
    | word 1 (16 bits)  | word 2 (16 bits)  | length (16 bits)  | payload | CRC-16 |
    +-------------------+-------------------+-------------------+---------+--------+
    word 1: version(3) | type(1) | sec-hdr flag(1) | APID(11)
    word 2: sequence flags(2) | sequence count(14)
    length: number of bytes after the header, minus one (payload + CRC)

The format is language-neutral, so Java or C ports of either element can
interoperate with this Python prototype.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

PRIMARY_HEADER = struct.Struct(">HHH")
CRC_FIELD = struct.Struct(">H")
MAX_APID = 0x7FF
MAX_SEQUENCE = 0x3FFF


class PacketError(ValueError):
    """Raised when a packet cannot be encoded or decoded."""


class PacketType(IntEnum):
    TELEMETRY = 0
    TELECOMMAND = 1


class Apid(IntEnum):
    """Application process identifiers used by this mission."""

    HOUSEKEEPING = 0x064
    COMMAND = 0x0C8
    COMMAND_ACK = 0x0C9


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE, the checksum used by CCSDS packets."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


@dataclass(frozen=True)
class Packet:
    packet_type: PacketType
    apid: int
    sequence_count: int
    payload: bytes

    def encode(self) -> bytes:
        if not 0 <= self.apid <= MAX_APID:
            raise PacketError(f"APID out of range: {self.apid}")
        word1 = (int(self.packet_type) << 12) | self.apid
        word2 = (0b11 << 14) | (self.sequence_count & MAX_SEQUENCE)
        length = len(self.payload) + CRC_FIELD.size - 1
        header = PRIMARY_HEADER.pack(word1, word2, length)
        body = header + self.payload
        return body + CRC_FIELD.pack(crc16_ccitt(body))

    @classmethod
    def decode(cls, data: bytes) -> "Packet":
        minimum = PRIMARY_HEADER.size + CRC_FIELD.size
        if len(data) < minimum:
            raise PacketError(f"packet too short: {len(data)} bytes")
        word1, word2, length = PRIMARY_HEADER.unpack_from(data)
        expected = PRIMARY_HEADER.size + length + 1
        if len(data) != expected:
            raise PacketError(f"length mismatch: header says {expected}, got {len(data)}")
        body, (received_crc,) = data[:-2], CRC_FIELD.unpack(data[-2:])
        if crc16_ccitt(body) != received_crc:
            raise PacketError("CRC check failed")
        return cls(
            packet_type=PacketType((word1 >> 12) & 0x1),
            apid=word1 & MAX_APID,
            sequence_count=word2 & MAX_SEQUENCE,
            payload=body[PRIMARY_HEADER.size:],
        )
