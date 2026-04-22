from __future__ import annotations

from pathlib import Path

from ai_dm.ai.client import AIClient
from ai_dm.ai.prompts import build_narrator_prompt
from ai_dm.ai.response_parser import safe_parse_ai_output
from ai_dm.ai.schemas import AIOutput


class Narrator:
    def __init__(
        self,
        *,
        client: AIClient | None = None,
        system_prompt_path: Path | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.client = client or AIClient()
        self.system_prompt_path = system_prompt_path
        self.schema_path = schema_path

    def narrate(self, player_input: str, context: dict) -> AIOutput:
        prompt = build_narrator_prompt(
            player_input,
            context,
            system_path=self.system_prompt_path,
            schema_path=self.schema_path,
        )
        raw = self.client.generate(prompt, response_schema=None)
        output, _issues = safe_parse_ai_output(raw)
        return output
