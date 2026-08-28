from __future__ import annotations

import re


QUERY_TRANSLATIONS = (
    ("大语言模型", "large language model"),
    ("视觉语言模型", "vision language model"),
    ("知识图谱", "knowledge graph"),
    ("图神经网络", "graph neural network"),
    ("目标检测", "object detection"),
    ("缺陷检测", "defect detection"),
    ("异常检测", "anomaly detection"),
    ("语义分割", "semantic segmentation"),
    ("实例分割", "instance segmentation"),
    ("推荐系统", "recommender system"),
    ("信息检索", "information retrieval"),
    ("强化学习", "reinforcement learning"),
    ("联邦学习", "federated learning"),
    ("自监督学习", "self-supervised learning"),
    ("深度学习", "deep learning"),
    ("机器学习", "machine learning"),
    ("计算机视觉", "computer vision"),
    ("自然语言处理", "natural language processing"),
    ("扩散模型", "diffusion model"),
    ("生成式人工智能", "generative AI"),
    ("自动驾驶", "autonomous driving"),
    ("多模态", "multimodal"),
    ("机器人", "robotics"),
)


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def expand_search_query(query: str) -> str:
    """Translate common Chinese research terms while preserving Latin model names."""
    normalized = clean_text(query)
    if not normalized:
        return ""

    remaining = normalized
    translations: list[str] = []
    for chinese, english in QUERY_TRANSLATIONS:
        if chinese not in remaining:
            continue
        remaining = remaining.replace(chinese, " ")
        if english not in translations:
            translations.append(english)

    if not translations:
        return normalized

    latin_remainder = clean_text(re.sub(r"[\u3400-\u9fff]", " ", remaining))
    parts = [latin_remainder, *translations]
    return clean_text(" ".join(part for part in parts if part))


def dblp_search_terms(query: str) -> str:
    """Add a close anomaly-detection synonym for defect-search recall."""
    expanded = expand_search_query(query)
    return re.sub(
        r"\bdefect\s+detection\b",
        "defect|anomaly detection",
        expanded,
        flags=re.IGNORECASE,
    )


def openalex_query_variants(query: str) -> list[str]:
    expanded = expand_search_query(query)
    values = [expanded]
    if re.search(r"\bdefect\s+detection\b", expanded, flags=re.IGNORECASE):
        values.extend(
            [
                re.sub(
                    r"\bdefect\s+detection\b",
                    "anomaly detection",
                    expanded,
                    flags=re.IGNORECASE,
                ),
                re.sub(
                    r"\bdefect\s+detection\b",
                    "industrial anomaly detection",
                    expanded,
                    flags=re.IGNORECASE,
                ),
                re.sub(
                    r"\bdefect\s+detection\b",
                    "visual anomaly detection",
                    expanded,
                    flags=re.IGNORECASE,
                ),
            ]
        )
    return list(dict.fromkeys(clean_text(value) for value in values if clean_text(value)))
