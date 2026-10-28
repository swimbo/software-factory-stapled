// WorkUnit Virtual Object — the durable control-plane state for one unit of work.
//
// This is the "claims / leases / stages / receipts" state the architecture calls
// the orchestration spine. It is a Restate Virtual Object (single-writer, keyed
// by unit id), so stage transitions and receipts are serialized and durable.
//
// NOTE on leases: `claim` uses wall-clock for lease expiry, which is a known
// simplification of durable execution. A production version should expire leases
// with a durable `ctx.sleep` timer (see README "effect boundary" caveats) rather
// than comparing `Date.now()`, so expiry is replayable. Kept simple for the MVP.
import * as restate from "@restatedev/restate-sdk";

import { BuildEvidence, Claim, Receipt, Stage, WorkUnitState } from "./types.js";
import { conversationId } from "./build.js";

const CLAIM_LEASE_MS = 10 * 60 * 1000; // 10 minutes

const KEY = "state";

function empty(): WorkUnitState {
  return { stage: "unclaimed", build: null, claim: null, receipt: null, attempts: 0 };
}

export const workUnit = restate.object({
  name: "WorkUnit",

  handlers: {
    // Claim this unit for a worker. Returns false if someone else holds a live lease.
    claim: async (ctx: restate.ObjectContext, workerId: string): Promise<boolean> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "unclaimed") return false;
      const now = Date.now();
      if (s.claim && s.claim.lease_until_ms > now && s.claim.worker_id !== workerId) {
        return false; // held by another worker with a live lease
      }
      s.claim = { worker_id: workerId, lease_until_ms: now + CLAIM_LEASE_MS };
      s.stage = "claimed";
      ctx.set(KEY, s);
      return true;
    },

    recordRecoveryContext: async (ctx: restate.ObjectContext, recovery: NonNullable<WorkUnitState["recovery"]>): Promise<void> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "building" || s.recovery || !recovery.request || recovery.request.unitId !== ctx.key) throw new restate.TerminalError("Invalid recovery context");
      s.recovery = recovery;
      ctx.set(KEY, s);
    },

    // Preserve the remote identity required to reconcile an uncertain dispatch.
    recordBuild: async (ctx: restate.ObjectContext, build: BuildEvidence): Promise<void> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "building" || !s.recovery ||
          build.attempt_id !== `${ctx.key}:0` ||
          (build.conversation_id !== undefined && build.conversation_id !== conversationId(build.attempt_id)) ||
          !["completed", "unknown", "blocked", "failed", "skipped"].includes(build.status) ||
          (s.build && (s.build.attempt_id !== build.attempt_id || s.build.status !== build.status ||
            (s.build.conversation_id && s.build.conversation_id !== build.conversation_id) ||
            (s.build.git_revision && s.build.git_revision !== build.git_revision)))) {
        throw new restate.TerminalError("Invalid build evidence");
      }
      s.build = build;
      ctx.set(KEY, s);
    },

    // Complete recovery atomically so a late observation cannot overwrite a
    // different attempt or move a unit backwards from a later stage.
    completeReconciliation: async (ctx: restate.ObjectContext, build: BuildEvidence): Promise<boolean> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "needs_reconciliation" || !s.build || s.successor ||
          s.build.attempt_id !== build.attempt_id ||
          s.build.conversation_id !== build.conversation_id ||
          build.status !== "completed" || !build.git_revision ||
          !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(build.git_revision)) return false;
      s.build = build;
      s.stage = "needs_validation";
      ctx.set(KEY, s);
      return true;
    },

    // Serialize one successor intent against late reconciliation. The worker
    // still has to establish fencing, baseline, runtime and budget authority.
    beginSuccessor: async (ctx: restate.ObjectContext): Promise<boolean> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "needs_reconciliation" || !s.recovery || !s.build ||
          s.build.status !== "unknown" || s.build.attempt_id !== `${ctx.key}:0` ||
          s.build.conversation_id !== conversationId(s.build.attempt_id) || s.successor) return false;
      const attempt_id = `${s.build.attempt_id}:successor`;
      s.successor = { attempt_id, conversation_id: conversationId(attempt_id), status: "reserved" };
      ctx.set(KEY, s);
      return true;
    },

    recordSuccessor: async (ctx: restate.ObjectContext, build: BuildEvidence): Promise<boolean> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "needs_reconciliation" || !s.successor || s.successor.status !== "reserved" ||
          s.successor.attempt_id !== build.attempt_id || s.successor.conversation_id !== build.conversation_id ||
          !["blocked", "unknown", "failed", "completed"].includes(build.status) ||
          (build.status === "completed" && !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(build.git_revision ?? ""))) return false;
      s.successor = build;
      if (build.status === "completed") s.stage = "needs_validation";
      ctx.set(KEY, s);
      return true;
    },

    completeSuccessorReconciliation: async (ctx: restate.ObjectContext, build: BuildEvidence): Promise<boolean> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      if (s.stage !== "needs_reconciliation" || !s.successor || s.successor.status !== "unknown" ||
          s.successor.attempt_id !== build.attempt_id || s.successor.conversation_id !== build.conversation_id ||
          build.status !== "completed" || !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(build.git_revision ?? "")) return false;
      s.successor = build;
      s.stage = "needs_validation";
      ctx.set(KEY, s);
      return true;
    },

    // Record a receipt (verdict + usage) bound to a git revision; bump attempts.
    recordReceipt: async (ctx: restate.ObjectContext, receipt: Receipt): Promise<void> => {
      // Caller-supplied receipts cannot establish a trusted target binding.
      throw new restate.TerminalError("Receipt validation is not enabled");
    },

    // Explicit stage transition (e.g. merged / deployed / failed).
    transition: async (ctx: restate.ObjectContext, stage: Stage): Promise<void> => {
      const s = (await ctx.get<WorkUnitState>(KEY)) ?? empty();
      const valid = (s.stage === "claimed" && stage === "building") ||
        (s.stage === "building" && stage === "needs_reconciliation" && !!s.build &&
          ["unknown", "blocked"].includes(s.build.status) && !!s.build.conversation_id) ||
        (s.stage === "building" && stage === "needs_validation" && !!s.build &&
          s.build.status === "completed" && /^[0-9a-f]{40}([0-9a-f]{24})?$/.test(s.build.git_revision ?? "")) ||
        (s.stage === "building" && stage === "failed" && !!s.build &&
          ["failed", "skipped"].includes(s.build.status));
      if (!valid) throw new restate.TerminalError("Invalid stage transition");
      s.stage = stage;
      ctx.set(KEY, s);
    },

    getState: async (ctx: restate.ObjectContext): Promise<WorkUnitState> => {
      return (await ctx.get<WorkUnitState>(KEY)) ?? empty();
    },
  },
});
