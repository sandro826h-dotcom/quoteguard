"""Source check for extracted terms.

A quote fragment is verified when, after trimming and collapsing runs of whitespace in both texts,
it is a substring of the quote text. A term whose fragments are all unverifiable is downgraded to
missing: the extraction cannot point at the place where the term is written.
"""
from __future__ import annotations

import copy
import re

from .schema import _TERM_CODECS, Field, QuoteTerms


def _squash(s: str) -> str:
    return " ".join(str(s).split())


_CJK = r"\u3000-\u303f\u3400-\u9fff\uff00-\uffef"
_CJK_BEFORE = re.compile(rf" (?=[{_CJK}])")
_CJK_AFTER = re.compile(rf"(?<=[{_CJK}]) ")


def _bare(s: str) -> str:
    """Whitespace runs folded to one space, then dropped where they touch a CJK character; spaces between
    digits or letters stay ("12 30" does not become "1230")."""
    return _CJK_AFTER.sub("", _CJK_BEFORE.sub("", _squash(s)))


def _found(q: str, haystack: str, bare_haystack: str) -> bool:
    """Whitespace-normalised substring, or substring once spaces next to Chinese characters are removed (models add or drop
    spaces between Chinese and Latin text)."""
    return bool(q) and (q in haystack or _bare(q) in bare_haystack)


def verify(quote: str, source_text: str) -> bool:
    """True when the fragment occurs in the source text after whitespace normalisation; a fragment that
    differs from the text only in where spaces next to Chinese characters sit also counts."""
    q = _squash(quote or "")
    src = source_text or ""
    return _found(q, _squash(src), _bare(src))


def verify_terms(extracted: QuoteTerms, source_text: str, require_quotes: bool = False):
    """Returns (terms, downgraded_fields).

    A stated, implied or conflicting term with at least one fragment and no verifiable fragment becomes
    missing. A term without any fragment is kept unless require_quotes is set. The input is not modified.
    """
    terms = copy.deepcopy(extracted)
    down = []
    haystack, bare = _squash(source_text or ""), _bare(source_text or "")
    for name in _TERM_CODECS:
        f = getattr(terms, name)
        if f.status == "missing":
            continue
        quotes = [_squash(s.quote) for s in f.spans if _squash(s.quote)]
        if not quotes:
            ok = not require_quotes
        else:
            ok = any(_found(q, haystack, bare) for q in quotes)
        if not ok:
            setattr(terms, name, Field(None, "missing", []))
            down.append(name)
    return terms, down
