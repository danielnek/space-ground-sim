// In-memory stand-in for the UDP link: wires a SpaceElement and a GroundElement together.
// Packets are delivered asynchronously, like datagrams, and always as raw bytes.

import { GroundElement } from "./groundElement.js";
import { SpaceElement } from "./spaceElement.js";

/**
 * @param {object} options
 * @param {(bytes: Uint8Array, direction: "uplink" | "downlink") => void} [options.onPacket] packet tap
 */
export function createSimulation({ timeScale = 300, downlinkLoss = 0.05, seed, onPacket = () => {}, onEvent } = {}) {
  const ground = new GroundElement({
    sendUplink: (bytes) => {
      onPacket(bytes, "uplink");
      queueMicrotask(() => space.receive(bytes));
    },
  });
  const space = new SpaceElement({
    timeScale,
    downlinkLoss,
    seed,
    onEvent,
    sendDownlink: (bytes) => {
      onPacket(bytes, "downlink");
      queueMicrotask(() => ground.handlePacket(bytes));
    },
  });
  return { ground, space };
}
