from __future__ import annotations

from typing import Any


def unwrap_single_result(response: dict[str, Any]) -> dict[str, Any]:
    """
    Expected relay shape:
    {
      "type": "result",
      "request_id": "...",
      "result": {...}
    }
    """
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Expected single result dict, got: {response}")

    if not result.get("ok", False):
        raise RuntimeError(f"Foundry command failed: {result}")

    return result


def unwrap_batch_result(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result")
    if not isinstance(result, list):
        raise RuntimeError(f"Expected batch result list, got: {response}")

    for item in result:
        if not item.get("ok", False):
            raise RuntimeError(f"Foundry batch command failed: {item}")

    return result