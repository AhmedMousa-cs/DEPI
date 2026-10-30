import json
import logging
from typing import Type, TypeVar
from pydantic import BaseModel, ValidationError

from app.llm.contracts import ChatModel, LLMUsage, StructuredResult
from app.llm.errors import LLMConfigurationError, LLMProviderError, StructuredValidationError

log = logging.getLogger("nexus.llm")
T = TypeVar("T", bound=BaseModel)


def structured_call(
    *,
    model: ChatModel,
    messages: list[dict[str, str]],
    schema: Type[T],
    max_attempts: int = 3,
    max_tokens: int = 2000,
) -> StructuredResult:
    curr_messages = list(messages)
    json_schema = schema.model_json_schema()

    total_in = 0
    total_out = 0
    total_cost = 0.0
    last_raw = ""
    last_model = ""

    for attempt in range(1, max_attempts + 1):
        try:
            res = model.invoke(
                messages=curr_messages,
                response_schema=json_schema,
                max_tokens=max_tokens,
            )
        except (LLMConfigurationError, LLMProviderError):
            raise
        except Exception as exc:
            log.error("Provider invocation error on attempt %d: %s", attempt, exc)
            raise LLMProviderError(f"Provider invocation failed: {exc}") from exc

        last_raw = res.content
        last_model = res.model
        total_in += res.usage.input_tokens
        total_out += res.usage.output_tokens
        total_cost += res.usage.estimated_cost_usd

        try:
            clean_content = res.content.strip()
            if clean_content.startswith("```json"):
                clean_content = clean_content[7:]
            if clean_content.startswith("```"):
                clean_content = clean_content[3:]
            if clean_content.endswith("```"):
                clean_content = clean_content[:-3]
            clean_content = clean_content.strip()

            data = json.loads(clean_content)
            parsed_val = schema.model_validate(data)

            return StructuredResult(
                value=parsed_val,
                raw_text=last_raw,
                usage=LLMUsage(
                    input_tokens=total_in,
                    output_tokens=total_out,
                    total_tokens=total_in + total_out,
                    estimated_cost_usd=round(total_cost, 6),
                ),
                model=last_model,
                attempts=attempt,
            )
        except (json.JSONDecodeError, ValidationError) as val_err:
            log.warning(
                "Structured validation error on attempt %d/%d for %s: %s",
                attempt,
                max_attempts,
                schema.__name__,
                val_err,
            )
            if attempt == max_attempts:
                raise StructuredValidationError(
                    f"Failed to generate valid {schema.__name__} after {max_attempts} attempts. Last error: {val_err}"
                ) from val_err

            curr_messages.append({"role": "assistant", "content": res.content})
            curr_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous output failed validation with error:\n{val_err}\n"
                        f"Please fix the error and respond strictly conforming to the {schema.__name__} schema."
                    ),
                }
            )

    raise StructuredValidationError(f"Failed to parse {schema.__name__} response.")
