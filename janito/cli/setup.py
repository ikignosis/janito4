"""
CLI setup helpers.

Runtime configuration (API key, endpoint, model) is resolved on demand from the
auth store (~/.janito/auth.json) and the config file (~/.janito/config.json) --
see :func:`janito.runtime_config.resolve_runtime_config`. No ``OPENAI_*``
environment variables are read or written.

These helpers only perform an early, friendly validation before a session
starts so that misconfiguration is reported with an actionable message instead
of failing deep inside the API call.
"""

import sys

from ..runtime_config import resolve_runtime_config


def validate_runtime_config(args=None) -> None:
    """Validate that the runtime configuration can be resolved.

    Resolves the API key (from the auth store), the endpoint (configured
    endpoint or the provider's built-in default) and the model (``--model`` or
    the provider's configured model). If any of these is missing, prints an
    actionable error to stderr and exits.

    Args:
        args: Parsed command line arguments (optional). ``args.model`` and
            ``args.provider`` are honored when present.

    Raises:
        SystemExit: If required configuration is missing.
    """
    cli_model = getattr(args, "model", None) if args is not None else None
    cli_provider = getattr(args, "provider", None) if args is not None else None
    try:
        resolve_runtime_config(cli_model, cli_provider)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def validate_system_prompt_file(args=None) -> None:
    """Validate that the configured ``system-prompt-file`` exists.

    When the ``system-prompt-file`` config key is set (e.g. via
    ``janito --set system-prompt-file=~/base-prompt.md``), checks that the
    file actually exists before a session starts.  A missing file is reported
    with an actionable error instead of surfacing only when the system prompt
    is rendered deep inside the session setup (where it would otherwise show
    up as a bare ``ValueError`` traceback).

    ``~`` is expanded and relative paths resolve against the current working
    directory, matching
    :func:`janito.config_loaders.load_system_prompt_start`.

    Args:
        args: Parsed command line arguments (optional, currently unused).

    Raises:
        SystemExit: If ``system-prompt-file`` is set but the file does not
            exist.
    """
    from ..config_loaders import validate_system_prompt_file_path
    from ..config_store import get_config_value

    file_value = get_config_value("system-prompt-file")
    if not file_value:
        return
    try:
        validate_system_prompt_file_path(file_value)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
