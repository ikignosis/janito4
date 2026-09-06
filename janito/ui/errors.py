"""Auth / not-found error explainers (Rich console).

Rendered by the CLI's ``RichTurnObserver.on_error`` when the API clients
report a classified failure (``error_kind`` is ``"not_found"`` or
``"auth"``).  Pure presentation: the classification itself is done by the
clients (`janito.llm_clients.client_support._classify_error` or the typed
``except`` blocks of the OpenAI SDK clients).
"""

import logging

from rich.console import Console

logger = logging.getLogger(__name__)


def _handle_not_found_error(
    e: Exception,
    base_url: str | None,
    model: str,
    console: Console,
    response_id: str | None = None,
) -> None:
    """Explain a not-found failure (unknown model / expired conversation).

    Merges the per-client explainers: the Chat Completions client reports an
    unknown model, and the Responses client additionally reports a stale
    ``previous_response_id`` (the server no longer holds the referenced
    response).  Nothing is printed when the failure is not one of these;
    the caller always re-raises.
    """
    message = str(e).lower()
    if "model not exist" in message or "model not found" in message:
        api_url = base_url if base_url else "https://api.openai.com"
        console.print(
            f"[bold red]Error: Model not found.[/bold red] "
            f"Current model being used: [bold]{model}[/bold] | API URL: [bold]{api_url}[/bold]"
        )
        console.print("[dim]Please check that the model name is correct and available for your API key/provider.[/dim]")
        logger.error(f"Model '{model}' not found at API URL '{api_url}': {e}")
    elif "previous response" in message:
        console.print(
            "[bold red]Error: Conversation state not found.[/bold red] "
            "The server no longer holds the referenced previous response "
            "(it may have expired or the conversation was reset)."
        )
        console.print("[dim]Start a fresh conversation by passing previous_response_id=None.[/dim]")
        logger.error(f"Previous response '{response_id}' not found: {e}")


def _handle_auth_error(
    e: Exception,
    cli_provider: str | None,
    api_key: str,
    base_url: str | None,
    model: str,
    console: Console,
) -> None:
    """Explain an authentication failure (invalid API key) and re-raise.

    Works for the OpenAI SDK clients (called from an ``AuthenticationError``
    handler) and for the native-SDK clients (Anthropic / DashScope / Gemini),
    which raise their own exception types: the failure is recognized by a 401
    status code, a 401 error code (google-genai) or an ``InvalidApiKey``
    error code.  When the exception does not look like an auth failure (e.g.
    a different HTTP error from a native SDK), nothing is printed and the
    caller re-raises as usual.
    """
    from janito.config_keys import get_masked_api_key
    from janito.general_config import get_active_provider

    status_code = getattr(e, "status_code", None)
    code = getattr(e, "code", None)
    if status_code != 401 and code != 401 and not (isinstance(code, str) and "InvalidApiKey" in code):
        return

    provider = cli_provider or get_active_provider()
    masked_key = get_masked_api_key(api_key)
    api_url = base_url if base_url else "https://api.openai.com"
    console.print("[bold red]Error: Authentication failed (invalid API key).[/bold red]")
    console.print(f"  Provider: [bold]{provider}[/bold]")
    console.print(f"  Model:    [bold]{model}[/bold]")
    console.print(f"  API URL:  [bold]{api_url}[/bold]")
    console.print(f"  API Key:  [bold]{masked_key}[/bold]")
    console.print(f"[dim]Please verify your API key for the '{provider}' provider and try again.[/dim]")
    logger.error(
        f"Authentication failed - provider: {provider}, model: {model}, api_url: {api_url}, api_key: {masked_key}: {e}"
    )
