"""Stable API response snapshots for boundary replay; SDK clients are never serialized."""


class Object(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def model_dump(self, **kwargs):
        return dict(self)


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    if hasattr(value, "__slots__"):
        return {k: _plain(getattr(value, k)) for k in value.__slots__ if hasattr(value, k)}
    return value


def encode(message):
    return {"__apex_message__": _plain(message)}


def decode(value):
    if not isinstance(value, dict) or "__apex_message__" not in value:
        raise ValueError("invalid recorded response snapshot")
    def restore(x):
        if isinstance(x, dict):
            return Object({k: restore(v) for k, v in x.items()})
        if isinstance(x, list):
            return [restore(v) for v in x]
        return x
    return restore(value["__apex_message__"])
def encode_turn(value):
    return {"content": encode(value[0]), "stop_reason": value[1], "text": value[2]}


def decode_turn(value):
    return decode(value["content"]), value["stop_reason"], value["text"]
