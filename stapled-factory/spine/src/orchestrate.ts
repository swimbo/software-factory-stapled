// The orchestrate workflow — the outer control loop for one unit of work.
//
// Claim and build one unit, then hold its revision for a trusted validation
// binding. Ambiguous outcomes are reconciled without repeating the dispatch.
// Control-plane state lives in the WorkUnit Virtual Object.
import * as restate from "@restatedev/restate-sdk";

import { buildStep, reconcileBuildStep, reconcileSuccessorStep, restartBuildStep, successorPreflight, conversationId } from "./build.js";
import { workUnit } from "./state.js";
import { Receipt } from "./types.js";
import { WallRequest } from "./wall.js";

const MAX_ATTEMPTS = 3;

export interface OrchestrateRequest {
  unitId: string;
  target: string; // ephemeral deploy URL the wall evaluates against
  scenarios: string; // path to holdout scenarios (out of the worker's reach)
  targetRepo?: string; // worktree the agent works in
  taskFile?: string; // path to the spec / work-package text
  paths?: string[]; // changed file paths (for security-relevance classification)
  wall?: WallRequest;
}

export const orchestrate = restate.workflow({
  name: "orchestrate",

  handlers: {
    run: async (ctx: restate.WorkflowContext, req: OrchestrateRequest): Promise<Receipt | null> => {
      // Fail fast on malformed input (a retried 500 wastes attempts against a wall).
      const missing = (["unitId", "target", "scenarios", "taskFile"] as const).filter(
        (k) => !req[k] || (typeof req[k] === "string" && !(req[k] as string).trim()),
      );
      if (missing.length > 0) {
        throw new restate.TerminalError(
          `orchestrate.run missing required fields: ${missing.join(", ")}`,
        );
      }

      if (req.unitId !== ctx.key) {
        throw new restate.TerminalError("unitId must match the workflow key");
      }
      const unit = ctx.objectClient(workUnit, req.unitId);

      // 1. Claim the unit. If another worker holds a live lease, bail.
      const claimed = await unit.claim("spine");
      if (!claimed) {
        return null;
      }
      await unit.transition("building");

      // Journal the store location so replay cannot silently select a different database.
      const attemptDb = await ctx.run(() => process.env.FACTORY_ATTEMPT_DB ?? "");

      const attemptAnchor = await ctx.run(() => process.env.FACTORY_ATTEMPT_ANCHOR ?? "");
      await unit.recordRecoveryContext({ request: req, attemptDb, attemptAnchor });

      // 2. Build + validate, retrying up to MAX_ATTEMPTS.
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        const built = await ctx.run(() =>
          buildStep({ attemptId: `${ctx.key}:${attempt}`, attemptDb, attemptAnchor, targetRepo: req.targetRepo ?? "", taskFile: req.taskFile ?? "" }),
        );
        await unit.recordBuild({
          attempt_id: `${ctx.key}:${attempt}`, status: built.status,
          ...(built.conversation_id ? { conversation_id: built.conversation_id } : {}),
          ...(built.error ? { error: built.error } : {}),
        });
        // An ambiguous dispatch must be reconciled; do not retry or claim failure.
        if (built.status === "unknown" || built.status === "blocked") {
          // A replay can observe a now-blocked runtime after an earlier process
          // may have accepted work. Preserve the deterministic identity in both
          // cases and schedule read-only recovery, never another build.
          if (!built.conversation_id) {
            await unit.recordBuild({ attempt_id: `${ctx.key}:${attempt}`, status: built.status,
              conversation_id: conversationId(`${ctx.key}:${attempt}`), ...(built.error ? { error: built.error } : {}) });
          }
          await unit.transition("needs_reconciliation");
          // A blocked configuration may never become usable (for example an
          // empty journaled store path). Leave it visible for intervention
          // rather than creating a permanent polling invocation.
          if (built.status === "unknown") ctx.workflowSendClient(reconcile, ctx.key).run();
          return null;
        }
        // A skipped or failed worker has not produced a build to validate.
        if (built.status !== "completed") {
          await unit.transition("failed");
          return null;
        }
        // A target URL supplied by the caller does not prove it serves this revision.
        // Keep the revision for a future trusted deployment/validation binding.
        await unit.recordBuild({
          attempt_id: `${ctx.key}:${attempt}`, status: "completed",
          ...(built.conversation_id ? { conversation_id: built.conversation_id } : {}),
          git_revision: built.git_revision,
        });
        await unit.transition("needs_validation");
        return null;
      }

      await unit.transition("failed");
      return null;
    },
  },
});

// One keyed workflow per original unit. The input contains no evidence or paths:
// only the persisted original attempt can be queried. Never redispatch here.
export const reconcile = restate.workflow({
  name: "reconcileBuild",
  handlers: {
    run: async (ctx: restate.WorkflowContext): Promise<Receipt | null> => {
      const unit = ctx.objectClient(workUnit, ctx.key);
      const state = await unit.getState();
      if (state.stage !== "needs_reconciliation" || !state.build || !state.recovery) {
        throw new restate.TerminalError("No original attempt available for reconciliation");
      }
      const { request: req, attemptDb, attemptAnchor } = state.recovery;
      if (req.unitId !== ctx.key || !state.build.conversation_id ||
          !Array.from({ length: MAX_ATTEMPTS }, (_, i) => `${ctx.key}:${i}`).includes(state.build.attempt_id) ||
          state.build.conversation_id !== conversationId(state.build.attempt_id)) {
        throw new restate.TerminalError("Stored attempt identity mismatch");
      }
      // A pending or uncertain outcome is polled durably; it is never redispatched.
      for (let poll = 0; poll < 20; poll++) {
        const result = await ctx.run(() => reconcileBuildStep({
          attemptId: state.build!.attempt_id, attemptDb, attemptAnchor: attemptAnchor ?? "",
          targetRepo: req.targetRepo ?? "", taskFile: req.taskFile ?? "",
        }));
        if (result.status === "completed" && /^[0-9a-f]{40}([0-9a-f]{24})?$/.test(result.git_revision)) {
          if (result.conversation_id !== state.build.conversation_id) {
            throw new restate.TerminalError("Reconciled conversation identity mismatch");
          }
          await unit.completeReconciliation({attempt_id: state.build.attempt_id, status: "completed", conversation_id: result.conversation_id, git_revision: result.git_revision});
          return null;
        }
        // A blocked runtime cannot currently be queried. Leave the persisted
        // attempt in needs_reconciliation for manual recovery and stop polling.
        // Restarting this completed keyed workflow is not yet supported.
        if (result.status === "blocked") return null;
        // Unknown does not prove the remote tool had no effects.
        await ctx.sleep(30_000);
      }
      // The attempt remains unresolved for manual recovery after the bounded
      // polling window. There is no supported keyed workflow restart yet.
      return null;
    },
  },
});

// The workflow is keyed and cannot be restarted once finished. This separate
// keyed object offers a repeatable single observation without any dispatch or
// caller-supplied paths. Its exclusive handler serializes manual observations.
export const reconciliation = restate.object({
  name: "Reconciliation",
  handlers: {
    // This handler accepts no
    // caller-provided evidence or runtime configuration. The worker must use a
    // durable one-shot claim because ctx.run may re-execute after a crash.
    restart: async (ctx: restate.ObjectContext): Promise<string> => {
      const unit = ctx.objectClient(workUnit, ctx.key);
      const state = await unit.getState();
      if (state.stage !== "needs_reconciliation" || !state.build || !state.recovery ||
          state.recovery.request.unitId !== ctx.key || state.build.attempt_id !== `${ctx.key}:0` ||
          state.build.conversation_id !== conversationId(state.build.attempt_id)) {
        throw new restate.TerminalError("No original attempt available for restart");
      }
      const {request: req, attemptDb, attemptAnchor} = state.recovery;
      const workerRequest = {attemptId: state.build.attempt_id, attemptDb,
        attemptAnchor: attemptAnchor ?? "", targetRepo: req.targetRepo ?? "", taskFile: req.taskFile ?? ""};
      // Preflight is advisory, and is journaled for deterministic replay. The
      // worker must check its prerequisites again immediately before dispatch.
      if (!await ctx.run(() => successorPreflight(workerRequest))) return "blocked";
      if (!await unit.beginSuccessor()) return "not_started";
      const successorId = `${state.build.attempt_id}:successor`;
      const result = await ctx.run(() => restartBuildStep(workerRequest));
      const evidence = {attempt_id: successorId, conversation_id: conversationId(successorId),
        status: result.status, ...(result.status === "completed" ? {git_revision: result.git_revision} : {}),
        ...(result.error ? {error: result.error} : {})};
      if (!await unit.recordSuccessor(evidence)) return "stale";
      return result.status === "completed" ? "needs_validation" : result.status;
    },
    observe: async (ctx: restate.ObjectContext): Promise<string> => {
      const unit = ctx.objectClient(workUnit, ctx.key);
      const state = await unit.getState();
      if (state.stage !== "needs_reconciliation" || !state.build || !state.recovery) {
        throw new restate.TerminalError("No original attempt available for reconciliation");
      }
      const { request: req, attemptDb, attemptAnchor } = state.recovery;
      if (req.unitId !== ctx.key || !state.build.conversation_id ||
          !Array.from({ length: MAX_ATTEMPTS }, (_, i) => `${ctx.key}:${i}`).includes(state.build.attempt_id) ||
          state.build.conversation_id !== conversationId(state.build.attempt_id)) {
        throw new restate.TerminalError("Stored attempt identity mismatch");
      }
      if (state.successor) {
        const attemptId = `${state.build.attempt_id}:successor`;
        if (state.successor.attempt_id !== attemptId || state.successor.conversation_id !== conversationId(attemptId)) {
          throw new restate.TerminalError("Stored successor identity mismatch");
        }
        if (state.successor.status !== "unknown") return state.successor.status;
        const result = await ctx.run(() => reconcileSuccessorStep({ attemptId: state.build!.attempt_id,
          attemptDb, attemptAnchor: attemptAnchor ?? "", targetRepo: req.targetRepo ?? "", taskFile: req.taskFile ?? "" }));
        if (result.status !== "completed") return result.status;
        if (result.conversation_id !== state.successor.conversation_id ||
            !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(result.git_revision)) {
          throw new restate.TerminalError("Reconciled successor identity mismatch");
        }
        const accepted = await unit.completeSuccessorReconciliation({attempt_id: attemptId,
          status: "completed", conversation_id: result.conversation_id, git_revision: result.git_revision});
        return accepted ? "needs_validation" : "stale";
      }
      const result = await ctx.run(() => reconcileBuildStep({ attemptId: state.build!.attempt_id,
        attemptDb, attemptAnchor: attemptAnchor ?? "", targetRepo: req.targetRepo ?? "", taskFile: req.taskFile ?? "" }));
      if (result.status !== "completed") return result.status;
      if (result.conversation_id !== state.build.conversation_id ||
          !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(result.git_revision)) {
        throw new restate.TerminalError("Reconciled result identity mismatch");
      }
      const accepted = await unit.completeReconciliation({attempt_id: state.build.attempt_id,
        status: "completed", conversation_id: result.conversation_id, git_revision: result.git_revision});
      return accepted ? "needs_validation" : "stale";
    },
  },
});
