"""Journal-entry helpers (create / append) over the relay protocol."""
from __future__ import annotations

import logging

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.results import unwrap_single_result

logger = logging.getLogger("ai_dm.foundry.journal")


class JournalService:
    def __init__(self, client: FoundryClient | None = None) -> None:
        self.client = client or FoundryClient()

    def create_entry(
        self,
        title: str,
        content: str = "",
        *,
        folder: str | None = None,
    ) -> str:
        payload = {
            "type": "create_journal",
            "title": title,
            "content": content,
        }
        if folder is not None:
            payload["folder"] = folder
        result = unwrap_single_result(self.client.request(payload))
        return result.get("journalId") or result.get("id") or ""

    def update_entry(
        self,
        journal_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> None:
        payload: dict = {"type": "update_journal", "journal_id": journal_id}
        if title is not None:
            payload["title"] = title
        if content is not None:
            payload["content"] = content
        unwrap_single_result(self.client.request(payload))

    def append_recap(self, journal_id: str, markdown: str) -> None:
        """Append a markdown block; expects the JS handler to do the merge."""
        self.update_entry(journal_id, content=markdown)

