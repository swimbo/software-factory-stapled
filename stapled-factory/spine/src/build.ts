// The build effect — dispatch OpenHands (via the Agent Server) to produce a diff.
//
// Shells out to the operator-configured `worker/worker.py`. The caller wraps
// this nondeterministic effect in `ctx.run`; the worker fails closed without
// its private runtime configuration.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { promisify } from "node:util";

const execFileP = promisify(execFile);

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// RFC 4122 UUIDv5, matching worker.execute_attempt's NAMESPACE_URL identity.
export function conversationId(attemptId: string): string {
  const namespace = Buffer.from("6ba7b8119dad11d180b400c04fd430c8", "hex");
  const bytes = createHash("sha1").update(namespace).update("stapled-factory:" + attemptId, "utf8").digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export interface BuildResult {
  git_revision: string;
  status: string;
  error?: string;
  conversation_id?: string;
}

export interface BuildRequest {
  attemptId: string; // stable per workflow attempt across replay
  attemptDb: string; // durable path journaled before dispatch
  attemptAnchor: string; // independently retained watermark
  targetRepo: string; // worktree path the agent works in
  taskFile: string; // path to the spec / work-package text
  agentServer?: string; // override OPENHANDS_AGENT_SERVER_URL
  model?: string; // override OPENHANDS_MODEL
  baseUrl?: string; // route through Bifrost (OPENHANDS_BASE_URL)
}

export async function buildStep(req: BuildRequest): Promise<BuildResult> {
  return invokeWorker(req, "build");
}

export async function reconcileBuildStep(req: BuildRequest): Promise<BuildResult> {
  return invokeWorker(req, "reconcile");
}

export async function reconcileSuccessorStep(req: BuildRequest): Promise<BuildResult> {
  return invokeWorker(req, "successor-reconcile");
}

// Advisory only: Restate can replay this answer. The worker must independently
// recheck every gate at dispatch time, before performing any external effect.
export async function successorPreflight(req: BuildRequest): Promise<boolean> {
  const result = await invokeWorker(req, "successor-preflight");
  return result.status === "ready";
}

// A successor shares the original attempt's budget. The worker must enforce its
// durable one-shot claim before provisioning or dispatch; Restate effects can replay.
export async function restartBuildStep(req: BuildRequest): Promise<BuildResult> {
  return invokeWorker(req, "successor");
}

async function invokeWorker(req: BuildRequest, mode: "build" | "reconcile" | "successor" | "successor-reconcile" | "successor-preflight"): Promise<BuildResult> {
  const attemptDb = req.attemptDb;
  if (!attemptDb || !path.isAbsolute(attemptDb)) {
    return { status: "blocked", git_revision: "", error: "FACTORY_ATTEMPT_DB must be an absolute durable path" };
  }
  const anchor = req.attemptAnchor;
  if (!anchor || !path.isAbsolute(anchor) || path.dirname(path.resolve(anchor)) === path.dirname(path.resolve(attemptDb))) {
    return { status: "blocked", git_revision: "", error: "independent absolute attempt anchor required" };
  }
  try {
    if (!statSync(anchor).isFile()) throw new Error("anchor missing");
    if (!statSync(attemptDb).isFile()) throw new Error("not a regular file");
  } catch {
    return { status: "blocked", git_revision: "", error: "attempt store must already exist" };
  }
  const args = [
    path.join(PROJECT_ROOT, "worker", "worker.py"),
    "--attempt-id", req.attemptId,
    "--attempt-db", attemptDb,
    "--attempt-anchor", anchor,
    "--task-file", req.taskFile,
    "--repo", req.targetRepo,
  ];
  if (mode === "reconcile") args.push("--reconcile-only");
  if (mode === "successor") args.push("--successor-only");
  if (mode === "successor-reconcile") args.push("--successor-reconcile-only");
  if (mode === "successor-preflight") args.push("--successor-preflight-only");
  const expectedConversation = conversationId(mode.startsWith("successor") ? `${req.attemptId}:successor` : req.attemptId);
  if (req.agentServer) args.push("--agent-server", req.agentServer);
  if (req.model) args.push("--model", req.model);
  if (req.baseUrl) args.push("--base-url", req.baseUrl);

  try {
    const { stdout } = await execFileP("python3", args, { cwd: PROJECT_ROOT,
      ...(mode === "successor-preflight" ? { timeout: 10_000 } : {}) });
    const result: unknown = JSON.parse(stdout);
    if (typeof result !== "object" || result === null ||
        typeof (result as BuildResult).status !== "string" ||
        typeof (result as BuildResult).git_revision !== "string") throw new Error("malformed worker result");
    const build = result as BuildResult;
    if (!(mode === "successor-preflight" ? ["blocked", "ready"] : ["blocked", "failed", "unknown", "completed"]).includes(build.status)) throw new Error("worker status invalid");
    if (build.conversation_id && build.conversation_id !== expectedConversation) throw new Error("worker identity mismatch");
    if ((build.status === "unknown" || build.status === "completed") && !build.conversation_id) throw new Error("worker identity missing");
    if (build.status === "completed" && !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(build.git_revision)) throw new Error("worker revision invalid");
    return build;
  } catch {
    // The process may have died after acceptance. Never retry this external effect.
    return { status: "unknown", git_revision: "", conversation_id: expectedConversation,
      error: "Worker outcome uncertain; reconcile without redispatch" };
  }
}
