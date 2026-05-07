from .models import SongCandidate


class PlayerController:
    def play(self, song: SongCandidate) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def next(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
