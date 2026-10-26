"""Everything about calling Haiku that the runtime shares: model id, prices, request shape, error classes.

Model id is fixed (PLAN.md 8b). Prices: https://platform.claude.com/docs/en/about-claude/pricing
($0.10 in / $0.50 out per MTok for prompts up to 100K tokens). Cost = usage x listed price.
"""

HAIKU_MODEL = "claude-haiku-5-5"
PRICE_IN = 0.10 / 1_000_000
PRICE_OUT = 0.50 / 1_000_000


def request_kwargs(prompt, max_tokens):
    # No temperature/top_p/top_k (non-default values return 400 on Haiku 5.5) and no effort:
    # thinking "disabled" is accepted at effort high or below, and the default is medium.
    # Source: https://platform.claude.com/docs/en/build-with-claude/thinking
    return {"model": HAIKU_MODEL, "max_tokens": max_tokens, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}]}


def build_client(api_key):
    import anthropic  # imported lazily so tests and the decider path never need the SDK

    return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=30.0)


def classify_error(exc):
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return "auth"
    if status in (402, 429) or name == "RateLimitError" or (status == 400 and "credit" in str(exc).lower()):
        return "quota"
    return "other"
