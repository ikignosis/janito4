"""
Tests for the Provider / ProviderRegistry classes (janito.provider_models /
janito.provider_registry).

Covers typed accessors, case-insensitive lookup, the whitespace distinction
between ``get`` (no strip, mirrors get_provider_config) and ``canonical_name``
(strips), runtime mutation of ``janito.providers._PROVIDER_CONFIGS``, and
validation errors.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import janito.config_dir as config_dir_mod
import janito.provider_accessors as pa
import janito.provider_validation as pv
import janito.providers as pvd
from janito.provider_models import Provider
from janito.provider_registry import ProviderRegistry

if pytest is not None:

    def test_provider_accessors():
        p = Provider("alibaba")
        assert p.name == "alibaba"
        assert p.default_model() == "qwen3.8-max"
        assert p.reasoning_level() == "xhigh"
        assert p.default_thinking() is True
        assert p.supported_api_types() == ["Completions", "Responses", "DashScope"]
        assert p.default_api_type() == "Responses"
        assert p.is_custom is False

    def test_provider_custom():
        p = Provider("custom")
        assert p.is_custom is True
        assert p.default_model() is None
        assert p.max_input_tokens() is None

    def test_provider_unknown_raises():
        with pytest.raises(ValueError):
            Provider("bogus")

    def test_provider_endpoint_for():
        p = Provider("anthropic")
        assert p.endpoint_for("Completions") == "https://api.anthropic.com/v1/"
        assert p.endpoint_for("Anthropic") == "https://api.anthropic.com"
        # Multi-entry map: an absent API type falls back to the built-in endpoint.
        assert p.endpoint_for("Responses") == "https://api.anthropic.com/v1/"

    def test_registry_get_case_insensitive():
        reg = ProviderRegistry()
        assert reg.get("openai").name == "openai"
        assert reg.get("OpenAI").name == "openai"
        # get() does NOT strip whitespace (mirrors get_provider_config).
        assert reg.get("  MiniMax ") is None
        assert reg.get("bogus") is None
        assert reg.get("") is None

    def test_registry_canonical_name_strips():
        reg = ProviderRegistry()
        assert reg.canonical_name("  MiniMax ") == "minimax"
        assert reg.canonical_name("  ") is None
        assert reg.canonical_name(None) is None

    def test_registry_require():
        reg = ProviderRegistry()
        assert reg.require("OpenAI").name == "openai"
        with pytest.raises(ValueError) as exc:
            reg.require("bogus")
        assert "Supported providers" in str(exc.value)
        for name in pv.list_supported_providers():
            assert name in str(exc.value)

    def test_registry_names():
        reg = ProviderRegistry()
        assert reg.names() == pv.list_supported_providers()

    def test_registry_reflects_runtime_mutations():
        """The registry holds a reference (never a copy) to _PROVIDER_CONFIGS,
        so injecting/restoring a provider is visible to every lookup."""
        reg = ProviderRegistry()
        original = dict(pvd._PROVIDER_CONFIGS)
        pvd._PROVIDER_CONFIGS["fake-provider"] = {
            "default_model": "fake-model",
            "endpoint": None,
            "models": {
                "fake-model": {
                    "supported_api_types": ["Completions"],
                    "max_input_tokens": None,
                    "max_output_tokens": None,
                }
            },
        }
        try:
            assert reg.get("fake-provider") is not None
            assert reg.get("fake-provider").default_model() == "fake-model"
            assert reg.canonical_name("Fake-Provider") == "fake-provider"
            assert "fake-provider" in reg.names()
        finally:
            pvd._PROVIDER_CONFIGS.clear()
            pvd._PROVIDER_CONFIGS.update(original)
        assert reg.get("fake-provider") is None

    def test_registry_requires_reference():
        reg = ProviderRegistry()
        assert reg.requires is pvd.REQUIRES_BY_API_TYPE

    def test_module_functions_agree_with_registry():
        """The module-level accessors behave identically to the class API."""
        reg = ProviderRegistry()
        assert pa.get_provider_config("minimax") == reg.get("minimax").info
        assert (
            pa.get_base_url_from_provider("minimax")
            == reg.get("minimax").info["endpoint"]
        )
        assert (
            pa.get_default_model_from_provider("openai")
            == reg.get("openai").default_model()
        )
        assert (
            pa.get_default_thinking_from_provider("deepseek")
            == reg.get("deepseek").default_thinking()
        )
        assert (
            pa.get_default_api_type_from_provider("anthropic")
            == reg.get("anthropic").default_api_type()
        )
        assert pv.list_supported_providers() == reg.names()
        assert pv.validate_provider_name("OpenAI") == reg.require("OpenAI").name
        assert pv.canonical_provider_name("  MiniMax ") == reg.canonical_name(
            "  MiniMax "
        )

    def test_responses_in_server_override_honored(monkeypatch, tmp_path):
        """Provider.responses_in_server() honors a model-scoped config override
        (and the module function delegates to it)."""
        import janito.config_store as gc

        monkeypatch.setattr(config_dir_mod, "_config_dir", tmp_path)
        gc.set_config_value("openai.models.gpt-5.6-luna.responses-in-server", False)
        assert Provider("openai").responses_in_server() is False
        assert pa.get_responses_in_server_from_provider("openai") is False

    def test_get_provider_cost():
        """get_provider_cost() delegates to the provider's cost module and
        renders the estimate with the adaptive magnitude-aware format."""
        from datetime import datetime, timezone

        # Weekday (Monday 2026-08-17 in Beijing Time) off-peak (12:00 UTC)
        # and peak (08:00 UTC) request times: the peak/off-peak split only
        # applies on weekdays.
        off_peak = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        peak = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
        # Weekend (Saturday 2026-08-22 in Beijing Time) request time inside
        # the weekday peak window: weekends charge the off-peak rate all day.
        weekend_peak = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)
        # DeepSeek ships a cost module: V4-Flash at $0.22 / $0.007 (cache
        # hit) / $0.66 output per 1M tokens (off-peak); the estimate is
        # rendered with the adaptive format plus the applied rate band.
        assert (
            pa.get_provider_cost(
                "deepseek",
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=off_peak,
            )
            == "88.0\xa2 (off-peak)"
        )
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost(
                "deepseek",
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                500_000,
                now=off_peak,
            )
            == "77.3\xa2 (off-peak)"
        )
        # Case-insensitive provider lookup (V4-Pro at $0.66 / $1.98).
        assert (
            pa.get_provider_cost(
                "DeepSeek", "deepseek-v4-pro", 1_000_000, 1_000_000, 0, now=off_peak
            )
            == "2.6$ (off-peak)"
        )
        # Peak-hour requests are billed at exactly double the off-peak rates.
        assert (
            pa.get_provider_cost(
                "deepseek",
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=peak,
            )
            == "1.8$ (peak)"
        )
        # Weekend requests are billed at the off-peak rate all day, even at
        # what would be a weekday peak hour (08:00 UTC).
        assert (
            pa.get_provider_cost(
                "deepseek",
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=weekend_peak,
            )
            == "88.0\xa2 (off-peak)"
        )
        # Alibaba ships a cost module: qwen3.8-max at $2 / $0.25 (implicit
        # cache hit) / $6 output per 1M tokens.
        assert (
            pa.get_provider_cost("alibaba", "qwen3.8-max", 1_000_000, 1_000_000, 0)
            == "8.0$"
        )
        # Cached input tokens are billed at the implicit cache-hit rate.
        assert (
            pa.get_provider_cost(
                "alibaba", "qwen3.8-max", 1_000_000, 1_000_000, 500_000
            )
            == "7.1$"
        )
        # qwen3.8-flash at $0.15 / $0.016 (implicit cache hit) / $0.47
        # output per 1M tokens.
        assert (
            pa.get_provider_cost("alibaba", "qwen3.8-flash", 1_000_000, 1_000_000, 0)
            == "62.0\xa2"
        )
        # Cached input tokens are billed at the implicit cache-hit rate.
        assert (
            pa.get_provider_cost(
                "alibaba", "qwen3.8-flash", 1_000_000, 1_000_000, 500_000
            )
            == "55.3\xa2"
        )
        # Moonshot ships a cost module: kimi-k3 at $2.75 / $0.28 (cache
        # hit) / $13.75 output per 1M tokens.
        assert (
            pa.get_provider_cost("moonshot", "kimi-k3", 1_000_000, 1_000_000, 0)
            == "16.5$"
        )
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost("moonshot", "kimi-k3", 1_000_000, 1_000_000, 500_000)
            == "15.3$"
        )
        # Case-insensitive provider lookup.
        assert (
            pa.get_provider_cost("Moonshot", "kimi-k3", 1_000_000, 1_000_000, 0)
            == "16.5$"
        )
        # Google ships a cost module: gemini-3.7-flash at $0.75 / $0.1875
        # (context cache read) / $3.75 output per 1M tokens.
        assert (
            pa.get_provider_cost("google", "gemini-3.7-flash", 1_000_000, 1_000_000, 0)
            == "4.5$"
        )
        # Cached input tokens are billed at the context cache read rate.
        assert (
            pa.get_provider_cost(
                "google", "gemini-3.7-flash", 1_000_000, 1_000_000, 500_000
            )
            == "4.2$"
        )
        # Case-insensitive provider lookup.
        assert (
            pa.get_provider_cost("Google", "gemini-3.7-flash", 1_000_000, 1_000_000, 0)
            == "4.5$"
        )
        # Z.ai ships a cost module: glm-5.3 at $1.40 / $0.26 (cache hit) /
        # $4.40 output per 1M tokens.
        assert pa.get_provider_cost("zai", "glm-5.3", 1_000_000, 1_000_000, 0) == "5.8$"
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost("zai", "glm-5.3", 1_000_000, 1_000_000, 500_000)
            == "5.2$"
        )
        # Case-insensitive provider lookup.
        assert pa.get_provider_cost("Zai", "glm-5.3", 1_000_000, 1_000_000, 0) == "5.8$"
        # GLM-5.3-Flash (default) at the 50% launch-promo price: $0.075 /
        # $0.015 (cache hit) / $0.25 output per 1M tokens.
        assert (
            pa.get_provider_cost("zai", "glm-5.3-flash", 1_000_000, 1_000_000, 0)
            == "32.5\xa2"
        )
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost("zai", "glm-5.3-flash", 1_000_000, 1_000_000, 500_000)
            == "29.5\xa2"
        )
        # Xiaomi ships a cost module: mimo-v2.5 at $0.14 / $0.0028 (cache
        # hit) / $0.28 output per 1M tokens.
        assert (
            pa.get_provider_cost("xiaomi", "mimo-v2.5", 1_000_000, 1_000_000, 0)
            == "42.0\xa2"
        )
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost("xiaomi", "mimo-v2.5", 1_000_000, 1_000_000, 500_000)
            == "35.1\xa2"
        )
        # Case-insensitive provider lookup.
        assert (
            pa.get_provider_cost("Xiaomi", "mimo-v2.5", 1_000_000, 1_000_000, 0)
            == "42.0\xa2"
        )
        # OpenAI ships a cost module: gpt-5.6-luna at $0.20 / $0.02 (cache
        # read) / $1.20 output per 1M tokens.  Standard request
        # (input <= 272K): 100k * $0.20 + 1M * $1.20 = 1.22 -> 1.2$.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-luna", 100_000, 1_000_000, 0)
            == "1.2$"
        )
        # Cached input tokens are billed at the cache-read rate.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-luna", 100_000, 1_000_000, 40_000)
            == "1.2$"
        )
        # High-context prompts (> 272K input tokens) bill the whole request
        # at 2x input ($0.40) and 1.5x output ($1.80):
        # 300k * $0.40 + 1M * $1.80 = 1.92 -> 1.9$.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-luna", 300_000, 1_000_000, 0)
            == "1.9$"
        )
        # The GPT-5.6 family also covers Sol and Terra:
        # sol: 100k * $4.00 + 1M * $20.00 = 20.40 -> 20.4$.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-sol", 100_000, 1_000_000, 0)
            == "20.4$"
        )
        # terra: 100k * $2.00 + 1M * $12.00 = 12.20 -> 12.2$.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-terra", 100_000, 1_000_000, 0)
            == "12.2$"
        )
        # terra cached reads bill at the cache-read rate ($0.20):
        # 60k * $2.00 + 40k * $0.20 + 1M * $12.00 = 12.128 -> 12.1$.
        assert (
            pa.get_provider_cost("openai", "gpt-5.6-terra", 100_000, 1_000_000, 40_000)
            == "12.1$"
        )
        # xAI ships a cost module: grok-4.6 at $2.00 / $0.50 (cache hit) /
        # $6.00 output per 1M tokens.  Standard request (input <= 200K):
        # 100k * $2.00 + 1M * $6.00 = 6.20 -> 6.2$.
        assert pa.get_provider_cost("xai", "grok-4.6", 100_000, 1_000_000, 0) == "6.2$"
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost("xai", "grok-4.6", 100_000, 1_000_000, 40_000)
            == "6.1$"
        )
        # Long-context prompts (> 200K input tokens) bill the whole request
        # at 2x input ($4.00) and 2x output ($12.00):
        # 300k * $4.00 + 1M * $12.00 = 13.20 -> 13.2$.
        assert pa.get_provider_cost("xai", "grok-4.6", 300_000, 1_000_000, 0) == "13.2$"
        # Case-insensitive provider lookup.
        assert pa.get_provider_cost("Xai", "grok-4.6", 100_000, 1_000_000, 0) == "6.2$"
        # Anthropic ships a cost module: claude-sonnet-5 at $2 / $0.20 (cache
        # hit) / $10 output per 1M tokens.
        assert (
            pa.get_provider_cost(
                "anthropic", "claude-sonnet-5", 1_000_000, 1_000_000, 0
            )
            == "12.0$"
        )
        # Cached input tokens are billed at the cache-hit rate.
        assert (
            pa.get_provider_cost(
                "anthropic", "claude-sonnet-5", 1_000_000, 1_000_000, 500_000
            )
            == "11.1$"
        )
        # Case-insensitive provider lookup.
        assert (
            pa.get_provider_cost(
                "Anthropic", "claude-sonnet-5", 1_000_000, 1_000_000, 0
            )
            == "12.0$"
        )
        # claude-opus-5 at $5 / $0.50 (cache hit) / $25 output per 1M tokens.
        assert (
            pa.get_provider_cost("anthropic", "claude-opus-5", 1_000_000, 1_000_000, 0)
            == "30.0$"
        )
        # claude-fable-5 at $10 / $1 (cache hit) / $50 output per 1M tokens.
        assert (
            pa.get_provider_cost("anthropic", "claude-fable-5", 1_000_000, 1_000_000, 0)
            == "60.0$"
        )
        # Unknown models within the provider fall back to "N/A".
        assert pa.get_provider_cost("deepseek", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("alibaba", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("google", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("moonshot", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("openai", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("xiaomi", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("zai", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("xai", "bogus-model", 1000, 500, 100) == "N/A"
        assert pa.get_provider_cost("anthropic", "bogus-model", 1000, 500, 100) == "N/A"
        # Unknown providers fall back to "N/A".
        assert pa.get_provider_cost("bogus", "model", 1000, 500, 100) == "N/A"

    def test_format_cost_adaptive():
        """_format_cost renders each magnitude band with the adaptive format."""
        assert pa._format_cost(0.0001234) == "0.012\xa2"  # < 1 cent: 0.abc\xa2
        assert pa._format_cost(0.005) == "0.500\xa2"
        assert pa._format_cost(0.01) == "1.0\xa2"  # >= 1 cent: X.a\xa2
        assert pa._format_cost(0.88) == "88.0\xa2"
        assert pa._format_cost(0.999) == "99.9\xa2"
        assert pa._format_cost(1.0) == "1.0$"  # >= 1$: X.a$
        assert pa._format_cost(12.345) == "12.3$"
        assert pa._format_cost(99.9) == "99.9$"
        assert pa._format_cost(100.0) == "100$"  # >= 100$: X$
        assert pa._format_cost(123.456) == "123$"

    def test_format_cost_rounding_boundary_promotion():
        """Values that round across a unit boundary promote to the next unit."""
        # 0.9999 cents rounds to a full cent -> 1.0\xa2 (never 1.000\xa2).
        assert pa._format_cost(0.009999) == "1.0\xa2"
        # 99.96$ rounds to 100 -> 100$ (never 100.0$).
        assert pa._format_cost(99.96) == "100$"
        assert pa._format_cost(99.99) == "100$"

    def test_adapt_cost_string_preserves_annotation_and_na():
        """_adapt_cost_string re-renders the numeric part and keeps the band."""
        assert pa._adapt_cost_string("0.880000$ (off-peak)") == "88.0\xa2 (off-peak)"
        assert pa._adapt_cost_string("1.760000$ (peak)") == "1.8$ (peak)"
        assert pa._adapt_cost_string("6.250000$") == "6.2$"
        assert pa._adapt_cost_string("N/A") == "N/A"

    def test_provider_get_cost_accepts_is_reference():
        """Every provider's get_cost() accepts the is_reference parameter."""
        from datetime import datetime, timezone

        from janito.providers.alibaba.cost import get_cost as alibaba_get_cost
        from janito.providers.anthropic.cost import get_cost as anthropic_get_cost
        from janito.providers.deepseek.cost import get_cost as deepseek_get_cost
        from janito.providers.google.cost import get_cost as google_get_cost
        from janito.providers.minimax.cost import get_cost as minimax_get_cost
        from janito.providers.moonshot.cost import get_cost as moonshot_get_cost
        from janito.providers.openai.cost import get_cost as openai_get_cost
        from janito.providers.xiaomi.cost import get_cost as xiaomi_get_cost
        from janito.providers.zai.cost import get_cost as zai_get_cost

        off_peak = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        # Anthropic also ignores is_reference (estimate unchanged).
        # 1M * $2 + 1M * $10 = 12.00.
        assert (
            anthropic_get_cost(
                "claude-sonnet-5", 1_000_000, 1_000_000, 0, is_reference=True
            )
            == "12.000000$"
        )
        # Alibaba, Google, and MiniMax ignore is_reference (estimate unchanged).
        assert (
            alibaba_get_cost("qwen3.8-max", 1_000_000, 1_000_000, 0, is_reference=True)
            == "8.000000$"
        )
        assert (
            alibaba_get_cost(
                "qwen3.8-flash", 1_000_000, 1_000_000, 0, is_reference=True
            )
            == "0.620000$"
        )
        assert (
            google_get_cost(
                "gemini-3.7-flash", 1_000_000, 1_000_000, 0, is_reference=True
            )
            == "4.500000$"
        )
        assert (
            minimax_get_cost("MiniMax-M3", 100_000, 100_000, 0, is_reference=True)
            == "0.150000$"
        )
        # Moonshot also ignores is_reference (estimate unchanged).
        assert (
            moonshot_get_cost("kimi-k3", 1_000_000, 1_000_000, 0, is_reference=True)
            == "16.500000$"
        )
        # OpenAI also ignores is_reference (estimate unchanged).
        # 1M input exceeds the 272K high-context threshold, so the whole
        # request is billed at 2x input ($0.40) / 1.5x output ($1.80):
        # 1M * $0.40 + 1M * $1.80 = 2.20.
        assert (
            openai_get_cost("gpt-5.6-luna", 1_000_000, 1_000_000, 0, is_reference=True)
            == "2.200000$"
        )
        # Xiaomi also ignores is_reference (estimate unchanged).
        # 1M * $0.14 + 1M * $0.28 = 0.42.
        assert (
            xiaomi_get_cost("mimo-v2.5", 1_000_000, 1_000_000, 0, is_reference=True)
            == "0.420000$"
        )
        # Z.ai also ignores is_reference (estimate unchanged).
        # 1M * $1.40 + 1M * $4.40 = 5.80.
        assert (
            zai_get_cost("glm-5.3", 1_000_000, 1_000_000, 0, is_reference=True)
            == "5.800000$"
        )
        # GLM-5.3-Flash: 1M * $0.075 + 1M * $0.25 = 0.325.
        assert (
            zai_get_cost("glm-5.3-flash", 1_000_000, 1_000_000, 0, is_reference=True)
            == "0.325000$"
        )
        # DeepSeek bills reference requests at the peak rates (double the
        # off-peak: (0.22 + 0.66) * 2 = 1.76) and omits the rate-band suffix.
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=off_peak,
                is_reference=True,
            )
            == "1.760000$"
        )

    def test_openai_high_context_boundary():
        """Requests at 272K input tokens use standard rates; above use 2x/1.5x."""
        from janito.providers.openai.cost import get_cost as openai_get_cost

        # Exactly 272K input tokens: standard rates (0.20 input, 1.20 output).
        assert openai_get_cost("gpt-5.6-luna", 272_000, 1_000, 0) == "0.055600$"
        # One token more: the whole request is billed at high-context rates
        # (272001 * 0.40 + 1000 * 1.80) / 1M = 0.110600.
        assert openai_get_cost("gpt-5.6-luna", 272_001, 1_000, 0) == "0.110600$"

    def test_minimax_cost_standard_and_high_context():
        """MiniMax-M3 uses standard rates <= 512K and 2x rates > 512K."""
        from janito.providers.minimax.cost import get_cost as minimax_get_cost

        # Standard rates (<= 512K tokens): $0.30/1M input, $0.06/1M cached, $1.20/1M output
        # Exactly 512K input tokens: (512,000 * 0.30 + 1,000 * 1.20) / 1M = 0.154800$
        assert minimax_get_cost("MiniMax-M3", 512_000, 1_000, 0) == "0.154800$"
        # With cached input tokens
        # (400,000 * 0.30 + 100,000 * 0.06 + 10,000 * 1.20) / 1M = 0.138000$
        assert minimax_get_cost("MiniMax-M3", 500_000, 10_000, 100_000) == "0.138000$"
        # High-context (> 512K tokens): 2x input ($0.60), 2x cache ($0.12), 2x output ($2.40)
        # (512,001 * 0.60 + 1,000 * 2.40) / 1M = 0.309601$
        assert minimax_get_cost("MiniMax-M3", 512_001, 1_000, 0) == "0.309601$"
        # Unknown model returns "N/A"
        assert minimax_get_cost("unknown-model", 1000, 1000, 0) == "N/A"

    def test_deepseek_reference_uses_peak_rates_without_suffix():
        """DeepSeek reference requests bill at peak rates and drop the band."""
        from datetime import datetime, timezone

        from janito.providers.deepseek.cost import get_cost as deepseek_get_cost

        # 2026-08-16 is a Sunday in Beijing Time: weekends normally bill the
        # off-peak rate all day, but reference requests override that and
        # still bill at the peak (double) rates without a rate-band suffix.
        off_peak = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        peak = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
        # Off-peak reference request: billed as peak (double), no suffix.
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=off_peak,
                is_reference=True,
            )
            == "1.760000$"
        )
        # Peak reference request: same cost, still no suffix.
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=peak,
                is_reference=True,
            )
            == "1.760000$"
        )
        # Cached input tokens still bill at the cache-hit rate (peak double):
        # (0.5M * 0.22 + 0.5M * 0.007 + 1M * 0.66) / 1M * 2 = 1.547.
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                500_000,
                now=off_peak,
                is_reference=True,
            )
            == "1.547000$"
        )

    def test_deepseek_weekend_off_peak_all_day():
        """DeepSeek bills the off-peak rate all day on weekends (Beijing Time)."""
        from datetime import datetime, timezone

        from janito.providers.deepseek.cost import get_cost as deepseek_get_cost

        # Weekday (Monday 2026-08-17 in Beijing Time): the peak/off-peak
        # split remains in effect.
        mon_off_peak = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        mon_peak = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
        # Weekend (Saturday 2026-08-22 / Sunday 2026-08-16 in Beijing Time):
        # 08:00 UTC is inside the weekday peak window; 12:00 UTC is off-peak.
        sat_peak = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)
        sat_off_peak = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        sun_peak = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
        # Weekday: off-peak hour stays off-peak, peak hour stays peak (2x).
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash", 1_000_000, 1_000_000, 0, now=mon_off_peak
            )
            == "0.880000$ (off-peak)"
        )
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash", 1_000_000, 1_000_000, 0, now=mon_peak
            )
            == "1.760000$ (peak)"
        )
        # Weekend: uniform off-peak rate for the whole day, including what
        # would be a weekday peak hour (08:00 UTC).
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash", 1_000_000, 1_000_000, 0, now=sat_peak
            )
            == "0.880000$ (off-peak)"
        )
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash", 1_000_000, 1_000_000, 0, now=sat_off_peak
            )
            == "0.880000$ (off-peak)"
        )
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash", 1_000_000, 1_000_000, 0, now=sun_peak
            )
            == "0.880000$ (off-peak)"
        )
        # Reference requests still bill at the peak rates on weekends and
        # drop the rate-band suffix.
        assert (
            deepseek_get_cost(
                "deepseek-v4-flash",
                1_000_000,
                1_000_000,
                0,
                now=sat_peak,
                is_reference=True,
            )
            == "1.760000$"
        )

    def test_get_provider_cost_forwards_is_reference(monkeypatch):
        """get_provider_cost() forwards is_reference to the provider's get_cost."""
        captured = {}

        def fake_get_cost(model, input, output, cached, now=None, is_reference=False):
            captured["is_reference"] = is_reference
            return "0.000000$"

        monkeypatch.setattr("janito.providers.deepseek.cost.get_cost", fake_get_cost)
        pa.get_provider_cost(
            "deepseek", "deepseek-v4-flash", 1000, 500, 100, is_reference=True
        )
        assert captured["is_reference"] is True
        # The parameter defaults to False.
        pa.get_provider_cost("deepseek", "deepseek-v4-flash", 1000, 500, 100)
        assert captured["is_reference"] is False

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
