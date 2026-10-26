"""Telemetry and telecommand payload definitions."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .packets import PacketError


class Mode(IntEnum):
    SAFE = 0
    NOMINAL = 1
    SCIENCE = 2


class Opcode(IntEnum):
    NOOP = 0
    SET_MODE = 1
    HEATER_ON = 2
    HEATER_OFF = 3
    RESET_COUNTERS = 4


class AckStatus(IntEnum):
    ACCEPTED = 0
    REJECTED = 1


@dataclass(frozen=True)
class Telecommand:
    opcode: Opcode
    argument: float = 0.0

    _FORMAT = struct.Struct(">Bf")

    def encode(self) -> bytes:
        return self._FORMAT.pack(int(self.opcode), self.argument)

    @classmethod
    def decode(cls, payload: bytes) -> "Telecommand":
        try:
            opcode, argument = cls._FORMAT.unpack(payload)
            return cls(Opcode(opcode), argument)
        except (struct.error, ValueError) as exc:
            raise PacketError(f"bad telecommand payload: {exc}") from exc


@dataclass(frozen=True)
class CommandAck:
    command_sequence: int
    status: AckStatus
    reason: str = ""

    _HEADER = struct.Struct(">HB")

    def encode(self) -> bytes:
        return self._HEADER.pack(self.command_sequence, int(self.status)) + self.reason.encode()

    @classmethod
    def decode(cls, payload: bytes) -> "CommandAck":
        try:
            seq, status = cls._HEADER.unpack_from(payload)
            return cls(seq, AckStatus(status), payload[cls._HEADER.size:].decode())
        except (struct.error, ValueError) as exc:
            raise PacketError(f"bad ack payload: {exc}") from exc


@dataclass(frozen=True)
class HousekeepingTelemetry:
    mission_time_s: float
    battery_soc_pct: float
    battery_voltage_v: float
    temperature_c: float
    latitude_deg: float
    longitude_deg: float
    sunlit: bool
    heater_on: bool
    mode: Mode
    commands_accepted: int
    commands_rejected: int

    _FORMAT = struct.Struct(">dfffff??BHH")

    def encode(self) -> bytes:
        return self._FORMAT.pack(
            self.mission_time_s,
            self.battery_soc_pct,
            self.battery_voltage_v,
            self.temperature_c,
            self.latitude_deg,
            self.longitude_deg,
            self.sunlit,
            self.heater_on,
            int(self.mode),
            self.commands_accepted & 0xFFFF,
            self.commands_rejected & 0xFFFF,
        )

    @classmethod
    def decode(cls, payload: bytes) -> "HousekeepingTelemetry":
        try:
            fields = list(cls._FORMAT.unpack(payload))
            fields[8] = Mode(fields[8])
            return cls(*fields)
        except (struct.error, ValueError) as exc:
            raise PacketError(f"bad housekeeping payload: {exc}") from exc
