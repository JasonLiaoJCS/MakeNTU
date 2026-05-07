from typing import List

from .models import SongCandidate


class SearchProvider:
    def search(self, query: str, limit: int = 3) -> List[SongCandidate]:
        """Return song candidates for the input query."""
        raise NotImplementedError
