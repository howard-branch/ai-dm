"""Tiny outbound payload helpers (legacy + Phase 3 additions)."""
from __future__ import annotations


def move_token(token_id: str, x: int, y: int) -> dict:
    return {"type": "move_token", "token_id": token_id, "x": x, "y": y}


def create_journal(title: str, content: str = "", *, folder: str | None = None) -> dict:
    payload: dict = {"type": "create_journal", "title": title, "content": content}
    if folder is not None:
        payload["folder"] = folder
    return payload


def update_journal(
    journal_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
) -> dict:
    payload: dict = {"type": "update_journal", "journal_id": journal_id}
    if title is not None:
        payload["title"] = title
    if content is not None:
        payload["content"] = content
    return payload


def read_actor(actor_id: str) -> dict:
    return {"type": "read_actor", "actor_id": actor_id}


def read_active_scene() -> dict:
    return {"type": "read_active_scene"}

