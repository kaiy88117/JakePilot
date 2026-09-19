import sys
import types

from config import model_provider


def test_chat_model_reuses_hermes_environment_names(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ARK_API_KEY", "shared-key")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MODEL", "deepseek-flash")
    monkeypatch.setattr(model_provider, "ChatOpenAI", fake_chat_openai)

    model_provider.create_chat_model()

    assert captured["model"] == "deepseek-flash"
    assert captured["api_key"].get_secret_value() == "shared-key"
    assert captured["base_url"] == "https://api.deepseek.com"


def test_embedding_model_supports_ollama(monkeypatch):
    captured = {}

    class FakeOllamaEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = types.ModuleType("langchain_ollama")
    fake_module.OllamaEmbeddings = FakeOllamaEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:11434")

    model_provider.create_embedding_model()

    assert captured == {
        "model": "bge-m3",
        "base_url": "http://127.0.0.1:11434",
    }
