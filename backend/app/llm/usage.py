"""Adapter between structured_call's result and the budget layer.
If your result object uses other attribute names, change ONLY this file."""
from dataclasses import dataclass


class UsageUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_IN = ("input_tokens", "prompt_tokens", "tokens_in")
_OUT = ("output_tokens", "completion_tokens", "tokens_out")


def _get(obj, names):
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def extract_usage(result) -> TokenUsage:
    for holder in (getattr(result, "usage", None), result):
        if holder is None:
            continue
        i, o = _get(holder, _IN), _get(holder, _OUT)
        if i is not None and o is not None:
            return TokenUsage(i, o)
        total = _get(holder, ("total_tokens",))
        if total is not None:
            return TokenUsage(total, 0)
    raise UsageUnavailable("the provider result carries no token usage")


def unwrap_parsed(result, schema):
    if isinstance(result, schema):
        return result
    for name in ("parsed", "data", "output", "value", "result"):
        v = getattr(result, name, None)
        if isinstance(v, schema):
            return v
    raise TypeError("cannot find the parsed object in the structured_call result")
