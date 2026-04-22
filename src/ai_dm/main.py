from ai_dm.app.bootstrap import build_runtime
from ai_dm.utils.dotenv import load_dotenv


def main() -> None:
    # Load .env (if present) before anything reads OPENAI_API_KEY etc.
    load_dotenv()
    runtime = build_runtime()
    runtime.start()


if __name__ == "__main__":
    main()
