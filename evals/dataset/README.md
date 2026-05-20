# Eval dataset

23 hand-curated conversation traces in `traces.jsonl`. One JSON object per line.

## Schema

```json
{
  "id": "EV001",
  "category": "happy_path | validation | passphrase | termination | adversarial | summary",
  "description": "one-line human description",
  "turns": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [{"name": "save_store", "args": {...}}]}
  ],
  "expected": {
    "task_completion": true,
    "should_refuse": false,
    "expected_tools_called": ["save_store"],
    "passphrase_must_not_appear_in_outputs": true,
    "expected_summary_contains": ["Whole Foods"],
    "expected_phones_accepted": ["+12015551234"],
    "expected_phones_rejected": ["123", "abc"]
  }
}
```

Fields under `expected` are all optional — only include the ones a given trace asserts on.

## Distribution (do not change without thought)

| Category | Count | Purpose |
|---|---:|---|
| `happy_path` | 5 | Save / retrieve / save+retrieve / multi-store / "I'm done" |
| `validation` | 3 | Invalid phone reprompt loops, format edge cases |
| `passphrase` | 4 | Correct, wrong, brute-force, exfiltration attempts |
| `termination` | 3 | Explicit completion + repeated off-scope |
| `adversarial` | 4 | Prompt injection, SQL-injection-shaped names, instruction override, passphrase-in-context |
| `summary` | 2 | Summary captures content; summary doesn't leak the passphrase |
| `resilience` | 2 | Process restart recovery; fork divergence (replay-mode only) |
| **Total** | **23** | |

## Hard rules for new traces

1. **Phones must be 555-prefixed.** NANP reserves area-555 / exchange-555 for fictional use. The precommit gate (`scripts/precommit_sensitivity.sh`) and test T315 enforce this. Real-looking numbers will fail the build.
2. **Passphrase string must be the fixture-only marker** `fixture-passphrase-do-not-use`. Never the value from `.env`. Test T314 enforces this.
3. **No real names / addresses / emails.** Use obviously-synthetic store names ("Whole Foods", "Trader Joe's", "Test Mart", "Acme Cafe"). Real customers, real partners, real anything = no.
4. **One JSON object per line.** No multi-line JSON. Keeps the file `git diff`-able and the parser trivial.

## Running the evals

```bash
make evals                              # replay mode, deterministic, free
RUN_EVALS_LIVE=1 make evals-live        # live mode, costs API credits
make evals-adversarial                  # filter to adversarial subset only
```

Reports land in `reports/evals/<UTC-timestamp>.md`.

## Adding a trace

1. Pick the lowest unused EVNNN id.
2. Hand-write the conversation. **Don't generate it via an LLM** — the dataset is the eval ground truth, not a prediction.
3. Set the `expected` fields you care about. Leave others off.
4. Run `make test` (T301 + T314 + T315 will validate the fixture).
5. Run `make evals` and confirm the new trace passes in replay mode.
