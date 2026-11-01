// ApprovalStore — the durable store backing the human gates.
//
import * as restate from "@restatedev/restate-sdk";

import { Approval, GateType } from "./types.js";

const KEY = "approval";
const GATE_TYPES = new Set<GateType>([
  "initial_prod_deploy", "budget_raise", "destructive_op", "credential_issue", "security_review",
]);

function text(value: unknown, max: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

export function validateApprovalRequestId(value: unknown): asserts value is string {
  if (!text(value, 160)) throw new restate.TerminalError("Invalid approval request identity");
}

export function validateApprovalRequest(req: { requestId: string; gateType: GateType; context: string }): void {
  validateApprovalRequestId(req?.requestId);
  if (!GATE_TYPES.has(req.gateType) || !text(req.context, 16384)) {
    throw new restate.TerminalError("Invalid approval gate or context");
  }
}

export const approvalStore = restate.object({
  name: "ApprovalStore",

  handlers: {
    register: async (
      ctx: restate.ObjectContext,
      req: { requestId: string; gateType: GateType; awakeableId: string; context: string },
    ): Promise<Approval> => {
      validateApprovalRequest(req);
      if (req.requestId !== ctx.key || !text(req.awakeableId, 2048)) {
        throw new restate.TerminalError("Invalid approval object or awakeable identity");
      }
      const existing = await ctx.get<Approval>(KEY);
      if (existing) {
        if (existing.requestId !== req.requestId || existing.gateType !== req.gateType ||
            existing.context !== req.context || existing.awakeableId !== req.awakeableId) {
          throw new restate.TerminalError("Conflicting approval registration");
        }
        return existing;
      }
      const approval: Approval = {
        requestId: req.requestId,
        gateType: req.gateType,
        status: "pending",
        decision: "",
        approver: null,
        context: req.context,
        awakeableId: req.awakeableId,
        requestedAtMs: await ctx.date.now(),
        resolvedAtMs: null,
      };
      ctx.set(KEY, approval);
      return approval;
    },

    getAwakeableId: async (ctx: restate.ObjectContext): Promise<string | null> => {
      const a = await ctx.get<Approval>(KEY);
      return a && a.status === "pending" ? a.awakeableId : null;
    },

    resolve: async (
      ctx: restate.ObjectContext,
      req: { decision: string; approver: string },
    ): Promise<Approval> => {
      if (!req || !["approved", "rejected"].includes(req.decision) || !text(req.approver, 200)) {
        throw new restate.TerminalError("Invalid approval decision or approver");
      }
      const a = await ctx.get<Approval>(KEY);
      if (!a || a.requestId !== ctx.key) throw new restate.TerminalError("No original approval request");
      if (a.status !== "pending") {
        if (a.decision !== req.decision || a.approver !== req.approver) {
          throw new restate.TerminalError("Conflicting approval decision");
        }
        return a;
      }
      a.status = req.decision === "approved" ? "approved" : "rejected";
      a.decision = req.decision;
      a.approver = req.approver;
      a.resolvedAtMs = await ctx.date.now();
      ctx.set(KEY, a);
      return a;
    },

    get: async (ctx: restate.ObjectContext): Promise<Approval | null> => {
      return (await ctx.get<Approval>(KEY)) ?? null;
    },
  },
});
