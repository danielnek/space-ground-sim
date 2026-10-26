"""Simulated ground element: a ground station with mission control functions.

Responsibilities:
* uplink telecommands to the space element and track their acknowledgements
* receive and decode telemetry
* check telemetry against limits and raise alarms
* detect lost downlink packets from gaps in sequence counters
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
from dataclasses import dataclass, field

from .messages import AckStatus, CommandAck, HousekeepingTelemetry, Mode, Opcode, Telecommand
from .packets import MAX_SEQUENCE, Apid, Packet, PacketError, PacketType

log = logging.getLogger("ground")


@dataclass(frozen=True)
class Limit:
    parameter: str
    low: float
    high: float


DEFAULT_LIMITS = (
    Limit("battery_soc_pct", 30.0, 100.0),
    Limit("temperature_c", -10.0, 40.0),
)


@dataclass
class GroundStats:
    telemetry_received: int = 0
    packets_lost: int = 0
    packets_corrupt: int = 0
    alarms: list[str] = field(default_factory=list)


class GroundElement:
    def __init__(
        self,
        space_addr: tuple[str, int] | None = None,
        bind_addr: tuple[str, int] = ("127.0.0.1", 0),
        limits: tuple[Limit, ...] = DEFAULT_LIMITS,
    ) -> None:
        self.space_addr = space_addr
        self.limits = limits
        self.stats = GroundStats()
        self.latest: HousekeepingTelemetry | None = None
        self.acks: dict[int, CommandAck] = {}
        self._uplink_seq = 0
        self._last_seq: dict[int, int] = {}
        self._in_alarm: set[str] = set()
        self._lock = threading.Lock()
        self._ack_event = threading.Condition(self._lock)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(bind_addr)
        self._sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self._sock.getsockname()

    # ---- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._receive_loop, name="ground-rx", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._sock.close()

    # ---- uplink ------------------------------------------------------------------
    def send_command(self, opcode: Opcode, argument: float = 0.0) -> int:
        """Uplink a telecommand and return its sequence count."""
        if self.space_addr is None:
            raise RuntimeError("space element address not set")
        with self._lock:
            seq = self._uplink_seq
            self._uplink_seq = (seq + 1) & MAX_SEQUENCE
        packet = Packet(PacketType.TELECOMMAND, Apid.COMMAND, seq, Telecommand(opcode, argument).encode())
        self._sock.sendto(packet.encode(), self.space_addr)
        log.info("uplinked #%d %s(%g)", seq, opcode.name, argument)
        return seq

    def wait_for_ack(self, seq: int, timeout: float = 2.0) -> CommandAck | None:
        with self._ack_event:
            self._ack_event.wait_for(lambda: seq in self.acks, timeout=timeout)
            return self.acks.get(seq)

    def command(self, opcode: Opcode, argument: float = 0.0, timeout: float = 2.0) -> CommandAck | None:
        """Send a command and block until it is acknowledged or times out."""
        return self.wait_for_ack(self.send_command(opcode, argument), timeout)

    # ---- downlink ------------------------------------------------------------------
    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                continue  # socket closed, or ICMP port-unreachable noise on Windows
            self.handle_packet(data)

    def handle_packet(self, data: bytes) -> None:
        try:
            packet = Packet.decode(data)
        except PacketError as exc:
            self.stats.packets_corrupt += 1
            log.error("corrupt downlink packet: %s", exc)
            return
        if packet.packet_type is not PacketType.TELEMETRY:
            return
        self._track_sequence(packet)
        try:
            if packet.apid == Apid.HOUSEKEEPING:
                self._on_housekeeping(HousekeepingTelemetry.decode(packet.payload))
            elif packet.apid == Apid.COMMAND_ACK:
                self._on_ack(CommandAck.decode(packet.payload))
        except PacketError as exc:
            self.stats.packets_corrupt += 1
            log.error("bad payload on APID %#x: %s", packet.apid, exc)

    def _track_sequence(self, packet: Packet) -> None:
        last = self._last_seq.get(packet.apid)
        if last is not None:
            gap = (packet.sequence_count - last - 1) & MAX_SEQUENCE
            if 0 < gap < MAX_SEQUENCE // 2:
                self.stats.packets_lost += gap
        self._last_seq[packet.apid] = packet.sequence_count

    def _on_housekeeping(self, tm: HousekeepingTelemetry) -> None:
        self.stats.telemetry_received += 1
        self.latest = tm
        self.check_limits(tm)

    def _on_ack(self, ack: CommandAck) -> None:
        with self._ack_event:
            self.acks[ack.command_sequence] = ack
            self._ack_event.notify_all()
        level = logging.INFO if ack.status is AckStatus.ACCEPTED else logging.WARNING
        log.log(level, "ack #%d %s %s", ack.command_sequence, ack.status.name, ack.reason)

    def check_limits(self, tm: HousekeepingTelemetry) -> list[str]:
        """Raise an alarm when a parameter leaves its limits, once per excursion."""
        new_alarms = []
        for limit in self.limits:
            value = getattr(tm, limit.parameter)
            out = not (limit.low <= value <= limit.high)
            if out and limit.parameter not in self._in_alarm:
                self._in_alarm.add(limit.parameter)
                msg = (f"t={tm.mission_time_s:.0f}s ALARM {limit.parameter}={value:.1f} "
                       f"outside [{limit.low}, {limit.high}]")
                new_alarms.append(msg)
                log.warning(msg)
            elif not out and limit.parameter in self._in_alarm:
                self._in_alarm.discard(limit.parameter)
                log.info("t=%.0fs %s back within limits", tm.mission_time_s, limit.parameter)
        self.stats.alarms.extend(new_alarms)
        return new_alarms


def format_housekeeping(tm: HousekeepingTelemetry) -> str:
    return (f"t={tm.mission_time_s:7.0f}s  mode={tm.mode.name:<7} soc={tm.battery_soc_pct:5.1f}%  "
            f"V={tm.battery_voltage_v:4.2f}  T={tm.temperature_c:6.1f}C  "
            f"heater={'ON ' if tm.heater_on else 'OFF'}  {'SUN ' if tm.sunlit else 'ECL '}  "
            f"lat={tm.latitude_deg:6.1f} lon={tm.longitude_deg:7.1f}")


HELP = """commands:
  noop | heater on | heater off | reset
  mode safe | mode nominal | mode science
  status | stats | quit"""


def parse_console_command(line: str) -> tuple[Opcode, float] | None:
    words = line.lower().split()
    if words == ["noop"]:
        return Opcode.NOOP, 0.0
    if words == ["reset"]:
        return Opcode.RESET_COUNTERS, 0.0
    if len(words) == 2 and words[0] == "heater" and words[1] in ("on", "off"):
        return (Opcode.HEATER_ON if words[1] == "on" else Opcode.HEATER_OFF), 0.0
    if len(words) == 2 and words[0] == "mode" and words[1].upper() in Mode.__members__:
        return Opcode.SET_MODE, float(Mode[words[1].upper()])
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulated ground element console")
    parser.add_argument("--port", type=int, default=5001, help="telemetry receive port")
    parser.add_argument("--space-port", type=int, default=5000, help="space element uplink port")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    ground = GroundElement(space_addr=("127.0.0.1", args.space_port), bind_addr=("127.0.0.1", args.port))
    ground.start()
    print(HELP)
    try:
        while True:
            line = input("ground> ").strip()
            if line in ("quit", "exit"):
                break
            if line == "status":
                print(format_housekeeping(ground.latest) if ground.latest else "no telemetry yet")
            elif line == "stats":
                print(ground.stats)
            elif (parsed := parse_console_command(line)) is not None:
                ack = ground.command(*parsed)
                print(f"ack: {ack.status.name} {ack.reason}" if ack else "no ack (timeout)")
            elif line:
                print(HELP)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        ground.stop()


if __name__ == "__main__":
    main()
