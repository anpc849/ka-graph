from __future__ import annotations

import os
from typing import Any


def load_default_llm():
    """Load the default kbench ModelProxy-backed LLM.

    KaGraph does not wrap or replace the model object. The returned object is
    the canonical ``kaggle_benchmarks.actors.LLMChat`` implementation.
    """

    from kaggle_benchmarks.kaggle.models import load_default_model

    return load_default_model()


def load_llm(model_id: str, *, api: str = "openai", **kwargs: Any):
    """Load a specific kbench ModelProxy-backed LLM by model id."""

    from kaggle_benchmarks.kaggle.model_proxy import ModelProxy

    return ModelProxy(
        model=model_id,
        api=api,
        api_key=os.getenv("MODEL_PROXY_API_KEY"),
        base_url=os.getenv("MODEL_PROXY_URL"),
        **kwargs,
    )
