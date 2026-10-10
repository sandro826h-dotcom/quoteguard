"""Source check for extracted terms.

A quote fragment is verified when, after trimming and collapsing runs of whitespace in both texts,
it is a substring of the quote text. A term whose fragments are all unverifiable is downgraded to
missing: the extraction cannot point at the place where the term is written.
"""
from __future__ import annotations

import copy

from .schema import _TERM_CODECS, Field, QuoteTerms


def _squash(s: str) -> str:
    return " ".join(str(s).split())


def verify(quote: str, source_text: str) -> bool:
    """True when the fragment (whitespace-normalised) occurs in the source text (whitespace-normalised)."""
    q = _squash(quote or "")
    return bool(q) and q in _squash(source_text or "")


def verify_terms(extracted: QuoteTerms, source_text: str, require_quotes: bool = False):
    """Returns (terms, downgraded_fields).

    A stated, implied or conflicting term with at least one fragment and no verifiable fragment becomes
    missing. A term without any fragment is kept unless require_quotes is set. The input is not modified.
    """
    terms = copy.deepcopy(extracted)
    down = []
    haystack = _squash(source_text or "")
    for name in _TERM_CODECS:
        f = getattr(terms, name)
        if f.status == "missing":
            continue
        quotes = [_squash(s.quote) for s in f.spans if _squash(s.quote)]
        if not quotes:
            ok = not require_quotes
        else:
            ok = any(q in haystack for q in quotes)
        if not ok:
            setattr(terms, name, Field(None, "missing", []))
            down.append(name)
    return terms, down
