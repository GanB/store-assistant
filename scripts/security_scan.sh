#!/usr/bin/env bash
# Aggregated security scan: SAST (bandit, ruff S), SCA (pip-audit), strict
# typecheck (mypy), secret scan, migration drift (alembic check), and a guard
# against raw DDL in src/. Each tool runs to completion; the script exits
# non-zero at the end if any check failed.

set -uo pipefail

cd "$(dirname "$0")/.."

FAILED=0
declare -a RESULTS

mark_pass() {
    RESULTS+=("PASS  $1")
}
mark_fail() {
    RESULTS+=("FAIL  $1")
    FAILED=1
}
mark_skip() {
    RESULTS+=("SKIP  $1")
}

section() {
    printf '\n--- %s ---\n' "$1"
}

# 1. bandit (SAST) — fail on MEDIUM or HIGH
section "bandit (SAST)"
if uv run bandit -r src/ -ll -q; then
    mark_pass "bandit"
else
    mark_fail "bandit"
fi

# 2. pip-audit (SCA) — fail on any known vulnerability with a fix available.
# Acknowledged unfixed advisories are listed in scripts/pip-audit-ignored.txt
# alongside a short rationale; new entries require justification.
section "pip-audit (SCA)"
PIP_AUDIT_ARGS=(--skip-editable)
if [ -f scripts/pip-audit-ignored.txt ]; then
    while IFS= read -r line; do
        # Skip blanks and comment lines
        case "$line" in
            ''|'#'*) continue ;;
        esac
        cve_id="${line%%[[:space:]#]*}"
        [ -n "$cve_id" ] && PIP_AUDIT_ARGS+=(--ignore-vuln "$cve_id")
    done < scripts/pip-audit-ignored.txt
fi
if uv run pip-audit "${PIP_AUDIT_ARGS[@]}"; then
    mark_pass "pip-audit"
else
    mark_fail "pip-audit"
fi

# 3. ruff security rules
section "ruff (security rules)"
if uv run ruff check src/ --select S; then
    mark_pass "ruff-security"
else
    mark_fail "ruff-security"
fi

# 4. mypy strict
section "mypy (strict typecheck)"
if uv run mypy src/; then
    mark_pass "mypy"
else
    mark_fail "mypy"
fi

# 5. Secret scan: gitleaks if available, else regex grep
section "secret scan"
SECRET_PATTERNS='(sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{32,}|ls__[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,}|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+]{40})'

if command -v gitleaks >/dev/null 2>&1; then
    echo "(using gitleaks)"
    if gitleaks detect --source . --no-git --config .gitleaks.toml --redact --no-banner; then
        mark_pass "secret-scan"
    else
        mark_fail "secret-scan"
    fi
else
    echo "(gitleaks not installed; running grep-based fallback on tracked files)"
    if git grep -nIE "$SECRET_PATTERNS" -- \
        ':!scripts/security_scan.sh' \
        ':!docs/security.md' \
        ':!docs/observability.md' \
        ':!.gitignore' \
        ':!tests/' ; then
        echo "(real-looking secret patterns found in tracked files)"
        mark_fail "secret-scan"
    else
        echo "no real-looking secret patterns in tracked files"
        mark_pass "secret-scan"
    fi
fi

# 6. Migration drift (requires Postgres). Soft-skip when DB is unreachable.
section "alembic check (migration drift)"
ALEMBIC_OUT=$(uv run alembic check 2>&1) && ALEMBIC_RC=0 || ALEMBIC_RC=$?
echo "$ALEMBIC_OUT"
if [ "$ALEMBIC_RC" -eq 0 ]; then
    mark_pass "alembic-drift"
elif echo "$ALEMBIC_OUT" | grep -qiE "(connect call failed|could not connect|connection refused|name or service not known|nodename nor servname|field required|validation error.*Settings)"; then
    echo "(database or required env vars unavailable; skipping drift check)"
    mark_skip "alembic-drift"
else
    mark_fail "alembic-drift"
fi

# 7. DDL-in-code guard
section "DDL-in-code scan"
if grep -rEn "CREATE TABLE|ALTER TABLE|DROP TABLE|CREATE INDEX|DROP INDEX|CREATE EXTENSION|CREATE TYPE" src/; then
    echo "DDL keywords found in src/. All DDL must live in alembic/versions/."
    mark_fail "ddl-guard"
else
    echo "no DDL keywords in src/"
    mark_pass "ddl-guard"
fi

# Summary
section "Summary"
for r in "${RESULTS[@]}"; do echo "  $r"; done

# OWASP Top 10 (2021) coverage
section "OWASP Top 10 (2021) Coverage"
cat <<'OWASP'
A01 Broken Access Control            — passphrase gate at src/store_assistant/agent/tools.py
A02 Cryptographic Failures           — hmac.compare_digest in agent/tools.py; SecretStr in config.py
A03 Injection                        — SQLAlchemy parameterized queries (data/repository.py); Pydantic validation at every input boundary (data/schemas.py, agent/tool_schemas.py); structured tool args
A04 Insecure Design                  — explicit StateGraph transitions; APP_MAX_GRAPH_ITERATIONS safeguard; validation at every boundary
A05 Security Misconfiguration        — required env vars without defaults in config.py; .env.example never committed as .env; non-root appuser (UID 1000) in docker/backend.Dockerfile
A06 Vulnerable & Outdated Components — pip-audit in CI; uv.lock pins all transitive deps
A07 Identification & Auth Failures   — passphrase auth with constant-time hmac.compare_digest; failure does not reveal whether the record exists
A08 Software & Data Integrity        — uv.lock pinning; pinned base images (python:3.11-slim, postgres:16-alpine)
A09 Security Logging Failures        — structlog JSON logs with RedactingProcessor (logging_config.py); thread_id propagation; LangSmith tracing optional
A10 SSRF                             — no user-controlled URLs are constructed by the application
OWASP

if [ "$FAILED" -eq 0 ]; then
    echo
    echo "All security checks passed."
    exit 0
else
    echo
    echo "Security scan FAILED."
    exit 1
fi
