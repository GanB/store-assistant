#!/usr/bin/env bash
# Pre-commit sensitivity gate.
# Scans staged files (and optionally a fixed list) for hard secrets, real-looking
# phone numbers outside fixtures, personal contact emails, and company-domain
# emails in code. Failure exits 1 and prints offending file:line:reason.
#
# Usage:
#   ./scripts/precommit_sensitivity.sh                        # scan staged files
#   ./scripts/precommit_sensitivity.sh path/to/file [...]     # scan explicit list

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

EXIT=0

# Hard secret patterns — block anywhere they appear.
SECRET_REGEX='sk-ant-[a-zA-Z0-9_-]+|sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82}|-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|xox[bpars]-[a-zA-Z0-9-]+'

# US phone shape — exchange code [2-9] catches 555 too; we filter those for fixture paths.
PHONE_REGEX='1?[-. ]?\(?[2-9][0-9]{2}\)?[-. ]?[2-9][0-9]{2}[-. ]?[0-9]{4}'

# Company-domain email lines — block in code, allow in pyproject (author block),
# .env.example (placeholder), this script, README/docs (commentary), alembic.ini.
COMPANY_EMAIL_REGEX='@(gmail\.com|outlook\.com|yahoo\.com)'

fail() {
    echo "FAIL: $1"
    EXIT=1
}

is_555_match() {
    # Receives a phone-shaped substring; returns 0 if exchange (middle 3) is 555.
    local digits
    digits=$(printf '%s' "$1" | tr -cd '0-9')
    if [ ${#digits} -eq 11 ] && [ "${digits:0:1}" = "1" ]; then
        digits="${digits:1}"
    fi
    if [ ${#digits} -eq 10 ] && [ "${digits:3:3}" = "555" ]; then
        return 0
    fi
    return 1
}

scan_secrets() {
    local f="$1"
    case "$f" in
        scripts/precommit_sensitivity.sh) return ;;
        tests/unit/test_logging_redaction.py) return ;;
    esac
    local hits
    hits=$(grep -nE "$SECRET_REGEX" "$f" 2>/dev/null || true)
    if [ -n "$hits" ]; then
        fail "$f contains a hard secret pattern"
        printf '%s\n' "$hits"
    fi
}

scan_phones() {
    local f="$1"
    # Skip generated lockfiles — they're full of SHA hashes that look
    # phone-shaped to a regex but cannot leak real numbers.
    case "$f" in
        uv.lock|*/uv.lock|package-lock.json|yarn.lock|poetry.lock)
            return ;;
        # The gate script's own comments document the patterns it scans for,
        # so it legitimately contains phone-shaped example strings. Same
        # exemption logic as scan_secrets above.
        scripts/precommit_sensitivity.sh)
            return ;;
        # Tool schema descriptions are seen by the LLM at runtime, so they
        # need example phone shapes embedded as literal strings. The numbers
        # used are reserved-for-fiction (555-0100 is NANP-reserved,
        # +441632960000 is Ofcom-reserved). Allowlisted with the same intent
        # as the test/eval-fixture phone exemption: deliberate examples in
        # a known location, not real numbers leaking through.
        src/store_assistant/agent/tool_schemas.py)
            return ;;
    esac
    local hits
    hits=$(grep -nE "$PHONE_REGEX" "$f" 2>/dev/null || true)
    [ -z "$hits" ] && return

    case "$f" in
        tests/*|evals/*)
            # In test/eval fixtures, phones must be 555-prefixed (NANP reserved).
            # Skip lines whose phone is part of a clearly non-NANP international
            # number (e.g., +44 207 183 8750).
            local non555_lines=""
            while IFS= read -r line; do
                # Lines that contain a +<non-1-digit> country code are int'l;
                # the 555 NANP rule does not apply.
                if printf '%s' "$line" | grep -qE '\+[2-9]'; then
                    continue
                fi
                local match
                match=$(printf '%s\n' "$line" | grep -oE "$PHONE_REGEX" | head -1)
                if [ -n "$match" ] && ! is_555_match "$match"; then
                    non555_lines="${non555_lines}${line}\n"
                fi
            done <<< "$hits"
            if [ -n "$non555_lines" ]; then
                fail "$f has non-555 phone in test/eval fixture"
                printf '%b' "$non555_lines"
            fi
            ;;
        *.md|docs/*)
            # Markdown / docs may show example phones; allow.
            ;;
        *)
            # Any phone-shaped string in non-doc, non-fixture code is suspicious.
            fail "$f contains phone-shaped string outside tests/evals/docs"
            printf '%s\n' "$hits"
            ;;
    esac
}

scan_company_email() {
    local f="$1"
    case "$f" in
        pyproject.toml|.env.example|scripts/precommit_sensitivity.sh|README.md|alembic.ini)
            return ;;
        docs/*)
            return ;;
    esac
    local hits
    hits=$(grep -nE "$COMPANY_EMAIL_REGEX" "$f" 2>/dev/null || true)
    if [ -n "$hits" ]; then
        fail "$f contains company-domain email in code"
        printf '%s\n' "$hits"
    fi
}

scan_one() {
    local f="$1"
    [ ! -f "$f" ] && return
    case "$f" in
        alembic/versions/*) return ;;
    esac
    if file "$f" 2>/dev/null | grep -q "binary"; then return; fi

    scan_secrets "$f"
    scan_phones "$f"
    scan_company_email "$f"
}

# Determine the file list: explicit args win, otherwise staged files.
if [ "$#" -gt 0 ]; then
    files=$(printf '%s\n' "$@")
else
    files=$(git diff --cached --name-only --diff-filter=AM 2>/dev/null || true)
fi

while IFS= read -r f; do
    [ -z "$f" ] && continue
    scan_one "$f"
done <<< "$files"

# gitleaks (advisory unless installed). Only run in staged mode; explicit-file
# mode falls back to the regex scans above so it doesn't accidentally pick up
# the gitignored .env that's intentionally on disk locally.
if [ "$#" -eq 0 ]; then
    if command -v gitleaks >/dev/null 2>&1; then
        if ! gitleaks protect --staged --no-banner --config .gitleaks.toml \
            >/dev/null 2>&1; then
            gitleaks protect --staged --no-banner --config .gitleaks.toml || true
            fail "gitleaks reported findings on staged content"
        fi
    else
        echo "warning: gitleaks not installed; skipping (install for stronger checks)" >&2
    fi
fi

if [ "$EXIT" -eq 0 ]; then
    echo "Pre-commit sensitivity check passed."
fi
exit "$EXIT"
