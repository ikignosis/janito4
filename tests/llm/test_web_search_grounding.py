"""Search grounding for Meta models (issue #131): behavior assertions."""

from janito.llm_adapters.responses import (
    ResponsesTurnAccumulator,
    _citations_from_output,
)
from janito.llm_clients.openai.responses_stream import ResponsesStreamConsumer
from janito.llm_clients.openai.responses_stream import (
    _citations_from_output as _cli_citations,
)
from janito.providers.registry import get_provider


class _E:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Resp:
    def __init__(self, id, output=None, usage=None):
        self.id = id
        self.output = output or []
        self.usage = usage


def test_meta_models_declare_responses_only_web_search():
    for model in ("muse-spark-1.3", "muse-spark-1.3-contributor"):
        tools = get_provider("meta").tools(model, api_type="Responses")
        assert tools == [{"type": "web_search"}]
        assert get_provider("meta").tools(model, api_type="Completions") is None


def test_cli_consumer_collects_search_call_and_citations():
    out = [
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "hello",
                    "annotations": [{"type": "url_citation", "url": "https://x.test", "title": "X"}],
                }
            ],
        }
    ]
    c = ResponsesStreamConsumer()
    for ev in [
        _E("response.created", response=_Resp("r1")),
        _E("response.output_item.done", item=_E("web_search_call", id="ws_1", status="completed")),
        _E("response.completed", response=_Resp("r1", output=out)),
    ]:
        c.handle_event(ev)
    assert c.web_search_calls == [{"id": "ws_1", "status": "completed"}]
    assert c.web_search_citations == [{"url": "https://x.test", "title": "X", "start_index": None, "end_index": None}]


def test_web_accumulator_collects_search_call_and_citations():
    acc = ResponsesTurnAccumulator()
    acc.handle(_E("response.output_item.done", item=_E("web_search_call", status="completed")))
    assert acc.web_search_calls == [{"id": None, "status": "completed"}]
    acc.handle_completion_event(
        _E(
            "response.completed",
            response=_Resp(
                "r1",
                output=[
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "hi",
                                "annotations": [
                                    {"type": "other", "url": "https://skip.test"},
                                    {"type": "url_citation", "url": "https://y.test", "title": "Y"},
                                ],
                            }
                        ],
                    }
                ],
            ),
        )
    )
    assert [c["url"] for c in acc.web_search_citations] == ["https://y.test"]
    assert _cli_citations([]) == [] and _citations_from_output(None) == []


def test_observer_defaults_are_noops():
    from janito.llm_adapters.observer import NullObserver

    NullObserver().on_web_search_call()
    NullObserver().on_web_search_done([])
