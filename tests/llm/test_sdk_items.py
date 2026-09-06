from janito.llm_adapters.sdk import _extract_raw_attrs, _object_items


class DictLike:
    __slots__ = ("_data",)

    def __init__(self, data):
        self._data = dict(data)

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]


class BrokenKeys:
    __slots__ = ()

    def keys(self):
        raise TypeError("broken keys")

    def __getitem__(self, key):
        raise AssertionError("must not be called")


class FlakyGetItem:
    __slots__ = ()

    def keys(self):
        return ["ok", "missing"]

    def __getitem__(self, key):
        if key == "missing":
            raise KeyError(key)
        return "value"


def test_dict_like_object_items():
    assert dict(_object_items(DictLike({"id": "x"}))) == {"id": "x"}


def test_broken_keys_returns_empty():
    assert list(_object_items(BrokenKeys())) == []


def test_flaky_getitem_skips_bad_key():
    assert dict(_object_items(FlakyGetItem())) == {"ok": "value"}


def test_extract_raw_attrs_dict_like():
    out = _extract_raw_attrs(DictLike({"id": "abc", "model": "m"}))
    assert out == {"id": "abc", "model": "m"}
