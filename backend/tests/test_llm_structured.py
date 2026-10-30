import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm.contracts import LLMUsage, ModelResponse
from app.llm.errors import LLMConfigurationError, StructuredValidationError
from app.llm.structured import structured_call


class DemoExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class FakeModel:
    def __init__(self, responses: list[str] | None = None, raise_auth_error: bool = False):
        self.responses = list(responses or [])
        self.raise_auth_error = raise_auth_error
        self.calls = []

    def invoke(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> ModelResponse:
        self.calls.append({"messages": list(messages), "schema": response_schema})

        if self.raise_auth_error:
            raise LLMConfigurationError("Invalid API Key")

        resp_text = self.responses.pop(0) if self.responses else "{}"
        return ModelResponse(
            content=resp_text,
            usage=LLMUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                estimated_cost_usd=0.001,
            ),
            model="fake-llama-3.3",
        )


def test_fake_model_does_not_require_network():
    model = FakeModel(responses=['{"title": "Demo", "confidence": 0.95}'])
    res = model.invoke(messages=[{"role": "user", "content": "hello"}])
    assert res.content == '{"title": "Demo", "confidence": 0.95}'
    assert res.usage.total_tokens == 150


def test_valid_structured_response():
    fake_json = '{"title": "System Architecture", "confidence": 0.98}'
    model = FakeModel(responses=[fake_json])

    res = structured_call(
        model=model,
        messages=[{"role": "user", "content": "extract info"}],
        schema=DemoExtraction,
    )

    assert isinstance(res.value, DemoExtraction)
    assert res.value.title == "System Architecture"
    assert res.value.confidence == 0.98
    assert res.attempts == 1
    assert res.usage.total_tokens == 150
    assert res.usage.estimated_cost_usd == 0.001


def test_invalid_response_retries_and_succeeds():
    invalid_json = '{"title": "", "confidence": 1.5}'
    valid_json = '{"title": "Valid Spec", "confidence": 0.8}'
    model = FakeModel(responses=[invalid_json, valid_json])

    res = structured_call(
        model=model,
        messages=[{"role": "user", "content": "extract info"}],
        schema=DemoExtraction,
        max_attempts=3,
    )

    assert res.value.title == "Valid Spec"
    assert res.attempts == 2
    assert res.usage.total_tokens == 300
    assert len(model.calls) == 2


def test_validation_error_is_sent_to_next_attempt():
    invalid_json = '{"title": "Spec"}'
    valid_json = '{"title": "Spec", "confidence": 0.5}'
    model = FakeModel(responses=[invalid_json, valid_json])

    structured_call(
        model=model,
        messages=[{"role": "user", "content": "extract"}],
        schema=DemoExtraction,
        max_attempts=2,
    )

    second_call_messages = model.calls[1]["messages"]
    assert len(second_call_messages) == 3
    assert second_call_messages[1]["role"] == "assistant"
    assert "validation with error" in second_call_messages[2]["content"]


def test_max_attempts_raise_structured_validation_error():
    invalid_json = '{"title": "Spec", "confidence": 99}'
    model = FakeModel(responses=[invalid_json, invalid_json, invalid_json])

    with pytest.raises(StructuredValidationError) as excinfo:
        structured_call(
            model=model,
            messages=[{"role": "user", "content": "extract"}],
            schema=DemoExtraction,
            max_attempts=3,
        )

    assert "after 3 attempts" in str(excinfo.value)


def test_provider_auth_error_is_not_retried_forever():
    model = FakeModel(raise_auth_error=True)

    with pytest.raises(LLMConfigurationError):
        structured_call(
            model=model,
            messages=[{"role": "user", "content": "test"}],
            schema=DemoExtraction,
            max_attempts=3,
        )

    assert len(model.calls) == 1


def test_schema_rejects_extra_fields():
    extra_field_json = '{"title": "Demo", "confidence": 0.9, "extra": "forbidden"}'
    valid_json = '{"title": "Demo", "confidence": 0.9}'
    model = FakeModel(responses=[extra_field_json, valid_json])

    res = structured_call(
        model=model,
        messages=[{"role": "user", "content": "test"}],
        schema=DemoExtraction,
        max_attempts=2,
    )

    assert res.attempts == 2
