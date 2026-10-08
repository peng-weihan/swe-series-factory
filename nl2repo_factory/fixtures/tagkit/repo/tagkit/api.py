from .errors import InvalidTagInput
from .normalization import _unique
from .parser import _tokens


def normalize_tags(text: str) -> list[str]:
    """Return normalized unique nonempty comma-separated tags in input order."""
    if not isinstance(text, str):
        raise InvalidTagInput("text must be a string")
    return _unique(_tokens(text))
