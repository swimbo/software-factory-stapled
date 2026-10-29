// Operator validation authority; the key never belongs in a worker environment.
import { createHmac, timingSafeEqual } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
export interface ValidationEvidence {
  unit_id: string; attempt_id: string; git_revision: string;
  deployment_id: string; artifact_digest: string; scenario_digest: string;
  passed: boolean;
  scope: "disposable-candidate";
  security_engines: Record<string, string>;
}
export interface SignedValidation { payload: string; signature: string }
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
  return result;
}
