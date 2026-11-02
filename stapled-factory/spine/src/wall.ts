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

  let output: WallOutput;
  try {
    const { stdout } = await execFileP("python3", args, { cwd: PROJECT_ROOT });
    output = JSON.parse(stdout) as WallOutput;
  } catch {
    // Process errors and JSON parse errors can embed private evaluator output.
    throw new Error('Wall evaluation unavailable');
  }
  if (!output?.verdict || typeof output.verdict.passed !== 'boolean' ||
      !Number.isFinite(output.verdict.aggregate_pass_rate) || output.verdict.aggregate_pass_rate < 0 ||
      output.verdict.aggregate_pass_rate > 1 || !output.receipt ||
      typeof output.receipt !== 'object' || Array.isArray(output.receipt) ||
      !(['tokens_in','tokens_out','requests','retries','wall_clock_ms'] as const)
        .every(key => Number.isSafeInteger(output.receipt[key]) && output.receipt[key] >= 0) ||
      !(output.receipt.cost_estimate_usd === null || typeof output.receipt.cost_estimate_usd === 'number' &&
        Number.isFinite(output.receipt.cost_estimate_usd) && output.receipt.cost_estimate_usd >= 0) ||
      typeof output.receipt.ceiling_violation !== 'string') {
    throw new Error('Invalid wall output');
  }
  // The evaluator output is private: its scenario IDs and failure text may
  // contain holdout paths or expected values. Return only aggregate feedback.
  const {tokens_in,tokens_out,requests,retries,wall_clock_ms,cost_estimate_usd} = output.receipt;
  return {verdict:{passed:output.verdict.passed,aggregate_pass_rate:output.verdict.aggregate_pass_rate,
    failure_message:output.verdict.passed ? '' : 'A holdout scenario did not meet its quorum',per_scenario:{}},
    receipt:{tokens_in,tokens_out,requests,retries,wall_clock_ms,tier:req.tier ?? 'unknown',
      pricing_mode:req.pricingMode ?? 'subscription',cost_estimate_usd,
      ceiling_violation:output.receipt.ceiling_violation ? 'usage ceiling exceeded' : ''}};
}
