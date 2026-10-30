// Operator RSA authority is domain-separated from image approvals by payload kind.
import { verify } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
export interface AbandonmentEnvelope { payload: string; signature: string }
export interface AbandonmentEvidence {
  kind: string; unit_id: string; attempt_id: string; conversation_id: string;
  allocation_id: string; worker_id: string; proxy_id: string; store_id: string;
  generation: number; reason: string;
}
export function verifyAbandonment(value: AbandonmentEnvelope): AbandonmentEvidence {
  const key = process.env.FACTORY_IMAGE_APPROVAL_KEY;
  if (!key) throw Error('Operator abandonment authority unavailable');
  const st = lstatSync(key);
  if (!st.isFile() || st.isSymbolicLink() || st.uid !== process.getuid?.() || (st.mode & 0o077)) throw Error('Unsafe operator public key');
  if (typeof value?.payload !== 'string' || value.payload.length > 16384 ||
      typeof value.signature !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(value.signature) || value.signature.length > 4096 ||
      !verify('sha256', Buffer.from(value.payload), readFileSync(key), Buffer.from(value.signature, 'base64'))) throw Error('Invalid abandonment signature');
  const e = JSON.parse(value.payload) as AbandonmentEvidence;
  if (e?.kind !== 'operator-abandon-v1' || typeof e.unit_id !== 'string' || e.attempt_id !== `${e.unit_id}:0` ||
      typeof e.conversation_id !== 'string' || typeof e.reason !== 'string' || !e.reason.trim() || e.reason.length > 500 ||
      typeof e.store_id !== 'string' || !Number.isSafeInteger(e.generation) || e.generation < 0 ||
      !/^[a-f0-9]{32}$/.test(e.allocation_id) || !/^[a-f0-9]{64}$/.test(e.worker_id) || !/^[a-f0-9]{64}$/.test(e.proxy_id)) throw Error('Invalid abandonment evidence');
  return e;
}
export function commitAbandonment(envelope: AbandonmentEnvelope, db: string, anchor: string, unit: string): AbandonmentEvidence {
  const e = verifyAbandonment(envelope);
  if (e.unit_id !== unit || !db || !anchor) throw Error('Abandonment identity or store unavailable');
  const result = execFileSync('python3', [fileURLToPath(new URL('../../worker/abandon_attempt.py', import.meta.url)),
    '--db', db, '--anchor', anchor, '--unit', unit, `--reason=${e.reason}`, '--commit'],
    {input: envelope.payload, encoding: 'utf8', timeout: 30000, maxBuffer: 32768});
  if (result.trim() !== envelope.payload) throw Error('Abandonment receipt mismatch');
  return e;
}
