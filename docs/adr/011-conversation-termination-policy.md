# ADR-011: Conversation termination policy

## Status

Accepted.

## Context

A multi-turn agent needs explicit answers to three questions, otherwise it either runs forever or terminates surprisingly:

1. **What signals end the conversation?**
2. **How tolerant is the agent of off-topic input before it gives up?**
3. **What's the safety ceiling on a single conversation's iteration count?**

For both the current demo and the production target, the policy must be deterministic, testable, and tunable per channel. Different channels (voice IVR, web chat, SMS) want different tolerances.

## Decision

1. **Termination signals — fast-path keyword detector, not LLM-judged.** `detect_termination_utterance` (`agent/intents.py`) runs against the latest `HumanMessage` and matches a curated list of completion phrases (`I'm done`, `I am done`, `we're done`, `all done`, `I'm good`, `that's it`, `that's all`, `nothing else`, `goodbye`, `thanks bye`, `quit`, `exit`, `bye`, etc.). On match, the graph routes to summary then END.
2. **Off-scope tolerance — threshold = 3, configurable.** `detect_off_scope_utterance` flags clear off-topic input (weather, joke, news, song, math, etc.). Each match increments `state.off_scope_count`. When the count hits `Settings.app_off_scope_threshold` (default 3, env: `APP_OFF_SCOPE_THRESHOLD`), the conversation terminates with a summary. The first two off-topic messages trigger a polite redirect from the LLM but do not end the conversation.
3. **Iteration safeguard — `APP_MAX_GRAPH_ITERATIONS`, default 20.** Each LLM-node call increments `state.iteration_count`. When it exceeds the configured max inside a turn, `check_termination_node` flips `terminated=True`. This is the runaway guard for tool-loop pathologies, not a normal completion path.

The detectors are intentionally **deterministic Python**, not an LLM call. The model can be tricked into agreeing the conversation should end (or shouldn't); the keyword matcher cannot. Termination is a trust decision and is kept out of the LLM's hands.

## Consequences

Positive:

- **Testable.** 36 parametrised unit tests in `tests/unit/test_intents.py` cover the matcher; the integration suite covers each termination path end-to-end with a mocked LLM.
- **Tunable per channel.** `APP_OFF_SCOPE_THRESHOLD` and `APP_MAX_GRAPH_ITERATIONS` are environment-driven settings for per-deployment tuning. Voice channels typically want a lower off-scope threshold (2) than web chat (3–5).
- **Auditable.** Every termination is logged with `agent.intent.termination_detected reason=...` (`explicit_utterance` | `max_off_scope` | `max_iterations`) and persisted as a `ConversationSummary` row.

Negative:

- **Coverage gaps in keyword detection.** Phrases the matcher doesn't recognise (`bye!`, `we're good thanks`, etc.) require either pattern additions or the user clicking the explicit "End conversation" button below the Streamlit chat input. Both are mitigations rather than fixes.
- **Off-scope keyword list is hand-curated.** A truly determined user can stay technically on-topic while being unproductive. `classify_scope` (LLM-based) is implemented as a fallback in `agent/intents.py` but is not wired into the graph because it adds a per-turn LLM call for marginal benefit at this scale. The hook is there for the day cost is acceptable.

## Alternatives Considered

- **LLM-judged termination on every turn**: rejected. Per-turn classification cost without offsetting accuracy improvement at our scale. Easy to add later via the unwired `classify_scope` path.
- **No off-scope guard at all**: rejected. Without it, a user can keep an LLM session alive (and billable) indefinitely on off-topic chatter.
- **Single-strike termination on off-topic**: rejected. Too aggressive; users sometimes test the agent's edges before getting on-task. Two strikes plus the third terminating is the empirically reasonable default.
- **Hard cap on iterations enforced inside the graph itself (LangGraph `recursion_limit`)**: kept as the second line of defence; our `APP_MAX_GRAPH_ITERATIONS` runs first because it terminates with a clean summary path, while `recursion_limit` raises an exception.

## Operational notes

- **Manual termination button.** The Streamlit UI has a subdued "End conversation" control immediately under the chat input (visible from turn 1) that injects `"I'm done"` into the conversation. It is the deterministic, no-phrasing-required path to terminate, intended for the demo and for users whose phrasing falls outside the keyword detector.
- **Threshold change is one line.** `Settings.app_off_scope_threshold` (env `APP_OFF_SCOPE_THRESHOLD`) carries the threshold. Updating the env value and the test that pins it is the entire change.
