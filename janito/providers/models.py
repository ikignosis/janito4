"""
Typed provider/model accessors.

Defines :class:`ModelConfig` and :class:`Provider` -- the typed accessors
over the static provider registry
(:data:`janito.providers._PROVIDER_CONFIGS`).
Part of the split provider-config module family (see
:mod:`janito.providers.registry`).
"""

from . import _PROVIDER_CONFIGS


class ModelConfig:
    """Typed accessors over one model entry of
    ``_PROVIDER_CONFIGS[...]["models"]``.

    Wraps a raw model entry dict (e.g. ``{"supported_api_types": [...],
    "max_output_tokens": 128000, ...}``); an empty dict (unknown model /
    provider without built-in models) yields ``None``/empty defaults from
    every accessor.
    """

    def __init__(self, data: dict | None):
        self._data = data if isinstance(data, dict) else {}

    @property
    def data(self) -> dict:
        """The raw model entry dict backing this config."""
        return self._data

    def max_input_tokens(self) -> int | None:
        """The built-in max input-token (context window) limit, or ``None``."""
        return self._data.get("max_input_tokens")

    def max_output_tokens(self) -> int | None:
        """The built-in max output-token limit, or ``None``."""
        return self._data.get("max_output_tokens")

    def supported_api_types(self) -> list | None:
        """The API types the model supports, or ``None``."""
        return self._data.get("supported_api_types")

    def default_api_type(self) -> str | None:
        """The built-in default API type, from the model's ``default_api_type`` entry.

        The model entry declares it explicitly (usually the first of its
        ``supported_api_types``, e.g. ``"Responses"`` for OpenAI's default
        model).  ``None`` when the model declares no default (unknown model /
        provider without built-in models).
        """
        return self._data.get("default_api_type")

    def reasoning_effort(self) -> str | None:
        """The built-in default reasoning effort, or ``None``."""
        return self._data.get("default_reasoning_effort")

    def supported_reasoning_efforts(self) -> list | None:
        """The list of supported reasoning efforts, or ``None``."""
        return self._data.get("supported_reasoning_efforts")

    def default_thinking(self):
        """The model's built-in thinking default, or ``False`` when none.

        Returns the raw ``thinking`` value from the model entry: a plain
        ``True`` flag for flag-style providers (DeepSeek, Alibaba/Qwen) or a
        pass-through dict for providers whose API takes a structured
        thinking parameter (MiniMax-M3: ``{'type': 'adaptive'}``).  Callers
        must not coerce the dict to a bool -- use
        :func:`~janito.providers.payloads.apply_thinking_to_extra_body` to
        turn the value into the API's ``extra_body`` payload.
        """
        return self._data.get("thinking", False)

    def preserve_thinking(self) -> bool | None:
        """The model's built-in ``preserve_thinking`` default, or ``None``.

        ``preserve_thinking`` is a Qwen extension (see the QwenCloud
        Thinking guide): when ``True``, the API appends the assistant
        messages' ``reasoning_content`` to the next input in multi-turn
        conversations, letting the model reference its own prior reasoning.
        The built-in Qwen models (``qwen3.8-max`` / ``qwen3.8-flash``)
        declare it ``True``; other models declare nothing (``None``), so the
        callers send no flag and the API's own default applies.
        """
        return self._data.get("preserve_thinking")

    def tools(self, api_type: str | None = None) -> list | None:
        """The model's built-in (native) tool entries, or ``None``.

        Returns the raw ``tools`` value from the model entry (e.g. ``[
        {"type": "code_interpreter"}, {"type": "web_search"},
        {"type": "web_extractor"}]`` for Alibaba/Qwen's flagship).  These
        are not function tools: each ``type`` is enabled through
        request-body flags on the API call -- see
        :meth:`janito.providers.models.Provider.tools`.

        When ``api_type`` is given and the model declares a
        ``tools_by_api_type`` map containing it, that API type's own list is
        returned (API types absent from the map resolve to the plain
        ``tools`` default, or ``None`` when no default exists).
        """
        by_type = self._data.get("tools_by_api_type")
        if api_type and isinstance(by_type, dict) and api_type in by_type:
            return by_type[api_type]
        return self._data.get("tools")

    def stateless_mode(self) -> bool:
        """Whether the model's Responses API keeps state server-side.

        Absent defaults to ``True`` (the Responses API design).
        """
        return bool(self._data.get("stateless_mode", False))

    def responses_include(self) -> list | None:
        """Extra ``include`` values to request on every Responses call.

        A list of strings (e.g. ``["reasoning.encrypted_content"]`` for
        Meta's Muse Spark, whose chain of thought is only exposed in
        encrypted form for replay across turns), or ``None`` when the model
        declares none (no ``include`` parameter is sent).
        """
        value = self._data.get("responses_include")
        if isinstance(value, (list, tuple)):
            return [str(entry) for entry in value]
        return None

    def thinking_summary(self) -> bool:
        """Whether to request a Responses reasoning summary.

        When ``True`` (e.g. Meta's Muse Spark), Responses calls send
        ``reasoning.summary="auto"`` so the private chain of thought is
        returned as human-readable summary text streamed via
        ``response.reasoning_summary_text.delta`` events and surfaced
        through the existing ``on_reasoning`` observer.  Absent/``False``
        sends no summary (the API's own default applies).
        """
        return bool(self._data.get("thinking_summary", False))


class Provider:
    """A supported provider from :data:`janito.providers._PROVIDER_CONFIGS` with typed accessors.

    A provider may be a built-in provider (``name`` in ``_PROVIDER_CONFIGS``)
    or a registered *variant* (``<provider>-<word>``): in that case the
    variant name is kept as :attr:`name`, ``variant_of`` names the base
    provider and every typed accessor reads the **base provider's** info
    entry, so the variant inherits the base's built-in defaults (including
    its ``models`` dict) while keeping its own per-variant config overrides.

    The model-level accessors (``max_output_tokens``, ``reasoning_effort``,
    ...) accept an optional ``model`` argument; ``None`` (the default)
    resolves to the provider's ``default_model``.  When the requested model
    has no built-in entry, the default model's entry applies; when neither
    exists (e.g. the ``custom`` provider), an empty config applies.

    Args:
        name: The provider name (a ``_PROVIDER_CONFIGS`` key, or a registered
            variant name).
        data: The provider registry dict to read from. Defaults to the
            module-level :data:`janito.providers._PROVIDER_CONFIGS` (held by
            reference, so mutations to the registry are reflected).
        variant_of: The base provider name when ``name`` is a variant.
            Defaults to ``None`` (``name`` is a built-in provider).
    """

    def __init__(
        self, name: str, data: dict | None = None, variant_of: str | None = None
    ):
        data = _PROVIDER_CONFIGS if data is None else data
        if name not in data and variant_of is None:
            supported = ", ".join(sorted(data.keys()))
            raise ValueError(
                f"Unknown provider '{name}'. Supported providers: {supported}"
            )
        if variant_of is not None and variant_of not in data:
            supported = ", ".join(sorted(data.keys()))
            raise ValueError(
                f"Unknown base provider '{variant_of}' for variant '{name}'. "
                f"Supported providers: {supported}"
            )
        self._data = data
        self._name = name
        self._base_name = variant_of
        self._info = data[variant_of] if variant_of is not None else data[name]

    @property
    def name(self) -> str:
        """The provider name (e.g. ``"openai"`` or ``"alibaba-tokenplan"``)."""
        return self._name

    @property
    def is_variant(self) -> bool:
        """Whether this provider is a registered variant (``<provider>-<word>``)."""
        return self._base_name is not None

    @property
    def base_name(self) -> str | None:
        """The base provider this variant inherits from, or ``None``."""
        return self._base_name

    @property
    def info(self) -> dict:
        """The raw info entry backing this provider (the base's for variants)."""
        return self._info

    @property
    def is_custom(self) -> bool:
        """Whether this is the special ``"custom"`` provider (or a variant of it)."""
        base = self._base_name or self._name
        return base == "custom"

    def has_usable_builtin_models(self) -> bool:
        """Whether the provider ships a non-empty, *usable* model list.

        ``False`` for providers with no ``models`` entry (``custom``) and for
        those whose only entry is the ``"custom"`` placeholder (``openrouter``)
        -- a placeholder carries built-in defaults but is not a model the user
        can select, so it must not gate validation.
        """
        names = self.model_names()
        if not names:
            return False
        # A single "custom" placeholder entry is not a real model list.
        if self.default_model() == "custom" and names == ["custom"]:
            return False
        return True

    def _get(self, key: str, default=None):
        """Read an attribute from the provider's info entry."""
        return self._info.get(key, default)

    # ------------------------------------------------------------------
    # Provider-level accessors
    # ------------------------------------------------------------------

    def default_model(self) -> str | None:
        """The built-in default model, or ``None`` (e.g. ``"custom"``)."""
        return self._get("default_model")

    def gemini_flavor(self) -> bool:
        """Whether the provider's API uses the Gemini (Google) flavor.

        Gemini-flavored providers (e.g. Google's Gemini models accessed
        through the OpenAI-compatibility layer) have provider-specific API
        behaviours: notably, their ``enable_thinking`` extra-body flag is
        **not** accepted (Gemini 3.x reasons by default).
        """
        return bool(self._get("gemini_flavor", False))

    def models(self) -> dict:
        """The raw ``models`` dict (model name -> model entry)."""
        models = self._get("models")
        return models if isinstance(models, dict) else {}

    def model_names(self) -> list:
        """The names of every model with a built-in entry."""
        return list(self.models().keys())

    def model_config(self, model: str | None = None) -> ModelConfig:
        """Return the :class:`ModelConfig` for ``model``.

        Fallback chain: the requested model's entry, then the default
        model's entry, then an empty config (unknown model / provider
        without built-in models).  ``model=None`` starts at the default
        model.
        """
        models = self.models()
        if model is not None and model in models:
            return ModelConfig(models[model])
        default = self.default_model()
        if default is not None and default in models:
            return ModelConfig(models[default])
        return ModelConfig({})

    # ------------------------------------------------------------------
    # Model-level accessors (model=None means the default model)
    # ------------------------------------------------------------------

    def max_input_tokens(self, model: str | None = None) -> int | None:
        """The built-in max input-token (context window) limit, or ``None``."""
        return self.model_config(model).max_input_tokens()

    def max_output_tokens(self, model: str | None = None) -> int | None:
        """The built-in max output-token limit, or ``None``."""
        return self.model_config(model).max_output_tokens()

    def reasoning_effort(self, model: str | None = None) -> str | None:
        """The built-in default reasoning effort, or ``None``."""
        return self.model_config(model).reasoning_effort()

    def supported_reasoning_efforts(self, model: str | None = None) -> list | None:
        """The list of supported reasoning efforts, or ``None``."""
        return self.model_config(model).supported_reasoning_efforts()

    def default_thinking(self, model: str | None = None):
        """The model's built-in thinking default, or ``False`` when none.

        See :meth:`ModelConfig.default_thinking` for the value shape (a
        ``True`` flag or a pass-through dict such as MiniMax-M3's
        ``{'type': 'adaptive'}``).
        """
        return self.model_config(model).default_thinking()

    def preserve_thinking(self, model: str | None = None) -> bool | None:
        """The model's built-in ``preserve_thinking`` default, or ``None``.

        See :meth:`ModelConfig.preserve_thinking`.  ``None`` (no built-in
        declaration) means the caller sends no flag and the API's own default
        applies.
        """
        return self.model_config(model).preserve_thinking()

    def tools(
        self, model: str | None = None, api_type: str | None = None
    ) -> list | None:
        """The model's built-in (native) tool entries, or ``None``.

        See :meth:`ModelConfig.tools` for the value shape (e.g.
        Alibaba/Qwen's ``[{"type": "code_interpreter"}, ...]``) and the
        ``api_type`` per-API-type resolution.
        """
        return self.model_config(model).tools(api_type=api_type)

    def supported_api_types(self, model: str | None = None) -> list | None:
        """The API types the model supports (``"Responses"``/``"Completions"``/...)."""
        return self.model_config(model).supported_api_types()

    def default_api_type(self, model: str | None = None) -> str | None:
        """The built-in default API type, from the model's ``default_api_type`` entry."""
        return self.model_config(model).default_api_type()

    def stateless_mode(self, model: str | None = None) -> bool:
        """Whether the model's Responses API keeps conversation state server-side.

        Absent defaults to ``True`` (the Responses API design).

        This returns the *built-in* default only.  A per-provider/model
        override stored in ``~/.janito/config.json`` under
        ``providers.<name>.models.<model>.stateless-mode`` is applied by the
        caller (see :func:`janito.config_loaders.load_stateless_mode_from_config`
        and the single effective resolution point
        :func:`janito.llm_clients.openai.responses_state.stateless_mode`):
        the provider layer never imports the config layer (issue #110).
        """
        return self.model_config(model).stateless_mode()

    def responses_include(self, model: str | None = None) -> list | None:
        """Extra ``include`` values to request on every Responses call.

        See :meth:`ModelConfig.responses_include`.  ``None`` when the model
        declares none (no ``include`` parameter is sent).
        """
        return self.model_config(model).responses_include()

    def thinking_summary(self, model: str | None = None) -> bool:
        """Whether to request a Responses reasoning summary.

        See :meth:`ModelConfig.thinking_summary`.  ``False`` when the model
        declares none.
        """
        return self.model_config(model).thinking_summary()

    def endpoint_for(self, api_type: str | None = None) -> str | None:
        """Get the base URL for this provider, honoring ``endpoint_by_api_type``.

        Resolution rules (mirrors :func:`get_endpoint_for_api_type`):

        1. A single-entry ``endpoint_by_api_type`` dict is the default for
           *any* API type.
        2. Otherwise, if ``api_type`` is given and present in the dict, that
           entry's URL is returned.
        3. Otherwise the provider's single built-in ``endpoint`` applies
           (``None`` for standard OpenAI, the ``CUSTOM_ENDPOINT`` marker for
           "custom").

        Args:
            api_type: The canonical API type (e.g. ``"Completions"``). May be
                ``None`` for the provider's default endpoint.

        Returns:
            The base URL for the provider/API type, or ``None``.
        """
        by_type = self._get("endpoint_by_api_type")
        if by_type:
            # A single-element dict is the default endpoint for any API type.
            if len(by_type) == 1:
                return next(iter(by_type.values()))
            if api_type and api_type in by_type:
                return by_type[api_type]
        return self._get("endpoint")
