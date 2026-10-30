"""Backend-controlled pricing. Prices come from configuration, never from model output."""
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings

_MILLION = Decimal(1_000_000)
_QUANT = Decimal("0.00000001")


class PricingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: Decimal
    output_per_1m: Decimal


def load_pricing(raw: str | None = None) -> dict[str, ModelPrice]:
    raw = settings.llm_pricing_json if raw is None else raw
    try:
        data = json.loads(raw or "{}")
        out = {}
        for model, p in data.items():
            i, o = Decimal(str(p["input_per_1m"])), Decimal(str(p["output_per_1m"]))
            if i < 0 or o < 0:
                raise ValueError("negative price")
            out[model] = ModelPrice(i, o)
        return out
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        raise PricingConfigError(f"invalid LLM_PRICING_JSON: {type(exc).__name__}") from None


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int, pricing: dict | None = None) -> tuple[Decimal, bool]:
    """Returns (cost, pricing_configured). Unknown model -> (0, False): never an invented price."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be >= 0")
    price = (pricing if pricing is not None else load_pricing()).get(model)
    if price is None:
        return Decimal(0), False
    cost = (Decimal(input_tokens) * price.input_per_1m + Decimal(output_tokens) * price.output_per_1m) / _MILLION
    return cost.quantize(_QUANT, rounding=ROUND_HALF_UP), True
