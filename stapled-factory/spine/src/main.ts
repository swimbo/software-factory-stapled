// Endpoint — binds the objects, services, and workflow.
// Run with `npm run dev` against a local Restate server (see README).
import * as restate from "@restatedev/restate-sdk";
import { createServer } from "node:http2";

import { approvalStore } from "./approval_store.js";
import { gate } from "./gate.js";
import { orchestrate, reconcile, reconciliation } from "./orchestrate.js";
import { workUnit } from "./state.js";

// Verify requests originate from the configured Restate cluster. Ingress and
// admin authorization remain separate requirements.
const identityKeys = (process.env.FACTORY_RESTATE_IDENTITY_KEYS ?? "")
  .split(",").map(key => key.trim()).filter(Boolean);
if (identityKeys.length === 0 || identityKeys.some(key => !/^publickeyv1_[A-Za-z0-9]+$/.test(key))) {
  throw new Error("FACTORY_RESTATE_IDENTITY_KEYS must contain Restate request identity public keys");
}
const port = Number(process.env.FACTORY_ENDPOINT_PORT ?? "9080");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid endpoint port");
createServer(restate.createEndpointHandler({ identityKeys, services: [
  workUnit, approvalStore, gate, orchestrate, reconcile, reconciliation,
] })).listen(port, "127.0.0.1");
