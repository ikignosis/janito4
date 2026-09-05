"""Hosted tool search (issue #128): flag, namespaces, observer, stream."""

from rich.console import Console

from janito.llm_adapters.responses import (
    convert_tools_for_tool_search,
    model_uses_tool_search,
)
from janito.providers.registry import get_provider
from janito.tooling.schema import get_tool_namespace, group_schemas_by_namespace
from janito.ui.observer import RichTurnObserver


def test_tool_search_flag_only_on_meta():
    assert model_uses_tool_search("meta", "muse-spark-1.3") is True
    assert model_uses_tool_search("meta", "muse-spark-1.3-contributor") is True
    assert model_uses_tool_search("openai", "gpt-4o") is False
    assert model_uses_tool_search(None, "muse-spark-1.3") is False
    assert get_provider("openai").tool_search("gpt-4o") is False


def test_tool_namespace_defaults_to_default():
    def fn():
        """Do."""

    assert get_tool_namespace(fn) == "default"
    fn._tool_namespace = "files"  # noqa: SLF001 - test sets discovery attr
    assert get_tool_namespace(fn) == "files"


def test_group_schemas_by_namespace_marks_defer_loading():
    def fn_a(x: int):
        """Do a."""

    def fn_b(x: int):
        """Do b."""

    fn_a._tool_namespace = "files"  # noqa: SLF001
    fn_b._tool_namespace = "net"  # noqa: SLF001
    grouped = group_schemas_by_namespace({"fn_a": fn_a, "fn_b": fn_b})
    assert {g["name"] for g in grouped} == {"files", "net"}
    assert grouped[0]["tools"][0]["defer_loading"] is True


def test_convert_tools_for_tool_search_appends_tool_search():
    flat = [
        {
            "type": "function",
            "function": {"name": "a", "description": "d", "parameters": {}},
            "namespace": "files",
        }
    ]
    converted = convert_tools_for_tool_search(flat)
    assert converted[-1] == {"type": "tool_search"}
    assert converted[0]["type"] == "namespace"


def test_rich_observer_renders_tool_search_events():
    console = Console(file=open("/dev/null", "w"))
    observer = RichTurnObserver(console=console)
    observer.on_tool_search_call(["crm"])
    observer.on_tool_search_output(["list_open_orders"])
    assert observer is not None


def test_responses_stream_collects_tool_search_items():
    from janito.llm_clients.openai.responses_stream import ResponsesStreamConsumer

    consumer = ResponsesStreamConsumer()
    consumer.handle_output_item(
        type(
            "E",
            (),
            {
                "item": type(
                    "I",
                    (),
                    {"type": "tool_search_call", "arguments": {"paths": ["crm"]}},
                )()
            },
        )()
    )
    consumer.handle_output_item(
        type(
            "E",
            (),
            {
                "item": type(
                    "I",
                    (),
                    {
                        "type": "tool_search_output",
                        "tools": [{"type": "function", "name": "list_open_orders"}],
                    },
                )()
            },
        )()
    )
    assert consumer.tool_search_calls == [{"paths": ["crm"]}]
    assert consumer.tool_search_outputs == [{"tool_names": ["list_open_orders"]}]


def test_bare_tool_name_strips_namespace_prefix():
    from janito.llm_clients.openai.responses_stream import _bare_tool_name

    assert _bare_tool_name("files.ListFiles") == "ListFiles"
    assert _bare_tool_name("ListFiles", "files") == "ListFiles"


def test_tool_search_output_unwraps_namespace_objects():
    from janito.llm_clients.openai.responses_stream import ResponsesStreamConsumer

    ns = type(
        "NS",
        (),
        {
            "type": "namespace",
            "name": "files",
            "tools": [
                type("F", (), {"name": "ListFiles"})(),
            ],
        },
    )()
    consumer = ResponsesStreamConsumer()
    consumer.handle_output_item(
        type(
            "E",
            (),
            {"item": type("I", (), {"type": "tool_search_output", "tools": [ns]})()},
        )()
    )
    assert consumer.tool_search_outputs == [{"tool_names": ["ListFiles"]}]


def test_function_call_with_dotted_name_normalizes():
    from janito.llm_clients.openai.responses_stream import ResponsesStreamConsumer

    consumer = ResponsesStreamConsumer()
    consumer.handle_output_item(
        type(
            "E",
            (),
            {
                "item": type(
                    "I",
                    (),
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "id": "fc1",
                        "name": "files.ListFiles",
                        "namespace": "files",
                        "arguments": "{}",
                    },
                )()
            },
        )()
    )
    assert consumer.tool_calls[0]["name"] == "ListFiles"
