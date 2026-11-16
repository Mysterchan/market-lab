"""Searchable filings and reports that become visible at their publication time.

Documents come from a JSON list written by ``news_import.convert_documents``: each has a
``document_id``, ``available_time_ns``, ``title``, optional ``form`` and full ``text``.
Searches and reads only ever consider documents published by the experiment time.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']
SNIPPET_CHARACTERS = 600
PAGE_CHARACTERS = 8_000
MAX_RESULTS = 20
_WORD = re.compile(r'[a-z0-9]+')


class DocumentStore:
    """Documents by publication time; search and read never see later ones."""

    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        for document in documents:
            if (
                not isinstance(document, dict)
                or not isinstance(document.get('document_id'), str)
                or type(document.get('available_time_ns')) is not int
                or document['available_time_ns'] < 0
                or not isinstance(document.get('text'), str)
            ):
                raise ContractError('each document needs document_id, available_time_ns, text')
        self._documents = sorted(documents, key=lambda d: d['available_time_ns'])
        self._by_id = {d['document_id']: d for d in self._documents}

    def __len__(self) -> int:
        return len(self._documents)

    def _visible(self, cutoff_ns: int) -> List[Dict[str, Any]]:
        return [d for d in self._documents if d['available_time_ns'] <= cutoff_ns]

    def search(self, cutoff_ns: int, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Visible documents ranked by how often the query's words occur, best first.

        Each result has the document's ID, title, form, publication time and a snippet
        around its first match. An empty query lists the newest documents.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise ContractError(f'limit must be 1..{MAX_RESULTS}')
        terms = set(_WORD.findall(query.lower()))
        scored = []
        for document in self._visible(cutoff_ns):
            lowered = document['text'].lower()
            score = sum(lowered.count(term) for term in terms) if terms else 0
            if terms and not score:
                continue
            first = min((lowered.find(t) for t in terms if t in lowered), default=0)
            start = max(0, first - SNIPPET_CHARACTERS // 3)
            scored.append(
                (
                    score,
                    document['available_time_ns'],
                    dict(
                        document_id=document['document_id'],
                        title=document.get('title', ''),
                        form=document.get('form', ''),
                        available_time_ns=document['available_time_ns'],
                        matches=score,
                        snippet=document['text'][start : start + SNIPPET_CHARACTERS],
                    ),
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [result for _, _, result in scored[:limit]]

    def read(self, cutoff_ns: int, document_id: str, offset: int = 0) -> Dict[str, Any]:
        """One page of a visible document's text, starting at character ``offset``.

        Raises:
            ContractError: If the document is unknown or not yet published.
        """
        document = self._by_id.get(document_id)
        if document is None or document['available_time_ns'] > cutoff_ns:
            raise ContractError('no such document is visible yet')
        if type(offset) is not int or offset < 0:
            raise ContractError('offset must be a nonnegative integer')
        text = document['text']
        end = offset + PAGE_CHARACTERS
        return dict(
            document_id=document_id,
            title=document.get('title', ''),
            offset=offset,
            text=text[offset:end],
            next_offset=end if end < len(text) else None,
            length=len(text),
        )


def load_documents(path: PathInput) -> DocumentStore:
    """Read a documents JSON file written by convert_documents()."""
    try:
        values = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ContractError('documents file must be readable JSON') from exc
    if not isinstance(values, list):
        raise ContractError('documents file must be a JSON list')
    return DocumentStore(values)
