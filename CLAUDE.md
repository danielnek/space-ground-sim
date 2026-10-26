# Project: space-ground-sim

## Overview
Prototype software that operates a simulated space element (LEO satellite) and a
simulated ground element (ground station). They communicate over UDP with a
CCSDS-style packet format. Python is the reference implementation; Java and C ports
are planned and must stay wire-compatible.

## Layout
- spacesim/packets.py: packet header, APIDs, CRC. The wire format contract.
- spacesim/messages.py: payload layouts for commands, acks and telemetry.
- spacesim/space_element.py: SpacecraftModel (pure physics) + SpaceElement (UDP node).
- spacesim/ground_element.py: GroundElement (UDP node, limits, alarms) + console.
- run_demo.py: scripted end-to-end pass.

## Commands
- Python is launched with `py` on this machine. `python` is not on PATH.
- Test: py -m unittest discover -s tests -t .
- Demo: py run_demo.py
- Two-terminal mode: py -m spacesim.space_element / py -m spacesim.ground_element

## Rules
- Standard library only. Ask before adding dependencies.
- Type hints on all functions. Keep physics in SpacecraftModel free of networking.
- Any change to packets.py or messages.py changes the wire format. Update README and
  note it, because Java and C ports depend on it.
- Telemetry floats are 32-bit on the wire. Tests must use float32-exact values.
- Add or update a test for every behaviour change, then run the full suite.
