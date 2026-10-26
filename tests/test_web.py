import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from spacesim.ground_element import GroundElement
from spacesim.messages import HousekeepingTelemetry, Mode
from spacesim.packets import Apid, Packet, PacketType
from spacesim.space_element import SpaceElement
from spacesim.web import build_state, make_handler, run_command


class WebTest(unittest.TestCase):
    def setUp(self):
        self.ground = GroundElement()
        self.space = SpaceElement(ground_addr=self.ground.address, time_scale=60.0, tick_s=0.05)
        self.ground.space_addr = self.space.address
        self.ground.start()
        self.space.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ground))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.space.stop()
        self.ground.stop()

    def _post(self, body: bytes) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + "/api/command", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_index_page_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as resp:
            self.assertIn(b"Mission Control", resp.read())

    def test_state_is_json(self):
        with urllib.request.urlopen(self.base + "/api/state", timeout=5) as resp:
            state = json.load(resp)
        self.assertIn("stats", state)
        self.assertEqual({l["parameter"] for l in state["limits"]}, {"battery_soc_pct", "temperature_c"})

    def test_command_round_trip(self):
        status, body = self._post(json.dumps({"command": "heater on"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ACCEPTED")

    def test_unknown_command_reported(self):
        status, body = self._post(json.dumps({"command": "fly to mars"}).encode())
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])

    def test_malformed_body_is_400(self):
        status, body = self._post(b"not json")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/nope", timeout=5)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 404)


class BuildStateTest(unittest.TestCase):
    def test_telemetry_serialised_with_mode_name(self):
        ground = GroundElement()
        self.addCleanup(ground.stop)
        self.assertIsNone(build_state(ground)["telemetry"])
        tm = HousekeepingTelemetry(10.0, 80.0, 7.5, 15.0, 1.5, 2.5, True, False, Mode.SCIENCE, 1, 0)
        ground.handle_packet(Packet(PacketType.TELEMETRY, Apid.HOUSEKEEPING, 0, tm.encode()).encode())
        state = build_state(ground)
        self.assertEqual(state["telemetry"]["mode"], "SCIENCE")
        self.assertEqual(state["stats"]["telemetry_received"], 1)

    def test_run_command_without_ack_reports_error(self):
        ground = GroundElement()
        self.addCleanup(ground.stop)
        ground.space_addr = ("127.0.0.1", 9)  # nothing listening
        ground.command = lambda *a, **k: None  # skip the 2 s ack wait
        result = run_command(ground, "noop")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
