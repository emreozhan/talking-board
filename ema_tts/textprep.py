"""Panodan gelen metni seslendirmeye hazırlar: markdown temizliği ve parçalama."""

import re

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_QUOTE = re.compile(r"^\s*>+\s?", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_RULE = re.compile(r"^\s*([-*_=])\1{2,}\s*$", re.M)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M)
_EMPHASIS = re.compile(r"\*\*|__|~~|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])")
_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+")
_TERMINAL = ".!?…;:,"


def clean(text: str) -> str:
    """Markdown işaretlerini atar; her satırı noktalamayla bitirip tek metne çevirir."""
    text = text.replace("\r\n", "\n")
    text = _FENCE.sub("\nKod bloğu.\n", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("bağlantı", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TABLE_SEP.sub("", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)

    lines = []
    for line in text.split("\n"):
        line = line.strip().strip("|").strip()
        line = re.sub(r"\s*\|\s*", ", ", line)
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue
        if line[-1] not in _TERMINAL:
            line += "."
        lines.append(line)
    return " ".join(lines)


def split(text: str, max_chars: int = 300) -> list[str]:
    """Metni cümle sınırlarından, her biri en fazla max_chars olacak parçalara böler."""
    pieces = []
    for sentence in _SENTENCE_END.split(text.strip()):
        while len(sentence) > max_chars:
            cut = sentence.rfind(", ", 0, max_chars)
            if cut <= 0:
                cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            pieces.append(sentence)

    segments, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > max_chars:
            segments.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        segments.append(current)
    return segments


def prepare(text: str, max_chars: int = 300) -> list[str]:
    return split(clean(text), max_chars)
