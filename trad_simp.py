"""Traditional → Simplified Chinese conversion via OpenCC.

The OpenCC import is lazy so the optional dependency is only required when
the feature is actually enabled. The converter instance is cached for the
process lifetime (construction is expensive, conversion is cheap).
"""

_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        try:
            from opencc import OpenCC
        except ImportError as e:
            raise ImportError(
                "OpenCC is required for traditional→simplified conversion. "
                "Install with: pip install opencc-python-reimplemented"
            ) from e
        # tw2sp handles Taiwan-specific vocabulary (軟體→软件, 螢幕→屏幕,
        # 解析度→分辨率, etc.) on top of character conversion. Plain t2s only
        # does char-level substitution, leaving TW phrases intact and breaking
        # entity matching against mainland-standard prompts/dicts.
        _converter = OpenCC('tw2sp')
    return _converter


def convert_text(value):
    """Convert traditional Chinese characters to simplified.

    Accepts a string or a list of strings; returns the same shape.
    Idempotent — running on already-simplified text is a no-op.
    """
    if value is None:
        return value
    conv = _get_converter()
    if isinstance(value, list):
        return [conv.convert(line) if isinstance(line, str) else line for line in value]
    if isinstance(value, str):
        return conv.convert(value)
    return value
