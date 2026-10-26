# Space–Ground Simulator (Python prototype)

Prototype software that operates a **simulated space element** (a small LEO satellite)
and a **simulated ground element** (a ground station with mission control functions).
The two elements talk over UDP using a CCSDS-style packet format.

## Architecture

```
 +--------------------------+        telecommands (UDP)        +---------------------------+
 |      GROUND ELEMENT      |  ------------------------------> |       SPACE ELEMENT       |
 |  ground_element.py       |                                  |  space_element.py         |
 |  - command uplink        |  <------------------------------ |  - orbit / eclipse model  |
 |  - ack tracking          |   telemetry + acks (UDP, lossy)  |  - power / battery model  |
 |  - limit checks, alarms  |                                  |  - thermal + heater model |
 |  - packet-loss detection |                                  |  - command execution      |
 +--------------------------+                                  |  - autonomous SAFE mode   |
              \                                                +---------------------------+
               \__________ shared wire format: packets.py + messages.py _________/
```

| Module | Purpose |
|---|---|
| `spacesim/packets.py` | CCSDS-style primary header, APIDs, sequence counts, CRC-16 |
| `spacesim/messages.py` | Telecommand, command acknowledgement and housekeeping telemetry payloads |
| `spacesim/space_element.py` | Spacecraft physics model and UDP node |
| `spacesim/ground_element.py` | Ground station node and interactive console |
| `run_demo.py` | Runs both elements together through a scripted pass |

## Requirements

Python 3.10 or newer. No third-party packages are needed.

## Run

**Scripted demo** with both elements in one process:

```powershell
py run_demo.py
py run_demo.py --seconds 30 --time-scale 600 --loss 0.1
```

**Two terminals**, one per element:

```powershell
# terminal 1
py -m spacesim.space_element --port 5000 --ground-port 5001

# terminal 2
py -m spacesim.ground_element --port 5001 --space-port 5000
ground> mode science
ground> heater on
ground> status
```

**Web GUI** (both elements in one process, served on localhost only):

```powershell
py -m spacesim.web                 # then open http://127.0.0.1:8000/
py -m spacesim.web --port 8080 --time-scale 600 --loss 0.1
```

The page shows live telemetry, battery and temperature charts with limit lines, a ground
track, link statistics, alarms, and buttons for each telecommand. It uses only the standard
library. It needs the Python server, so it cannot be hosted on GitHub Pages. This change does
not touch `packets.py` or `messages.py`, so the wire format is unchanged.

## Test

```powershell
py -m unittest discover -s tests -t . -v
```

## Commands

| Opcode | Argument | Effect |
|---|---|---|
| NOOP | none | Connectivity check |
| SET_MODE | 0 SAFE, 1 NOMINAL, 2 SCIENCE | SCIENCE is rejected below 40% battery |
| HEATER_ON / HEATER_OFF | none | Adds 6 W load, warms the spacecraft |
| RESET_COUNTERS | none | Clears command counters |

## Porting to Java and C

The wire format is fixed-size, big-endian and language-neutral. A Java or C
implementation of either element can replace the Python one, as long as it uses the
same header layout, APIDs, payload layouts and CRC-16/CCITT-FALSE checksum
documented in `packets.py` and `messages.py`.
