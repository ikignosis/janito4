from janito.llm_clients.client_support import (
    _headers_retry_after,
    _is_rate_limit,
    _retry_after_seconds,
)


class BrokenAttr(Exception):
    def __getattr__(self, name):
        if name in ("status_code", "status", "code"):
            raise RuntimeError("broken descriptor")
        raise AttributeError(name)


class BrokenHeaders:
    def get(self, *args, **kwargs):
        raise TypeError("broken headers")


def test_is_rate_limit_broken_attr_no_crash():
    assert _is_rate_limit(BrokenAttr("429 rate limit")) is True


def test_is_rate_limit_status_code():
    err = RuntimeError("slow down")
    err.status_code = 429
    assert _is_rate_limit(err) is True


def test_headers_retry_after_broken_headers():
    assert _headers_retry_after(BrokenHeaders()) is None


def test_headers_retry_after_parses():
    assert _headers_retry_after({"retry-after": "2"}) == 2.0


def test_retry_after_seconds_no_crash():
    assert _retry_after_seconds(BrokenAttr("nope")) is None
