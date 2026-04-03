from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from llm_provider_pool import LLMProvider


class FakeBuilder:
    def __init__(self) -> None:
        self.concurrent_updates_value = None

    def token(self, _value):
        return self

    def post_init(self, _value):
        return self

    def concurrent_updates(self, value):
        self.concurrent_updates_value = value
        return self

    def build(self):
        return SimpleNamespace(add_handler=lambda *args, **kwargs: None, add_error_handler=lambda *args, **kwargs: None)


def test_build_application_uses_default_sequential_updates(tmp_path) -> None:
    fake_builder = FakeBuilder()
    settings = SimpleNamespace(
        telegram_token="token",
        database_dsn=str(tmp_path / "app.db"),
        openrouter_api_key="",
        openrouter_model="",
        openrouter_web_search=False,
        gemini_api_key="",
        groq_api_key="",
        travelpayouts_api_key="",
        travelpayouts_marker="",
        travelpayouts_trs="",
        log_level="INFO",
    )

    with patch.object(app_module, "load_settings", return_value=settings), patch.object(
        app_module,
        "ApplicationBuilder",
        return_value=fake_builder,
    ), patch.object(app_module, "build_housing_provider", return_value=SimpleNamespace()):
        app_module.build_application()

    assert fake_builder.concurrent_updates_value is None


def test_build_application_uses_llm_planner_when_provider_pool_is_available(tmp_path) -> None:
    fake_builder = FakeBuilder()
    settings = SimpleNamespace(
        telegram_token="token",
        database_dsn=str(tmp_path / "app.db"),
        openrouter_api_key="",
        openrouter_model="",
        openrouter_web_search=False,
        gemini_api_key="gemini-key",
        groq_api_key="",
        travelpayouts_api_key="",
        travelpayouts_marker="",
        travelpayouts_trs="",
        log_level="INFO",
    )
    providers = [
        LLMProvider(
            name="Gemini",
            daily_limit=1500,
            api_key="gemini-key",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model="gemini-2.0-flash",
            use_web_search=True,
        )
    ]

    with patch.object(app_module, "load_settings", return_value=settings), patch.object(
        app_module,
        "ApplicationBuilder",
        return_value=fake_builder,
    ), patch.object(
        app_module,
        "build_housing_provider",
        return_value=SimpleNamespace(),
    ), patch.object(
        app_module,
        "build_provider_list",
        return_value=providers,
    ), patch.object(app_module, "LLMProviderPool") as pool_cls, patch.object(
        app_module,
        "LLMTravelPlanner",
    ) as planner_cls:
        app_module.build_application()

    pool_cls.assert_called_once_with(providers)
    planner_cls.assert_called_once()
