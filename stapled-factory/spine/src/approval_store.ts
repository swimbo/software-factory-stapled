// ApprovalStore — the durable store backing the human gates.
//
// A Virtual Object keyed by requestId. Each key holds one approval record
// (gate type, status, decision, the awakeable handle, timestamps). This is the
// audit record the architecture calls "ApprovalGate": every approval/rejection
// is logged with who decided and when.
//
// In production this also backs a Postgres audit table (for cross-system query);
// for this increment Restate itself is the durable store.
import * as restate from "@restatedev/restate-sdk";

import { Approval, GateType } from "./types.js";

const KEY = "approval";

export const approvalStore = restate.object({
  name: "ApprovalStore",

  handlers: {
    register: async (
      ctx: restate.ObjectContext,
      req: { requestId: string; gateType: GateType; awakeableId: string; context: string },
    ): Promise<void> => {
      ctx.set(KEY, {
        requestId: req.requestId,
        gateType: req.gateType,
        status: "pending",
        decision: "",
        approver: null,
        context: req.context,
        awakeableId: req.awakeableId,
        requestedAtMs: Date.now(),
        resolvedAtMs: null,
      } satisfies Approval);
    },

    getAwakeableId: async (ctx: restate.ObjectContext): Promise<string | null> => {
      const a = await ctx.get<Approval>(KEY);
      return a && a.status === "pending" ? a.awakeableId : null;
    },

    resolve: async (
      ctx: restate.ObjectContext,
      req: { decision: string; approver: string },
    ): Promise<void> => {
      const a = await ctx.get<Approval>(KEY);
      if (!a || a.status !== "pending") return;
      a.status = req.decision === "approved" ? "approved" : "rejected";
      a.decision = req.decision;
      a.approver = req.approver;
      a.resolvedAtMs = Date.now();
      ctx.set(KEY, a);
    },

    get: async (ctx: restate.ObjectContext): Promise<Approval | null> => {
      return (await ctx.get<Approval>(KEY)) ?? null;
    },
  },
});
