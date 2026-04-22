from ai_dm.foundry.client import FoundryClient


def main() -> None:
    client = FoundryClient("ws://127.0.0.1:8765")
    client.send({
        "type": "highlight_object",
        "target_id": "sigil_door",
    })


if __name__ == "__main__":
    main()