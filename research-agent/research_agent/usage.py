"""Request-local accounting using provider-reported tokens, never generated text estimates."""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from threading import Lock

from research_core.contracts import ModelUsage, QueryUsage

# USD per million tokens, standard tier. Review when changing models or pricing.
# https://developers.openai.com/api/docs/models/gpt-5.4-mini
PRICING_VERSION = "openai-standard-2026-10-22"
PRICES = {
    "gpt-5.4-mini": (Decimal("0.75"), Decimal("0.075"), Decimal("4.50")),
    "gpt-5.4-mini-2026-03-17": (Decimal("0.75"), Decimal("0.075"), Decimal("4.50")),
}
_current: ContextVar["UsageCollector | None"] = ContextVar("query_usage", default=None)


class UsageCollector:
    def __init__(self):
        self.calls: list[ModelUsage] = []
        self.lock = Lock()

    def record(self, response, requested_model):
        model = getattr(response, "model", None) or requested_model
        tier = getattr(response, "service_tier", None)
        usage = getattr(response, "usage", None)
        call = ModelUsage(model=model, service_tier=tier)
        if usage is not None:
            call.input_tokens = usage.prompt_tokens
            call.output_tokens = usage.completion_tokens
            call.cached_input_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
            call.reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0
            rates = PRICES.get(model)
            if rates and tier in (None, "default") and call.cached_input_tokens <= call.input_tokens:
                # Reasoning is already included in completion_tokens.
                cost = ((call.input_tokens - call.cached_input_tokens) * rates[0]
                        + call.cached_input_tokens * rates[1] + call.output_tokens * rates[2]) / 1_000_000
                call.estimated_cost_usd = float(cost)
        with self.lock:
            self.calls.append(call)

    def summary(self):
        with self.lock:
            calls = list(self.calls)
        known = round(sum(c.estimated_cost_usd or 0 for c in calls), 12)
        return QueryUsage(
            calls=calls,
            input_tokens=sum(c.input_tokens or 0 for c in calls),
            cached_input_tokens=sum(c.cached_input_tokens for c in calls),
            output_tokens=sum(c.output_tokens or 0 for c in calls),
            reasoning_tokens=sum(c.reasoning_tokens for c in calls),
            usage_complete=all(c.input_tokens is not None and c.output_tokens is not None for c in calls),
            estimated_cost_usd=known if all(c.estimated_cost_usd is not None for c in calls) else None,
            known_cost_usd=known, pricing_version=PRICING_VERSION,
        )


@contextmanager
def collect_usage():
    collector = UsageCollector()
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


def record_usage(response, requested_model):
    collector = _current.get()
    if collector is not None:
        collector.record(response, requested_model)
