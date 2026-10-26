import unittest

from spacesim.messages import AckStatus, Mode, Opcode, Telecommand
from spacesim.space_element import ORBIT_PERIOD_S, INCLINATION_DEG, SpacecraftModel


class OrbitTest(unittest.TestCase):
    def test_latitude_bounded_by_inclination(self):
        model = SpacecraftModel()
        for _ in range(200):
            model.step(ORBIT_PERIOD_S / 200)
            lat, lon = model.ground_track()
            self.assertLessEqual(abs(lat), INCLINATION_DEG + 1e-6)
            self.assertTrue(-180.0 <= lon < 180.0)

    def test_orbit_has_sunlit_and_eclipse_phases(self):
        model = SpacecraftModel()
        states = set()
        for _ in range(100):
            model.step(ORBIT_PERIOD_S / 100)
            states.add(model.sunlit)
        self.assertEqual(states, {True, False})


class PowerThermalTest(unittest.TestCase):
    def test_battery_drains_in_eclipse(self):
        model = SpacecraftModel(mission_time_s=ORBIT_PERIOD_S / 2)  # middle of eclipse
        before = model.battery_soc_pct
        model.step(60)
        self.assertLess(model.battery_soc_pct, before)

    def test_autonomous_safe_mode_on_low_battery(self):
        model = SpacecraftModel(battery_soc_pct=21.0, mode=Mode.SCIENCE,
                                mission_time_s=ORBIT_PERIOD_S / 2)
        model.step(300)
        self.assertIs(model.mode, Mode.SAFE)
        self.assertTrue(model.events)

    def test_heater_warms_spacecraft(self):
        cold, warm = SpacecraftModel(), SpacecraftModel(heater_on=True)
        for m in (cold, warm):
            m.step(600)
        self.assertGreater(warm.temperature_c, cold.temperature_c)


class CommandTest(unittest.TestCase):
    def test_set_mode_accepted(self):
        model = SpacecraftModel()
        status, _ = model.execute(Telecommand(Opcode.SET_MODE, float(Mode.SAFE)))
        self.assertIs(status, AckStatus.ACCEPTED)
        self.assertIs(model.mode, Mode.SAFE)
        self.assertEqual(model.commands_accepted, 1)

    def test_science_rejected_when_battery_low(self):
        model = SpacecraftModel(battery_soc_pct=30.0)
        status, reason = model.execute(Telecommand(Opcode.SET_MODE, float(Mode.SCIENCE)))
        self.assertIs(status, AckStatus.REJECTED)
        self.assertIn("battery", reason)
        self.assertEqual(model.commands_rejected, 1)

    def test_invalid_mode_rejected(self):
        status, _ = SpacecraftModel().execute(Telecommand(Opcode.SET_MODE, 9.0))
        self.assertIs(status, AckStatus.REJECTED)

    def test_reset_counters(self):
        model = SpacecraftModel(commands_accepted=5, commands_rejected=2)
        model.execute(Telecommand(Opcode.RESET_COUNTERS))
        self.assertEqual((model.commands_accepted, model.commands_rejected), (0, 0))


if __name__ == "__main__":
    unittest.main()
