from __future__ import annotations


def test_load_llm_uses_kbench_model_proxy(monkeypatch):
    import kaggle_benchmarks.kaggle.model_proxy as model_proxy
    from kagraph.llms import load_llm

    captured = {}

    def fake_model_proxy(**kwargs):
        captured.update(kwargs)
        return "llm"

    monkeypatch.setenv("MODEL_PROXY_API_KEY", "key")
    monkeypatch.setenv("MODEL_PROXY_URL", "http://proxy")
    monkeypatch.setattr(model_proxy, "ModelProxy", fake_model_proxy)

    assert load_llm("provider/model", api="genai", support_temperature=True) == "llm"
    assert captured == {
        "model": "provider/model",
        "api": "genai",
        "api_key": "key",
        "base_url": "http://proxy",
        "support_temperature": True,
    }


def test_load_default_llm_delegates_to_kbench(monkeypatch):
    import kaggle_benchmarks.kaggle.models as models
    from kagraph.llms import load_default_llm

    monkeypatch.setattr(models, "load_default_model", lambda: "default")

    assert load_default_llm() == "default"
