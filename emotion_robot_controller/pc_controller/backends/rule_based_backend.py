from __future__ import annotations

from .base_backend import EmotionBackend
from ..fallback_rules import classify_rule_based
from ..models import EmotionDecision


class RuleBasedBackend(EmotionBackend):
    def analyze(self, text: str) -> EmotionDecision:
        return classify_rule_based(text, self.map_data, self.config)

