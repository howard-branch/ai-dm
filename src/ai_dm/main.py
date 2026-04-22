from ai_dm.app.bootstrap import build_runtime


def main() -> None:
    runtime = build_runtime()
    runtime.start()


if __name__ == "__main__":
    main()
