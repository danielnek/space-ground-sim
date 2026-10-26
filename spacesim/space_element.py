"""Simulated space element: a small satellite in low Earth orbit.

The module has two layers:
* SpacecraftModel  - pure simulation of orbit, power and thermal state (easy to unit test).
* SpaceElement     - network node that receives telecommands and sends telemetry over UDP.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import socket
import threading
import time
from dataclasses import dataclass, field

from .messages import AckStatus, CommandAck, HousekeepingTelemetry, Mode, Opcode, Telecommand
from .packets import Apid, Packet, PacketError, PacketType

log = logging.getLogger("space")

ORBIT_PERIOD_S = 5_400.0          # ~90 minute LEO orbit
INCLINATION_DEG = 51.6
EARTH_ROTATION_DEG_PER_S = 360.0 / 86_164.0
BATTERY_CAPACITY_WH = 80.0
SOLAR_POWER_W = 40.0
HEATER_POWER_W = 6.0
MODE_LOAD_W = {Mode.SAFE: 8.0, Mode.NOMINAL: 15.0, Mode.SCIENCE: 28.0}
SAFE_MODE_SOC_PCT = 20.0          # autonomous fallback threshold
SCIENCE_MIN_SOC_PCT = 40.0


@dataclass
class SpacecraftModel:
    mission_time_s: float = 0.0
    battery_soc_pct: float = 80.0
    temperature_c: float = 15.0
    mode: Mode = Mode.NOMINAL
    heater_on: bool = False
    commands_accepted: int = 0
    commands_rejected: int = 0
    events: list[str] = field(default_factory=list)

    # ---- orbit -------------------------------------------------------------
    @property
    def orbit_phase_rad(self) -> float:
        return 2 * math.pi * (self.mission_time_s % ORBIT_PERIOD_S) / ORBIT_PERIOD_S

    @property
    def sunlit(self) -> bool:
        # Simple eclipse model: roughly a third of each orbit is in shadow.
        return math.cos(self.orbit_phase_rad) > -0.45

    def ground_track(self) -> tuple[float, float]:
        theta = self.orbit_phase_rad
        inc = math.radians(INCLINATION_DEG)
        lat = math.degrees(math.asin(math.sin(inc) * math.sin(theta)))
        lon = math.degrees(math.atan2(math.cos(inc) * math.sin(theta), math.cos(theta)))
        lon -= EARTH_ROTATION_DEG_PER_S * self.mission_time_s
        lon = (lon + 180.0) % 360.0 - 180.0
        return lat, lon

    # ---- power and thermal ---------------------------------------------------
    @property
    def battery_voltage_v(self) -> float:
        return 6.0 + 2.4 * self.battery_soc_pct / 100.0   # 2S Li-ion pack

    def load_w(self) -> float:
        return MODE_LOAD_W[self.mode] + (HEATER_POWER_W if self.heater_on else 0.0)

    def step(self, dt_s: float) -> None:
        """Advance the simulation by dt_s seconds of mission time."""
        self.mission_time_s += dt_s

        generated = SOLAR_POWER_W if self.sunlit else 0.0
        net_wh = (generated - self.load_w()) * dt_s / 3600.0
        self.battery_soc_pct += 100.0 * net_wh / BATTERY_CAPACITY_WH
        self.battery_soc_pct = min(100.0, max(0.0, self.battery_soc_pct))

        target_c = (20.0 if self.sunlit else -15.0) + (18.0 if self.heater_on else 0.0)
        self.temperature_c += (target_c - self.temperature_c) * min(1.0, dt_s / 600.0)

        if self.battery_soc_pct < SAFE_MODE_SOC_PCT and self.mode != Mode.SAFE:
            self.mode = Mode.SAFE
            self.events.append(f"t={self.mission_time_s:.0f}s autonomous SAFE mode (low battery)")

    # ---- commanding ----------------------------------------------------------
    def execute(self, command: Telecommand) -> tuple[AckStatus, str]:
        status, reason = self._apply(command)
        if command.opcode is Opcode.RESET_COUNTERS and status is AckStatus.ACCEPTED:
            return status, reason  # the reset itself is not counted
        if status is AckStatus.ACCEPTED:
            self.commands_accepted += 1
        else:
            self.commands_rejected += 1
        return status, reason

    def _apply(self, command: Telecommand) -> tuple[AckStatus, str]:
        if command.opcode is Opcode.NOOP:
            return AckStatus.ACCEPTED, ""
        if command.opcode is Opcode.SET_MODE:
            try:
                requested = Mode(int(command.argument))
            except ValueError:
                return AckStatus.REJECTED, f"unknown mode {command.argument:g}"
            if requested is Mode.SCIENCE and self.battery_soc_pct < SCIENCE_MIN_SOC_PCT:
                return AckStatus.REJECTED, "battery too low for SCIENCE"
            self.mode = requested
            return AckStatus.ACCEPTED, ""
        if command.opcode is Opcode.HEATER_ON:
            self.heater_on = True
            return AckStatus.ACCEPTED, ""
        if command.opcode is Opcode.HEATER_OFF:
            self.heater_on = False
            return AckStatus.ACCEPTED, ""
        if command.opcode is Opcode.RESET_COUNTERS:
            self.commands_accepted = self.commands_rejected = 0
            return AckStatus.ACCEPTED, ""
        return AckStatus.REJECTED, f"unsupported opcode {command.opcode}"

    def housekeeping(self) -> HousekeepingTelemetry:
        lat, lon = self.ground_track()
        return HousekeepingTelemetry(
            mission_time_s=self.mission_time_s,
            battery_soc_pct=self.battery_soc_pct,
            battery_voltage_v=self.battery_voltage_v,
            temperature_c=self.temperature_c,
            latitude_deg=lat,
            longitude_deg=lon,
            sunlit=self.sunlit,
            heater_on=self.heater_on,
            mode=self.mode,
            commands_accepted=self.commands_accepted,
            commands_rejected=self.commands_rejected,
        )


class SpaceElement:
    """UDP node that hosts the spacecraft model.

    Receives telecommands on its own port and downlinks telemetry and command
    acknowledgements to the ground element's address.
    """

    def __init__(
        self,
        ground_addr: tuple[str, int],
        bind_addr: tuple[str, int] = ("127.0.0.1", 0),
        time_scale: float = 60.0,
        tick_s: float = 0.1,
        downlink_loss: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.model = SpacecraftModel()
        self.ground_addr = ground_addr
        self.time_scale = time_scale
        self.tick_s = tick_s
        self.downlink_loss = downlink_loss
        self._rng = random.Random(seed)
        self._sequence = {Apid.HOUSEKEEPING: 0, Apid.COMMAND_ACK: 0}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(bind_addr)
        self._sock.settimeout(tick_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self._sock.getsockname()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="space-element", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._sock.close()

    def run(self) -> None:
        log.info("space element up on %s, downlink to %s", self.address, self.ground_addr)
        last = time.monotonic()
        next_hk = last
        while not self._stop.is_set():
            self._receive_commands()
            now = time.monotonic()
            self.model.step((now - last) * self.time_scale)
            last = now
            while self.model.events:
                log.warning(self.model.events.pop(0))
            if now >= next_hk:
                self._downlink(Apid.HOUSEKEEPING, self.model.housekeeping().encode())
                next_hk = now + self.tick_s

    def _receive_commands(self) -> None:
        try:
            data, _ = self._sock.recvfrom(4096)
        except (TimeoutError, socket.timeout):
            return
        except OSError:
            return  # socket closed during shutdown, or ICMP noise on Windows
        try:
            packet = Packet.decode(data)
            if packet.packet_type is not PacketType.TELECOMMAND or packet.apid != Apid.COMMAND:
                raise PacketError("not a telecommand")
            command = Telecommand.decode(packet.payload)
        except PacketError as exc:
            log.error("dropped uplink packet: %s", exc)
            self.model.commands_rejected += 1
            return
        status, reason = self.model.execute(command)
        log.info("executed %s(%g): %s %s", command.opcode.name, command.argument, status.name, reason)
        ack = CommandAck(packet.sequence_count, status, reason)
        self._downlink(Apid.COMMAND_ACK, ack.encode())

    def _downlink(self, apid: Apid, payload: bytes) -> None:
        seq = self._sequence[apid]
        self._sequence[apid] = (seq + 1) & 0x3FFF
        if self._rng.random() < self.downlink_loss:
            return  # simulated RF packet loss
        packet = Packet(PacketType.TELEMETRY, apid, seq, payload)
        try:
            self._sock.sendto(packet.encode(), self.ground_addr)
        except OSError as exc:
            log.debug("downlink failed: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulated space element")
    parser.add_argument("--port", type=int, default=5000, help="uplink (command) port")
    parser.add_argument("--ground-port", type=int, default=5001, help="ground telemetry port")
    parser.add_argument("--time-scale", type=float, default=60.0, help="sim seconds per real second")
    parser.add_argument("--loss", type=float, default=0.0, help="downlink packet loss, 0 to 1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    node = SpaceElement(
        ground_addr=("127.0.0.1", args.ground_port),
        bind_addr=("127.0.0.1", args.port),
        time_scale=args.time_scale,
        downlink_loss=args.loss,
    )
    try:
        node.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
