"""End-to-end demo: runs the space and ground elements together and plays a short pass.

Usage:
    python run_demo.py [--seconds 20] [--time-scale 300] [--loss 0.05]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from spacesim.ground_element import GroundElement, format_housekeeping
from spacesim.messages import Mode, Opcode
from spacesim.space_element import SpaceElement


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=20.0, help="real seconds to run")
    parser.add_argument("--time-scale", type=float, default=300.0, help="sim seconds per real second")
    parser.add_argument("--loss", type=float, default=0.05, help="downlink packet loss, 0 to 1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s", stream=sys.stdout)

    ground = GroundElement()
    space = SpaceElement(ground_addr=ground.address, time_scale=args.time_scale,
                         downlink_loss=args.loss, seed=42)
    ground.space_addr = space.address
    ground.start()
    space.start()

    # Scripted operations plan: (real-time offset in seconds, opcode, argument)
    plan = [
        (1.0, Opcode.NOOP, 0.0),
        (2.0, Opcode.SET_MODE, float(Mode.SCIENCE)),
        (6.0, Opcode.HEATER_ON, 0.0),
        (10.0, Opcode.SET_MODE, 7.0),            # invalid mode, expect REJECTED
        (12.0, Opcode.HEATER_OFF, 0.0),
        (14.0, Opcode.SET_MODE, float(Mode.SCIENCE)),
    ]

    start = time.monotonic()
    next_print = start
    try:
        while (elapsed := time.monotonic() - start) < args.seconds:
            while plan and plan[0][0] <= elapsed:
                _, opcode, arg = plan.pop(0)
                ack = ground.command(opcode, arg)
                result = f"{ack.status.name} {ack.reason}".strip() if ack else "NO ACK (lost or timed out)"
                print(f">> {opcode.name}({arg:g}) -> {result}")
            if time.monotonic() >= next_print and ground.latest:
                print(format_housekeeping(ground.latest))
                next_print = time.monotonic() + 1.0
            time.sleep(0.05)
    finally:
        space.stop()
        ground.stop()

    s = ground.stats
    print("\n--- pass summary ---")
    print(f"telemetry frames received : {s.telemetry_received}")
    print(f"downlink packets lost     : {s.packets_lost}")
    print(f"corrupt packets           : {s.packets_corrupt}")
    print(f"alarms raised             : {len(s.alarms)}")
    for alarm in s.alarms:
        print(f"  {alarm}")


if __name__ == "__main__":
    main()
