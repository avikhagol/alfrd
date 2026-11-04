"""Standard API-equivalent USD estimates, not subscription charges.

Source: https://developers.openai.com/api/docs/pricing (checked 2026-11-02).
Legacy rates: /api/docs/models/gpt-5-codex, /gpt-5.2, /gpt-5.4
on the same host (checked on the same date).
Each tuple is input, cached input, output USD per million tokens. Unknown
models deliberately have no fallback rate. Update this single table as prices change.
"""

MODEL_PRICES = {
    "gpt-5-codex": (1.25, 0.125, 10.0),
    "gpt-5.2": (1.75, 0.175, 14.0),
    "gpt-5.2-2025-12-11": (1.75, 0.175, 14.0),
    "gpt-5.4": (2.5, 0.25, 15.0),
    "gpt-6-astra": (10.0, 1.0, 50.0),
    "gpt-6.1-sol": (2.0, 0.1, 10.0),
    "gpt-6.1-terra": (1.0, 0.05, 5.0),
    "gpt-6-luna": (0.1, 0.01, 0.5),
    "gpt-5.6-sol": (4.0, 0.4, 20.0),
    "gpt-5.3-codex": (1.75, 0.175, 14.0),
}


def token_cost(model, usage):
    """Return (estimate, missing reason); cached tokens are included in input."""
    if not usage:
        return None, "Not reported by agent"
    rates = MODEL_PRICES.get(model)
    if rates is None:
        return None, f"Unknown model price: {model or 'model not reported'}"
    counts = [usage.get(key) for key in ("input_tokens", "cache_read_input_tokens", "output_tokens")]
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts):
        return None, "Incomplete token usage reported by agent"
    inputs, cached, output = counts
    if cached > inputs:
        return None, "Invalid token usage reported by agent"
    return ((inputs - cached) * rates[0] + cached * rates[1] + output * rates[2]) / 1_000_000, None
