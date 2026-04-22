from pathlib import Path
import json


class StateStore:
    def __init__(self, base: Path | None = None) -> None:
        self.base = Path(base) if base is not None else Path("data/saves")

    def get_context(self) -> dict:
        session = self._load_json("current_session.json")
        world = self._load_json("world_state.json")
        campaign = self._load_json("campaign_state.json")
        return {
            "session": session,
            "world": world,
            "campaign": campaign,
        }

    def apply_state_updates(self, updates) -> None:
        if not updates:
            return
        campaign = self._load_json("campaign_state.json")
        flags = campaign.setdefault("flags", {})
        for update in updates:
            if getattr(update, "type", None) == "set_flag" and update.key:
                flags[update.key] = update.value
        self._save_json("campaign_state.json", campaign)

    def _load_json(self, filename: str) -> dict:
        path = self.base / filename
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_json(self, filename: str, payload: dict) -> None:
        path = self.base / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
