import json
import os

import httpx

from app.config import settings
from app.llm.contracts import LLMUsage, ModelResponse
from app.llm.errors import LLMProviderError


class OllamaProvider:
    """Local Ollama via its native /api/chat (supports JSON-schema constrained output)."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.model = model or getattr(settings, "model_llm", os.getenv("MODEL_LLM", "qwen-nexus"))
        url = base_url or getattr(settings, "llm_base_url", None) or os.getenv("LLM_BASE_URL") or "http://localhost:11434"
        url = url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        self.base_url = url
        self.timeout = float(getattr(settings, "llm_timeout_seconds", 300) or 300)

    def _chat(self, payload: dict) -> dict:
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"{r.status_code}: {r.text[:500]}", request=r.request, response=r)
        return r.json()

    def invoke(self, *, messages: list[dict[str, str]], response_schema: dict | None = None,
               max_tokens: int = 2000) -> ModelResponse:
        formatted = list(messages)
        if response_schema and formatted:
            instruction = (
                "\n\nRespond ONLY with valid JSON that conforms to this schema:\n"
                f"{json.dumps(response_schema)}\n"
                "No markdown, no extra text."
            )
            formatted[-1] = {"role": formatted[-1]["role"], "content": formatted[-1]["content"] + instruction}

        payload: dict = {
            "model": self.model,
            "messages": formatted,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": max_tokens, "num_ctx": int(os.getenv("LLM_NUM_CTX", "8192"))},
        }
        if response_schema:
            payload["format"] = response_schema

        try:
            try:
                data = self._chat(payload)
            except httpx.HTTPStatusError:
                if not response_schema:
                    raise
                payload["format"] = "json"  # fallback: plain JSON mode
                data = self._chat(payload)
            content = (data.get("message") or {}).get("content", "") or ""
            tin = int(data.get("prompt_eval_count") or 0)
            tout = int(data.get("eval_count") or 0)
            return ModelResponse(
                content=content,
                usage=LLMUsage(input_tokens=tin, output_tokens=tout, total_tokens=tin + tout, estimated_cost_usd=0.0),
                model=self.model,
            )
        except Exception as exc:
            raise LLMProviderError(f"Ollama error: {exc}") from exc
