import time
import unittest

from spacesim.ground_element import GroundElement, Limit, parse_console_command
from spacesim.messages import AckStatus, HousekeepingTelemetry, Mode, Opcode
from spacesim.packets import Apid, Packet, PacketType


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        from spacesim.space_element import SpaceElement

        self.ground = GroundElement()
        self.space = SpaceElement(ground_addr=self.ground.address, time_scale=60.0, tick_s=0.05)
        self.ground.space_addr = self.space.address
        self.ground.start()
        self.space.start()

    def tearDown(self):
        self.space.stop()
        self.ground.stop()

    def test_telemetry_flows_to_ground(self):
        self.assertTrue(wait_until(lambda: self.ground.stats.telemetry_received >= 3))
        self.assertIsNotNone(self.ground.latest)

    def test_command_is_executed_and_acknowledged(self):
        ack = self.ground.command(Opcode.HEATER_ON)
        self.assertIsNotNone(ack)
        self.assertIs(ack.status, AckStatus.ACCEPTED)
        self.assertTrue(wait_until(lambda: self.ground.latest and self.ground.latest.heater_on))

    def test_invalid_command_is_rejected(self):
        ack = self.ground.command(Opcode.SET_MODE, 42.0)
        self.assertIs(ack.status, AckStatus.REJECTED)


class GroundLogicTest(unittest.TestCase):
    def setUp(self):
        self.ground = GroundElement(limits=(Limit("temperature_c", -10.0, 40.0),))

    def tearDown(self):
        self.ground.stop()

    def _hk(self, temp):
        return HousekeepingTelemetry(0.0, 80.0, 7.9, temp, 0.0, 0.0, True, False, Mode.NOMINAL, 0, 0)

    def test_alarm_raised_once_per_excursion(self):
        self.assertEqual(len(self.ground.check_limits(self._hk(-20.0))), 1)
        self.assertEqual(self.ground.check_limits(self._hk(-25.0)), [])
        self.ground.check_limits(self._hk(10.0))
        self.assertEqual(len(self.ground.check_limits(self._hk(50.0))), 1)

    def test_sequence_gap_counts_lost_packets(self):
        for seq in (0, 1, 4, 5):
            pkt = Packet(PacketType.TELEMETRY, Apid.HOUSEKEEPING, seq, self._hk(15.0).encode())
            self.ground.handle_packet(pkt.encode())
        self.assertEqual(self.ground.stats.packets_lost, 2)
        self.assertEqual(self.ground.stats.telemetry_received, 4)

    def test_corrupt_packet_counted(self):
        self.ground.handle_packet(b"\x00\x01garbage")
        self.assertEqual(self.ground.stats.packets_corrupt, 1)

    def test_console_parser(self):
        self.assertEqual(parse_console_command("mode science"), (Opcode.SET_MODE, 2.0))
        self.assertEqual(parse_console_command("heater off"), (Opcode.HEATER_OFF, 0.0))
        self.assertIsNone(parse_console_command("fly to mars"))


if __name__ == "__main__":
    unittest.main()
