// The gate service — human-in-the-loop approvals via Restate awakeables.
//
// `request` blocks durably on a human decision (survives restarts, waits
// indefinitely). A human resolves it later via `resolve` (from a dashboard or
// CLI). The decision + approver + timestamps land in the ApprovalStore VO as
// the audit record.
import * as restate from "@restatedev/restate-sdk";

import { approvalStore } from "./approval_store.js";
import { Approval, GateType } from "./types.js";

export interface RequestApproval {
  requestId: string;
  gateType: GateType;
  context: string;
}

export interface ResolveApproval {
  requestId: string;
  decision: string; // "approved" | "rejected"
  approver: string;
}

export const gate = restate.service({
  name: "gate",

  handlers: {
    request: async (ctx: restate.Context, req: RequestApproval): Promise<string> => {
      const store = ctx.objectClient(approvalStore, req.requestId);
      const { id, promise } = ctx.awakeable<string>();
      await store.register({
        requestId: req.requestId,
        gateType: req.gateType,
        awakeableId: id,
        context: req.context,
      });
      return await promise; // blocks until a human resolves it
    },

    resolve: async (ctx: restate.Context, req: ResolveApproval): Promise<void> => {
      const store = ctx.objectClient(approvalStore, req.requestId);
      const awakeableId = await store.getAwakeableId();
      if (!awakeableId) {
        throw new Error(`no pending approval for ${req.requestId}`);
      }
      await store.resolve({ decision: req.decision, approver: req.approver });
      ctx.resolveAwakeable(awakeableId, req.decision);
    },

    get: async (ctx: restate.Context, requestId: string): Promise<Approval | null> => {
      return ctx.objectClient(approvalStore, requestId).get();
    },
  },
});
