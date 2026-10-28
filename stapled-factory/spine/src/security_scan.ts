// The deterministic security-pipeline effect — scans a diff, non-LLM.
//
// Shells out to `security/pipeline_cli.py` (secret scan + SAST-lite +
// dependency change + security-relevance classification). The caller wraps it
// in `ctx.run`. Verdict drives tiered-dark routing: "fail" blocks, "needs_review"
// routes to a human `security_review` gate, "pass" runs dark.
import { execFile } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { ScanReport } from "./types.js";

const execFileP = promisify(execFile);
const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export interface SecurityScanRequest {
  diffText?: string;
  revision?: string; // git revision to derive the diff from
  paths?: string[];
  targetRepo?: string; // the repo the worker changed (scan scope)
  skip?: boolean; // worker produced no diff — nothing to scan
}

export async function securityScan(req: SecurityScanRequest): Promise<ScanReport> {
  if (req.skip) {
    // The worker made no changes, so there is no diff to scan: scanning the
    // factory's own tree instead would gate on our own fixtures (scanner noise).
    return {
      verdict: "pass",
      security_relevant: false,
      findings: [],
      engines: { note: "skipped: worker produced no diff" },
    } as ScanReport;
  }

  let diffText = req.diffText ?? "";
  if (req.revision) {
    try {
      const { stdout } = await execFileP("git", ["show", req.revision], {
        cwd: req.targetRepo ? path.resolve(PROJECT_ROOT, req.targetRepo) : PROJECT_ROOT,
      });
      diffText = stdout;
    } catch {
      diffText = "";
    }
  }

  const dir = mkdtempSync(path.join(tmpdir(), "secscan-"));
  const diffFile = path.join(dir, "diff.txt");
  writeFileSync(diffFile, diffText);

  const args = [path.join(PROJECT_ROOT, "security", "pipeline_cli.py"), "--diff-file", diffFile];
  for (const p of req.paths ?? []) {
    args.push("--paths", p);
  }

  const { stdout } = await execFileP("python3", args, { cwd: PROJECT_ROOT });
  return JSON.parse(stdout) as ScanReport;
}
