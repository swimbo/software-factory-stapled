// Operator validation authority; the key never belongs in a worker environment.
import { createHmac, timingSafeEqual } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import type { Receipt } from './types.js';
export interface ValidationEvidence {
  unit_id: string; attempt_id: string; git_revision: string;
  deployment_id: string; artifact_digest: string; scenario_digest: string;
  passed: boolean;
  scope: "disposable-candidate";
  security_engines: Record<string, string>;
  source_archive_digest?: string;
  checks_digest?: string;
  build_manifest_digest?: string;
  receipt?: Receipt;
}
export interface SignedValidation { payload: string; signature: string }

function validateReceipt(result: ValidationEvidence): void {
  const receipt = result.receipt;
  if (receipt === undefined) return; // Retain the historical metadata-only contract.
  if (!receipt || receipt.git_revision !== result.git_revision ||
      !/^[a-f0-9]{64}$/.test(result.checks_digest ?? '') ||
      !/^[a-f0-9]{64}$/.test(result.source_archive_digest ?? '') ||
      receipt.verdict?.passed !== true ||
      !Number.isFinite(receipt.verdict.aggregate_pass_rate) ||
      receipt.verdict.aggregate_pass_rate < .9 || receipt.verdict.aggregate_pass_rate > 1 ||
      typeof receipt.verdict.failure_message !== 'string' ||
      !receipt.verdict.per_scenario || Array.isArray(receipt.verdict.per_scenario) ||
      typeof receipt.verdict.per_scenario !== 'object' ||
      Object.values(receipt.verdict.per_scenario).some(value => typeof value !== 'boolean')) {
    throw Error('Invalid revision-bound wall receipt');
  }
  const usage = receipt.usage;
  const counters = ['tokens_in', 'tokens_out', 'requests', 'retries', 'wall_clock_ms'] as const;
  if (!usage || !counters.every(key => {
    const value = usage[key];
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  }) || typeof usage.tier !== 'string' || !['subscription', 'metered'].includes(usage.pricing_mode) ||
      usage.ceiling_violation !== '' ||
      !(usage.cost_estimate_usd === null || typeof usage.cost_estimate_usd === 'number' &&
        Number.isFinite(usage.cost_estimate_usd) && usage.cost_estimate_usd >= 0)) {
    throw Error('Invalid wall usage receipt');
  }
}
export function verifyValidation(value: SignedValidation): ValidationEvidence {
  const path = process.env.FACTORY_VALIDATION_KEY_FILE;
  if (!path) throw Error('Validation authority unavailable');
  const st = lstatSync(path);
  if (!st.isFile() || st.isSymbolicLink() || st.uid !== process.getuid?.() || (st.mode & 0o077)) throw Error('Unsafe validation key');
  const key = readFileSync(path);
  if (key.length < 32 || key.length > 4096) throw Error('Invalid validation key');
  if (typeof value?.payload !== 'string' || value.payload.length > 16384 || !/^[a-f0-9]{64}$/.test(value.signature ?? '')) throw Error('Invalid validation envelope');
  const expected = createHmac('sha256', key).update(value.payload).digest();
  if (!timingSafeEqual(expected, Buffer.from(value.signature, 'hex'))) throw Error('Invalid validation signature');
  const result = JSON.parse(value.payload) as ValidationEvidence;
  if (!result || typeof result.unit_id !== 'string' || typeof result.attempt_id !== 'string' ||
      !/^[a-f0-9]{40}$/.test(result.git_revision) || !/^[a-f0-9]{64}$/.test(result.scenario_digest) ||
      !/^sha256:[a-f0-9]{64}$/.test(result.artifact_digest) || typeof result.deployment_id !== 'string' || !result.deployment_id ||
      result.passed !== true || result.scope !== "disposable-candidate" ||
      !result.security_engines || typeof result.security_engines !== "object") throw Error('Invalid validation evidence');
  if ((result.source_archive_digest !== undefined || result.checks_digest !== undefined) &&
      (!/^[a-f0-9]{64}$/.test(result.source_archive_digest ?? '') ||
       !/^[a-f0-9]{64}$/.test(result.checks_digest ?? ''))) throw Error('Invalid validation check binding');
  if (result.build_manifest_digest !== undefined &&
      !/^[a-f0-9]{64}$/.test(result.build_manifest_digest)) throw Error('Invalid build manifest binding');
  validateReceipt(result);
  return result;
}
