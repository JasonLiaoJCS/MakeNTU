from __future__ import annotations

import re
from typing import Any

from .emotion_map import decision_from_profile
from .models import EmotionDecision


KEYWORDS: dict[str, list[str]] = {
    "excited": ["興奮", "期待", "迫不及待", "超開心", "超爽", "太嗨", "excited", "can't wait"],
    "happy": ["開心", "高興", "快樂", "太好了", "好棒", "讚", "順利", "happy", "great", "nice"],
    "sad": ["難過", "傷心", "想哭", "失落", "沮喪", "心碎", "sad", "depressed", "down"],
    "tired": ["很累", "累", "疲倦", "疲憊", "沒力", "耗盡", "撐不下去", "burnout", "tired", "exhausted"],
    "angry": ["生氣", "火大", "憤怒", "不爽", "煩死", "氣死", "angry", "mad", "furious"],
    "surprised": ["驚訝", "嚇到", "意外", "竟然", "真的假的", "surprised", "wow"],
    "curious": ["好奇", "為什麼", "怎麼會", "想知道", "可不可以解釋", "curious", "why", "how"],
    "confused": ["困惑", "不懂", "看不懂", "搞不清楚", "混亂", "confused", "lost"],
    "thinking": ["想想", "思考", "考慮", "猶豫", "權衡", "thinking", "consider"],
    "concerned": ["擔心", "焦慮", "緊張", "害怕", "不安", "壓力", "怕", "worry", "anxious", "concerned"],
    "sleepy": ["想睡", "睏", "好睏", "睡不著", "熬夜", "sleepy", "drowsy"],
}

PRIORITY = [
    "tired",
    "concerned",
    "sad",
    "angry",
    "excited",
    "happy",
    "surprised",
    "confused",
    "curious",
    "thinking",
    "sleepy",
    "neutral",
]


def classify_rule_based(text: str, map_data: dict[str, Any], config: dict[str, Any]) -> EmotionDecision:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    scores = {emotion: 0 for emotion in KEYWORDS}
    for emotion, words in KEYWORDS.items():
        for word in words:
            if word.lower() in normalized:
                scores[emotion] += 1

    if not any(scores.values()):
        return decision_from_profile("neutral", map_data, config, confidence=0.35)

    best_score = max(scores.values())
    candidates = {emotion for emotion, score in scores.items() if score == best_score}
    for emotion in PRIORITY:
        if emotion in candidates:
            confidence = min(0.85, 0.45 + 0.12 * best_score)
            return decision_from_profile(emotion, map_data, config, confidence=confidence)

    return decision_from_profile("neutral", map_data, config, confidence=0.35)

