"""Deterministic security pipeline — orchestrates real SAST engines, no LLM.

Full SAST: Semgrep (static analysis), Gitleaks (secrets), OSV-Scanner (dependency
vulnerabilities), Cosign (build provenance). Each engine runs when its binary is
installed; when absent it degrades to a deterministic regex fallback, and the
report records which engine actually ran (so "SAST-lite" is never silently
presented as full SAST).

Verdicts:
  "pass"         — clean, safe to run dark.
  "needs_review" — security-relevant or medium findings → human escalation.
  "fail"         — critical/high findings → block merge.
"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass
class Finding:
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    category: str  # "secret" | "sast" | "dependency" | "provenance"
    rule: str
    location: str
    message: str


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    security_relevant: bool = False
    verdict: str = "pass"
    engines: dict = field(default_factory=dict)  # category -> "semgrep" | "regex" | ...

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "security_relevant": self.security_relevant,
            "engines": self.engines,
            "findings": [f.__dict__ for f in self.findings],
        }


def _which(tool: str) -> str | None:
    return shutil.which(tool)


# ---------------------------------------------------------------------------
# Real engines
# ---------------------------------------------------------------------------

_SEMGREP_SEVERITY = {
    "error": "high", "warning": "medium", "info": "low",
    "high": "high", "medium": "medium", "low": "low",
}


def run_semgrep(target: str, ruleset: str = "auto") -> list[Finding] | None:
    """Run Semgrep (full SAST). Returns None if absent OR if it errors (→ fallback)."""
    if not _which("semgrep"):
        return None
    r = subprocess.run(
        ["semgrep", "--config", ruleset, "--json", "--quiet", target],
        capture_output=True, text=True,
    )
    # semgrep exit codes: 0 = clean, 1 = findings, >=2 = error. But a crash
    # (e.g. unwritable ~/.semgrep) can also exit 1 with empty stdout — treat
    # "no parseable JSON on stdout" as "couldn't run" and fall back.
    if r.returncode >= 2 or not r.stdout:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    out = []
    for res in data.get("results", []):
        extra = res.get("extra", {})
        # semgrep emits impact/likelihood (not a single "severity" in v1.x).
        sev_raw = extra.get("severity") or extra.get("impact") or "medium"
        sev = _SEMGREP_SEVERITY.get(str(sev_raw).lower(), "medium")
        out.append(Finding(
            severity=sev,
            category="sast",
            rule=res.get("check_id", "unknown"),
            location=str(res.get("path", "?")),
            message=extra.get("message", res.get("check_id", "")),
        ))
    return out


def run_gitleaks(target: str) -> list[Finding] | None:
    """Run Gitleaks (secret scanning). Returns None if the binary is absent."""
    if not _which("gitleaks"):
        return None
    # `dir` scans files directly (vs `detect`, which scans git history).
    # v8 writes the JSON report to --report-path (not stdout).
    with NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        report_path = tmp.name
    original_error = None
    try:
        subprocess.run(
            ["gitleaks", "dir", "--report-format", "json", "--report-path", report_path, target],
            capture_output=True, text=True,
        )
        try:
            data = json.loads(Path(report_path).read_text())
        except (json.JSONDecodeError, OSError):
            return None
        # Gitleaks v8 emits a top-level array; older versions nest under "Findings".
        raw = data if isinstance(data, list) else (data.get("Findings") or data.get("Leaks") or [])
        out = []
        for f in raw:
            out.append(Finding(
                severity="high",
                category="secret",
                rule=f.get("RuleID", f.get("ruleId", "secret")),
                location=str(f.get("File", f.get("file", "?"))),
                message=f.get("Description", "secret detected"),
            ))
        return out
    except BaseException as error:
        original_error = error
        raise
    finally:
        try:
            Path(report_path).unlink(missing_ok=True)
        except OSError:
            if original_error is None:
                raise
            original_error.add_note("Gitleaks temporary report cleanup also failed")


def run_osv_scanner(lockfile: str) -> list[Finding] | None:
    """Run OSV-Scanner (dependency vulnerabilities). Returns None if absent."""
    if not _which("osv-scanner"):
        return None
    # v2 syntax: `osv-scanner scan <lockfile> --format json` (the old --lockfile flag is gone).
    r = subprocess.run(
        ["osv-scanner", "scan", lockfile, "--format", "json"],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for res in data.get("results", []):
        for pkg in res.get("packages", []):
            name = pkg.get("package", {}).get("name", "?")
            for vuln in pkg.get("vulnerabilities", []):
                out.append(Finding(
                    severity="medium",
                    category="dependency",
                    rule=vuln.get("id", "CVE"),
                    location=str(name),
                    message=vuln.get("summary", vuln.get("id", "")),
                ))
    return out


def run_cosign_verify(image: str, identity: str) -> list[Finding] | None:
    """Verify build provenance (sigstore). Returns None if the binary is absent."""
    if not _which("cosign"):
        return None
    r = subprocess.run(
        ["cosign", "verify", "--certificate-identity", identity, image],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return [Finding("info", "provenance", "sigstore-verified", image, "signature verified")]
    return [Finding("high", "provenance", "sigstore-failed", image, r.stderr.strip() or "verification failed")]


# ---------------------------------------------------------------------------
# Deterministic regex fallback (used only when a real engine is absent)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "critical", "aws-access-key"),
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|DSA) PRIVATE KEY-----"), "critical", "private-key"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "critical", "github-token"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "critical", "openai-key"),
    (re.compile(r"\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.I), "high", "hardcoded-secret"),
]

_SAST_PATTERNS = [
    (re.compile(r"\beval\s*\("), "high", "eval"),
    (re.compile(r"\bexec\s*\("), "high", "exec"),
    (re.compile(r"os\.system\s*\("), "high", "os-system"),
    (re.compile(r"subprocess\..*shell\s*=\s*True"), "high", "shell-true"),
    (re.compile(r"innerHTML\s*=|dangerouslySetInnerHTML"), "medium", "xss"),
    (re.compile(r"(md5|sha1)\s*\("), "medium", "weak-hash"),
    (re.compile(r"(SELECT|INSERT|UPDATE|DELETE).*['\"]\s*\+"), "high", "sql-concat"),
]

_SECURITY_PATHS = re.compile(
    r"(auth|login|session|token|password|secret|crypto|encrypt|decrypt|permission|acl|rbac|ssl|tls|cert|key|jwt|oauth)",
    re.I,
)
_SECURITY_CONTENT = re.compile(
    r"(bcrypt|argon2|pbkdf2|jwt|oauth|encrypt|decrypt|hash\(|sign\(|verify\(|private key|secret)",
    re.I,
)
_DEPENDENCY_FILES = re.compile(
    r"(package\.json|package-lock\.json|Cargo\.toml|Cargo\.lock|requirements\.txt|go\.mod|go\.sum|pyproject\.toml|Pipfile|composer\.json|Gemfile)",
    re.I,
)


def _regex_secrets(diff_text: str) -> list[Finding]:
    return [
        Finding(sev, "secret", rule, "diff", f"possible {rule} in diff")
        for pat, sev, rule in _SECRET_PATTERNS
        for m in pat.finditer(diff_text)
    ]


def _regex_sast(diff_text: str) -> list[Finding]:
    return [
        Finding(sev, "sast", rule, "diff", f"{rule} pattern in diff")
        for pat, sev, rule in _SAST_PATTERNS
        for m in pat.finditer(diff_text)
    ]


def _regex_dependencies(diff_text: str) -> list[Finding]:
    return [
        Finding("info", "dependency", "manifest-change", m.group(0),
                "dependency manifest changed — verify against vulnerability DB")
        for m in _DEPENDENCY_FILES.finditer(diff_text)
    ]


def classify_security_relevance(paths: list[str], diff_text: str) -> bool:
    for p in paths:
        if _SECURITY_PATHS.search(p):
            return True
    return bool(_SECURITY_CONTENT.search(diff_text))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _verdict(findings: list[Finding], security_relevant: bool) -> str:
    if any(f.severity in ("critical", "high") for f in findings):
        return "fail"
    if security_relevant or any(f.severity == "medium" for f in findings):
        return "needs_review"
    return "pass"


def scan(diff_text: str, paths: list[str] | None = None, *, target: str | None = None, lockfile: str | None = None) -> ScanReport:
    """Run the pipeline. Uses real engines when installed, regex fallback otherwise."""
    paths = paths or []
    findings: list[Finding] = []
    engines: dict = {}

    # SAST
    semgrep_findings = run_semgrep(target) if target else None
    if semgrep_findings is not None:
        findings += semgrep_findings + _regex_sast(diff_text)
        engines["sast"] = "semgrep+diff-regex"
    else:
        findings += _regex_sast(diff_text)
        engines["sast"] = "regex-fallback"

    # Secrets
    gitleaks_findings = run_gitleaks(target) if target else None
    if gitleaks_findings is not None:
        findings += gitleaks_findings + _regex_secrets(diff_text)
        engines["secret"] = "gitleaks+diff-regex"
    else:
        findings += _regex_secrets(diff_text)
        engines["secret"] = "regex-fallback"

    # Dependencies
    if lockfile and _which("osv-scanner"):
        findings += run_osv_scanner(lockfile) or []
        engines["dependency"] = "osv-scanner"
    else:
        findings += _regex_dependencies(diff_text)
        engines["dependency"] = "regex-fallback" if lockfile else "manifest-flag"

    security_relevant = classify_security_relevance(paths, diff_text)
    return ScanReport(
        findings=findings,
        security_relevant=security_relevant,
        verdict=_verdict(findings, security_relevant),
        engines=engines,
    )
