"""Web GUI for the simulator: runs both elements in-process and serves a mission control page.

The page polls GET /api/state and sends commands with POST /api/command. The server is a thin
view over GroundElement; it adds no packet logic of its own.

Usage:
    py -m spacesim.web [--port 8000] [--time-scale 300] [--loss 0.05]
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ground_element import GroundElement, parse_console_command
from .space_element import SpaceElement

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 1024


def build_state(ground: GroundElement) -> dict:
    """Snapshot of ground-side state as JSON-serialisable data."""
    tm = ground.latest
    telemetry = None
    if tm is not None:
        telemetry = asdict(tm)
        telemetry["mode"] = tm.mode.name
    s = ground.stats
    return {
        "telemetry": telemetry,
        "stats": {
            "telemetry_received": s.telemetry_received,
            "packets_lost": s.packets_lost,
            "packets_corrupt": s.packets_corrupt,
        },
        "alarms": list(s.alarms[-20:]),
        "limits": [asdict(limit) for limit in ground.limits],
    }


def run_command(ground: GroundElement, line: str) -> dict:
    """Parse a console-style command, uplink it and wait for the ack."""
    parsed = parse_console_command(line)
    if parsed is None:
        return {"ok": False, "error": f"unknown command: {line!r}"}
    ack = ground.command(*parsed)
    if ack is None:
        return {"ok": False, "error": "no ack (lost or timed out)"}
    return {"ok": True, "status": ack.status.name, "reason": ack.reason}


def make_handler(ground: GroundElement) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path == "/api/state":
                self._send_json(200, build_state(ground))
            elif self.path in ("/", "/index.html"):
                self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/api/command":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "error": "body too large"})
                return
            try:
                line = str(json.loads(self.rfile.read(length))["command"])
            except (ValueError, KeyError, TypeError):
                self._send_json(400, {"ok": False, "error": 'expected JSON {"command": "..."}'})
                return
            self._send_json(200, run_command(ground, line))

        def log_message(self, format: str, *args: object) -> None:
            pass  # keep the console quiet; polling is noisy

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    parser.add_argument("--time-scale", type=float, default=300.0, help="sim seconds per real second")
    parser.add_argument("--loss", type=float, default=0.05, help="downlink packet loss, 0 to 1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    ground = GroundElement()
    space = SpaceElement(ground_addr=ground.address, time_scale=args.time_scale, downlink_loss=args.loss)
    ground.space_addr = space.address
    ground.start()
    space.start()
    # Bind to loopback only: the page can uplink commands, so it must not be reachable from the network.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(ground))
    print(f"Mission control at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        space.stop()
        ground.stop()


if __name__ == "__main__":
    main()
