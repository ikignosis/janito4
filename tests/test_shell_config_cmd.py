"""Tests for the shell /status command handler."""

from unittest.mock import patch

from janito.shell.cmds.status import _print_config_info


class TestPrintConfigInfo:
    """Tests for _print_config_info display logic (tokens, thinking, API type)."""

    def _run(
        self,
        capsys,
        provider=None,
        configured_max_tokens=None,
        default_max_tokens=128000,
        thinking=False,
        api_type="Responses",
        responses_in_server=True,
        cli_api_type=None,
        model=None,
        configured_model=None,
        default_model="gpt-5.6-luna",
    ):
        """Helper: patch config lookups and capture printed output.

        Args:
            provider: The session provider to pass to ``_print_config_info``.
                When None, the (patched) configured default is used.
            thinking: The ``--thinking`` CLI flag passed to ``_print_config_info``.
            api_type: The effective API type returned by ``resolve_api_type``.
            responses_in_server: Value returned by
                ``get_responses_in_server_from_provider`` (only meaningful when
                ``api_type`` is ``Responses``).
            cli_api_type: The ``--api-type`` CLI flag passed to
                ``_print_config_info`` (``None`` when the flag was not given).
            model: The session's effective model passed to
                ``_print_config_info`` (``None`` when it has to be resolved
                from the provider configuration).
            configured_model: Value returned by ``load_model_from_config``
                (the provider's configured model in config.json).
            default_model: Value returned by
                ``get_default_model_from_provider`` (the provider's built-in
                default model).
        """
        # Captures the arguments ``_print_config_info`` forwards to
        # ``resolve_api_type`` so tests can assert the session's ``--api-type``
        # reaches the API-type resolution.
        self._last_resolve_call = {}

        def _fake_resolve_api_type(resolved_cli_api_type, resolved_provider, model):
            self._last_resolve_call["cli_api_type"] = resolved_cli_api_type
            self._last_resolve_call["provider"] = resolved_provider
            self._last_resolve_call["model"] = model
            return api_type

        with (
            patch(
                "janito.shell.cmds.status.get_active_provider",
                return_value="openai",
            ),
            patch(
                "janito.shell.cmds.status.get_api_key",
                return_value="sk-test-key-1234567890",
            ),
            patch(
                "janito.shell.cmds.status.get_masked_api_key",
                return_value="sk-***7890",
            ),
            patch(
                "janito.shell.cmds.status.load_model_from_config",
                return_value=configured_model,
            ),
            patch(
                "janito.shell.cmds.status.get_default_model_from_provider",
                return_value=default_model,
            ),
            patch(
                "janito.shell.cmds.status.load_max_output_tokens",
                return_value=configured_max_tokens,
            ),
            patch(
                "janito.shell.cmds.status.load_endpoint_from_config",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.get_default_max_output_tokens_from_provider",
                return_value=default_max_tokens,
            ),
            patch(
                "janito.shell.cmds.status.resolve_api_type",
                side_effect=_fake_resolve_api_type,
            ),
            patch(
                "janito.shell.cmds.status.get_responses_in_server_from_provider",
                return_value=responses_in_server,
            ),
        ):
            _print_config_info(provider, thinking, cli_api_type, model)
        return capsys.readouterr().out

    def test_explicit_max_output_tokens_shown_as_is(self, capsys):
        """When the user has set max output tokens, display them without suffix."""
        out = self._run(capsys, configured_max_tokens=65536)
        assert "Max Output Tokens" in out
        assert "65536" in out
        # The Max Output Tokens row shows the configured value without the
        # '(default)' marker (the Model row may legitimately carry it).
        tokens_line = next(line for line in out.splitlines() if "65536" in line)
        assert "(default)" not in tokens_line

    def test_falls_back_to_provider_default(self, capsys):
        """When not configured, the provider's built-in default is shown with '(default)'."""
        out = self._run(capsys, configured_max_tokens=None, default_max_tokens=128000)
        assert "128000 (default)" in out

    def test_not_set_when_no_default_available(self, capsys):
        """When neither configured nor a provider default exists, show '(not set)'."""
        out = self._run(capsys, configured_max_tokens=None, default_max_tokens=None)
        assert "Max Output Tokens" in out
        assert "(not set)" in out

    def test_session_provider_wins_over_configured_default(self, capsys):
        """An explicit session provider (e.g. --provider deepseek) is reported."""
        out = self._run(capsys, provider="deepseek")
        assert "deepseek" in out
        # The configured default ('openai') must not be shown instead.
        assert "openai" not in out

    def test_thinking_enabled_by_provider_default(self, capsys):
        """DeepSeek reasons by default: thinking shows 'enabled (model default)'."""
        out = self._run(capsys, provider="deepseek")
        assert "enabled (model default)" in out

    def test_thinking_disabled_by_default(self, capsys):
        """OpenAI has no default thinking: thinking shows 'disabled'."""
        out = self._run(capsys, provider="openai")
        assert "disabled" in out

    def test_thinking_gemini_flavor_shows_na(self, capsys):
        """Google uses Gemini flavor: thinking shows 'N/A (controlled via Reasoning Level)'."""
        out = self._run(capsys, provider="google")
        assert "N/A (controlled via Reasoning Level)" in out

    def test_thinking_flag_overrides_provider_default(self, capsys):
        """The --thinking flag forces thinking on without the '(model default)' note."""
        out = self._run(capsys, provider="openai", thinking=True)
        assert "enabled" in out
        assert "(model default)" not in out

    def test_responses_in_server_shown_for_server_side_provider(self, capsys):
        """Responses API + server-side state reports previous_response_id chaining."""
        out = self._run(capsys, api_type="Responses", responses_in_server=True)
        assert "API Type" in out
        assert "Responses" in out
        assert "Responses In Server" in out
        assert "server-side (previous_response_id)" in out

    def test_responses_in_server_stateless_for_deepseek(self, capsys):
        """DeepSeek's /responses endpoint is stateless."""
        out = self._run(
            capsys, provider="deepseek", api_type="Responses", responses_in_server=False
        )
        assert "Responses" in out
        assert "stateless (client re-sends history)" in out

    def test_responses_in_server_hidden_when_api_type_completions(self, capsys):
        """The line is omitted when the API type resolves to Completions."""
        out = self._run(capsys, provider="openai", api_type="Completions")
        assert "Completions" in out
        assert "Responses In Server" not in out

    def test_cli_api_type_forwarded_to_resolve_api_type(self, capsys):
        """The session's --api-type (e.g. Gemini) reaches resolve_api_type.

        Regression test: /status used to hard-code ``None`` as the CLI API
        type, so a session started with ``--api-type=Gemini`` displayed the
        provider's built-in default (``Completions`` for google) instead of
        the API type actually in use.
        """
        out = self._run(
            capsys, provider="google", cli_api_type="Gemini", api_type="Gemini"
        )
        assert self._last_resolve_call["cli_api_type"] == "Gemini"
        assert self._last_resolve_call["provider"] == "google"
        assert "Gemini" in out

    def test_no_cli_api_type_keeps_none_forwarded(self, capsys):
        """Without --api-type, None is forwarded so the config/default applies."""
        self._run(capsys, provider="google")
        assert self._last_resolve_call["cli_api_type"] is None

    # ------------------------------------------------------------------
    # Model row (session model vs provider default)
    # ------------------------------------------------------------------

    @staticmethod
    def _model_row(out: str) -> str:
        """Return the rendered ``Model`` table row (empty when absent)."""
        return next(
            (line for line in out.splitlines() if line.strip().startswith("Model")),
            "",
        )

    def test_session_model_shown(self, capsys):
        """The shell's session model (--model, /model) is shown as-is."""
        out = self._run(capsys, provider="alibaba", model="qwen3.8-max")
        assert "Model" in out
        assert "qwen3.8-max" in out
        assert "qwen3.8-flash" not in out
        assert "(default)" not in self._model_row(out)

    def test_session_model_used_for_model_scoped_settings(self, capsys):
        """Model-scoped resolution (API type) uses the session model."""
        self._run(capsys, provider="alibaba", model="qwen3.8-max")
        assert self._last_resolve_call["model"] == "qwen3.8-max"

    def test_provider_default_model_marked(self, capsys):
        """Without a session model, the provider's built-in default is marked.

        Regression test: /status used to omit the Model row entirely, so a
        session running an Alibaba variant showed no model at all (and
        model-scoped settings were silently resolved for the provider's
        built-in default, e.g. qwen3.8-flash for alibaba).
        """
        out = self._run(
            capsys,
            provider="alibaba",
            configured_model=None,
            default_model="qwen3.8-flash",
        )
        assert "qwen3.8-flash (default)" in self._model_row(out)

    def test_configured_model_used_without_session_model(self, capsys):
        """Without a session model, the provider's configured model is used."""
        out = self._run(
            capsys,
            provider="alibaba",
            configured_model="qwen3.8-max",
            default_model="qwen3.8-flash",
        )
        assert "qwen3.8-max" in self._model_row(out)
        assert "qwen3.8-flash" not in out
        # A configured model is not the built-in default: no marker.
        assert "(default)" not in self._model_row(out)

    def test_no_model_anywhere_shows_not_set(self, capsys):
        """A provider with no configured and no built-in model shows '(not set)'."""
        out = self._run(
            capsys,
            provider="custom",
            configured_model=None,
            default_model=None,
        )
        assert "(not set)" in self._model_row(out)


class TestStatusCmdHandlerApiType:
    """Tests for the /status handler forwarding the shell's api_type."""

    def test_status_handler_forwards_shell_api_type(self, capsys):
        """/status passes the shell's --api-type into the API type resolution."""
        from janito.shell.cmds.status import StatusCmdHandler

        calls = {}

        def fake_resolve_api_type(cli_api_type, provider, model):
            calls["cli_api_type"] = cli_api_type
            calls["provider"] = provider
            return "Gemini"

        class FakeShell:
            provider = "google"
            model = "qwen3.8-flash"
            thinking = False
            api_type = "Gemini"

        with (
            patch(
                "janito.shell.cmds.status.get_active_provider",
                return_value="openai",
            ),
            patch("janito.shell.cmds.status.get_api_key", return_value=""),
            patch(
                "janito.shell.cmds.status.get_masked_api_key",
                return_value="(not set)",
            ),
            patch(
                "janito.shell.cmds.status.load_max_output_tokens",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.load_endpoint_from_config",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.get_default_max_output_tokens_from_provider",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.resolve_api_type",
                side_effect=fake_resolve_api_type,
            ),
            patch(
                "janito.shell.cmds.status.get_responses_in_server_from_provider",
                return_value=True,
            ),
        ):
            assert StatusCmdHandler().handle(FakeShell(), "/status") is True

        assert calls["cli_api_type"] == "Gemini"
        assert calls["provider"] == "google"
        out = capsys.readouterr().out
        assert "Gemini" in out
        # The shell's session model is displayed instead of the provider's
        # configured/default model.
        assert "qwen3.8-flash" in out

    def test_status_handler_tolerates_missing_api_type(self, capsys):
        """Shells without an api_type attribute (older sessions) still work."""
        from janito.shell.cmds.status import StatusCmdHandler

        calls = {}

        def fake_resolve_api_type(cli_api_type, provider, model):
            calls["cli_api_type"] = cli_api_type
            calls["provider"] = provider
            calls["model"] = model
            return "Completions"

        class FakeShell:
            provider = "google"
            thinking = False
            model = "gemini-3-pro"

        with (
            patch(
                "janito.shell.cmds.status.get_active_provider",
                return_value="openai",
            ),
            patch("janito.shell.cmds.status.get_api_key", return_value=""),
            patch(
                "janito.shell.cmds.status.get_masked_api_key",
                return_value="(not set)",
            ),
            patch(
                "janito.shell.cmds.status.load_max_output_tokens",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.load_endpoint_from_config",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.get_default_max_output_tokens_from_provider",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.resolve_api_type",
                side_effect=fake_resolve_api_type,
            ),
            patch(
                "janito.shell.cmds.status.get_responses_in_server_from_provider",
                return_value=True,
            ),
        ):
            assert StatusCmdHandler().handle(FakeShell(), "/status") is True

        assert calls["cli_api_type"] is None
        assert calls["model"] == "gemini-3-pro"

    def test_status_handler_tolerates_missing_model(self, capsys):
        """Shells without a model attribute fall back to the provider's model."""
        from janito.shell.cmds.status import StatusCmdHandler

        class FakeShell:
            provider = "alibaba"
            thinking = False

        with (
            patch(
                "janito.shell.cmds.status.get_active_provider",
                return_value="alibaba",
            ),
            patch("janito.shell.cmds.status.get_api_key", return_value=""),
            patch(
                "janito.shell.cmds.status.get_masked_api_key",
                return_value="(not set)",
            ),
            patch(
                "janito.shell.cmds.status.load_model_from_config",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.get_default_model_from_provider",
                return_value="qwen3.8-flash",
            ),
            patch(
                "janito.shell.cmds.status.load_max_output_tokens",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.load_endpoint_from_config",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.get_default_max_output_tokens_from_provider",
                return_value=None,
            ),
            patch(
                "janito.shell.cmds.status.resolve_api_type",
                return_value="Responses",
            ),
            patch(
                "janito.shell.cmds.status.get_responses_in_server_from_provider",
                return_value=True,
            ),
        ):
            assert StatusCmdHandler().handle(FakeShell(), "/status") is True

        out = capsys.readouterr().out
        assert "qwen3.8-flash (default)" in out
