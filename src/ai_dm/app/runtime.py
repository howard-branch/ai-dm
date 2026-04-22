class Runtime:
    def __init__(self, director, container=None) -> None:
        self.director = director
        self.container = container

    def start(self) -> None:
        print("AI DM runtime started.")
        result = self.director.handle_player_input("Look around")
        print(result.narration)

    def shutdown(self) -> None:
        if self.container is not None:
            self.container.shutdown()
