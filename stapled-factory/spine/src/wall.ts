// The wall effect — invokes the isolated Python holdout evaluator.
//
// This is a non-deterministic effect (runs an external process / hits a target),
// so it is wrapped in `ctx.run` by the caller. It never sees the generator's
// prompt or scenario text; it only receives an opaque target + scenario paths.
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { promisify } from "node:util";

import { UsageRecord, Verdict } from "./types.js";

const execFileP = promisify(execFile);

// Project root = spine/.. (the stapled-factory/ dir holding both wall/ and spine/).
const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export interface WallOutput {
  verdict: Verdict;
  receipt: UsageRecord;
}

export interface WallRequest {
  target: string;
  scenarios: string;
  config?: string;
  tier?: string;
  runs?: number;
  pricingMode?: "subscription" | "metered";
  costUsd?: number;
}

export async function runWall(req: WallRequest): Promise<WallOutput> {
  const args = [
    path.join(PROJECT_ROOT, "wall", "evaluator", "cli.py"),
    "--target", req.target,
    "--scenarios", req.scenarios,
    "--runs", String(req.runs ?? 3),
    "--pricing-mode", req.pricingMode ?? "subscription",
  ];
  if (req.config && req.tier) args.push("--config", req.config, "--tier", req.tier);
  if (req.costUsd !== undefined) args.push("--cost-usd", String(req.costUsd));

  const { stdout } = await execFileP("python3", args, { cwd: PROJECT_ROOT });
  return JSON.parse(stdout) as WallOutput;
}
