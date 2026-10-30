import type { OrchestrateRequest } from "./orchestrate.js";

// Shared types across the spine. Mirrors the Python wall's receipts/verdicts.

export type Stage =
  | "unclaimed"
  | "claimed"
  | "building"
  | "needs_reconciliation"
  | "needs_validation"
  | "validated"
  | "gated"
  | "merged"
  | "deployed"
  | "failed"
  | "abandoned";

export type PricingMode = "subscription" | "metered";

export interface UsageRecord {
  tokens_in: number;
  tokens_out: number;
  requests: number;
  retries: number;
  wall_clock_ms: number;
  tier: string;
  pricing_mode: PricingMode;
  cost_estimate_usd: number | null;
  ceiling_violation: string;
}

export interface Verdict {
  passed: boolean;
  aggregate_pass_rate: number;
  failure_message: string;
  per_scenario: Record<string, boolean>;
}

// A receipt binds a result to the exact git revision it was produced from.
export interface Receipt {
  verdict: Verdict;
  usage: UsageRecord;
  git_revision: string;
}

export interface Claim {
  worker_id: string;
  lease_until_ms: number; // epoch ms; the claim expires after this
}

export interface BuildEvidence {
  attempt_id: string;
  status: string;
  conversation_id?: string;
  git_revision?: string;
  error?: string;
}

export interface WorkUnitState {
  stage: Stage;
  abandonment?: { payload: string; evidence: import("./abandonment.js").AbandonmentEvidence };
  validation?: import("./validation.js").ValidationEvidence;
  recovery?: { request: OrchestrateRequest; attemptDb: string; attemptAnchor?: string } | null;
  build?: BuildEvidence | null;
  successor?: BuildEvidence | null;
  claim: Claim | null;
  receipt: Receipt | null;
  attempts: number;
}

// Human gates (the "mostly lights-out" approvals a human stays in the loop for).
export type GateType =
  | "initial_prod_deploy" // first promotion of a unit to production
  | "budget_raise" // raising the spend/quota envelope
  | "destructive_op" // a destructive or privileged operation
  | "credential_issue" // issuing a privileged credential
  | "security_review"; // human escalation for a security-relevant diff (tiered dark)

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface Approval {
  requestId: string;
  gateType: GateType;
  status: ApprovalStatus;
  decision: string; // "approved" | "rejected" | "" while pending
  approver: string | null;
  context: string; // what the human is being asked to approve
  awakeableId: string; // durable handle used to resolve the pending request
  requestedAtMs: number;
  resolvedAtMs: number | null;
}

// Deterministic security-pipeline result (mirrors security/pipeline.py).
export interface SecurityFinding {
  severity: "critical" | "high" | "medium" | "low" | "info";
  category: "secret" | "sast" | "dependency" | "provenance";
  rule: string;
  location: string;
  message: string;
}

export interface ScanReport {
  verdict: "pass" | "needs_review" | "fail";
  security_relevant: boolean;
  findings: SecurityFinding[];
}
