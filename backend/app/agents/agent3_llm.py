"""Adapter: repo's ``structured_call`` -> Agent 3's ``StructuredLLM`` protocol.

Kept in its own module so the pure scheduling code/tests never import app.config.
Usage:
    from app.llm.factory import get_chat_model   # whatever the factory exposes
    llm = make_structured_llm(get_chat_model())
    run_agent3(tasks, members, start, llm=llm)
"""
from __future__ import annotations

import json
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from app.llm.contracts import ChatModel
from app.llm.structured import structured_call

T = TypeVar("T", bound=BaseModel)


def make_structured_llm(model: ChatModel, *, max_attempts: int = 3):
    def call(*, system_prompt: str, payload: dict[str, Any], schema: Type[T]) -> T:
        messages = [
            {"role": "system", "content": system_prompt},
            # payload is untrusted data; the system prompt says so explicitly
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ]
        return structured_call(
            model=model, messages=messages, schema=schema, max_attempts=max_attempts
        ).value

    return call
