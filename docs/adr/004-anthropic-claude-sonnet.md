# ADR-004: Anthropic Claude Sonnet 4.5 as the LLM

## Status

Accepted.

## Context

The assistant relies heavily on tool calling: the model must decide between save, retrieve, and terminate intents; emit well-formed tool arguments matching Pydantic schemas; and produce a structured summary at termination. The model also needs to handle short, casual store-owner inputs without losing track of the slot-filling state. Quality of tool calling and structured output matters more than raw reasoning depth on long context.

Three LLM provider families were realistic candidates: Anthropic Claude, OpenAI GPT-4 family, and self-hosted open-weights models (e.g., Llama 3.x).

## Decision

Use Anthropic Claude Sonnet 4.5 (`claude-sonnet-4-5`) as the default model, accessed via `langchain-anthropic`. The model name is configurable via the `APP_LLM_MODEL` environment variable so the same code can run against newer Sonnet revisions, against Haiku for cheap classification paths, or against Opus for harder reasoning, without code changes.

## Consequences

Positive: Sonnet 4.5 has strong, schema-faithful tool calling, good adherence to system prompts, and a favourable cost/capability balance for an agent that issues multiple tool calls per conversation. Streaming and prompt caching are first-class, which matters for the Production Readiness path. The Anthropic API surface is stable and well-documented.

Negative: Vendor lock-in to Anthropic at the API level. `langchain-anthropic` mitigates this somewhat by providing a `BaseChatModel` abstraction, but the prompt style and tool schema dialect are tuned to Claude. Pricing fluctuates and is not the cheapest option for high-volume traffic.

## Alternatives Considered

- **OpenAI GPT-4o / GPT-4.1**: viable. Rejected as default because Anthropic's tool-call behaviour is more predictable for this style of structured agent in our experience, and because the production migration target (Bedrock) supports Claude natively.
- **Llama 3.x self-hosted**: rejected for current scope — operationally heavy, adds a model-serving stack, and tool-calling quality at the size we could realistically host is below frontier APIs.
- **Smaller Claude Haiku as the only model**: rejected as default — risk of degraded tool-call accuracy on the slot-filling flow. Haiku is reserved for cheaper sub-paths (e.g., intent classification) per ADR-011 cost optimization plans.

**Production migration path**: deploy Claude on Amazon Bedrock. Same model family, same prompts, AWS-managed endpoint inside the production VPC, IAM-based access, audit logs to CloudTrail, and Bedrock Guardrails for output policy enforcement. The application code changes only the client constructor.
