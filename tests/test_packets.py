import unittest

from spacesim.messages import AckStatus, CommandAck, HousekeepingTelemetry, Mode, Opcode, Telecommand
from spacesim.packets import Apid, Packet, PacketError, PacketType, crc16_ccitt


class CrcTest(unittest.TestCase):
    def test_known_check_value(self):
        # Standard check value for CRC-16/CCITT-FALSE
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)


class PacketTest(unittest.TestCase):
    def test_round_trip(self):
        original = Packet(PacketType.TELECOMMAND, Apid.COMMAND, 1234, b"\x01\x02\x03")
        self.assertEqual(Packet.decode(original.encode()), original)

    def test_sequence_count_wraps_at_14_bits(self):
        packet = Packet(PacketType.TELEMETRY, Apid.HOUSEKEEPING, 0x4001, b"x")
        self.assertEqual(Packet.decode(packet.encode()).sequence_count, 1)

    def test_corrupted_byte_fails_crc(self):
        data = bytearray(Packet(PacketType.TELEMETRY, Apid.HOUSEKEEPING, 7, b"hello").encode())
        data[7] ^= 0xFF
        with self.assertRaises(PacketError):
            Packet.decode(bytes(data))

    def test_truncated_packet_rejected(self):
        data = Packet(PacketType.TELEMETRY, Apid.HOUSEKEEPING, 7, b"hello").encode()
        with self.assertRaises(PacketError):
            Packet.decode(data[:-1])

    def test_apid_out_of_range(self):
        with self.assertRaises(PacketError):
            Packet(PacketType.TELEMETRY, 0x800, 0, b"").encode()


class MessageTest(unittest.TestCase):
    def test_telecommand_round_trip(self):
        tc = Telecommand(Opcode.SET_MODE, 2.0)
        self.assertEqual(Telecommand.decode(tc.encode()), tc)

    def test_unknown_opcode_rejected(self):
        with self.assertRaises(PacketError):
            Telecommand.decode(b"\x63\x00\x00\x00\x00")

    def test_ack_round_trip(self):
        ack = CommandAck(42, AckStatus.REJECTED, "battery too low")
        self.assertEqual(CommandAck.decode(ack.encode()), ack)

    def test_housekeeping_round_trip(self):
        # Values chosen to be exact in 32-bit floats, which the wire format uses.
        tm = HousekeepingTelemetry(100.0, 75.5, 7.75, 12.25, 10.5, -20.25, True, False,
                                   Mode.SCIENCE, 3, 1)
        self.assertEqual(HousekeepingTelemetry.decode(tm.encode()), tm)


if __name__ == "__main__":
    unittest.main()
