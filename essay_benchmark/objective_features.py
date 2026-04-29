from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
MARKERS_DIR = ROOT_DIR / "sherbold-chatgpt-student-essay-study-3f09052" / "markers"

OBJECTIVE_METRICS = [
    {
        "key": "lexical_diversity_mtld",
        "label_zh": "词汇多样性",
        "label_en": "Lexical diversity (MTLD)",
        "category_zh": "词汇多样性",
        "description_zh": "使用 MTLD 衡量文本词汇变化程度，数值越高通常表示词汇越丰富。",
    },
    {
        "key": "syntactic_complexity_depth",
        "label_zh": "句法复杂度（依存深度）",
        "label_en": "Syntactic complexity (dependency depth)",
        "category_zh": "句法复杂度",
        "description_zh": "参考原研究的依存句法树深度，反映句子结构层级。",
    },
    {
        "key": "syntactic_complexity_clauses",
        "label_zh": "句法复杂度（从句/并列结构）",
        "label_en": "Syntactic complexity (clauses)",
        "category_zh": "句法复杂度",
        "description_zh": "统计从句、并列、状语从句等复杂结构标记。",
    },
    {
        "key": "nominalizations",
        "label_zh": "名词化",
        "label_en": "Nominalizations",
        "category_zh": "句法复杂度",
        "description_zh": "统计常见名词化后缀，如 -tion、-ment、-ness、-ity 等。",
    },
    {
        "key": "modals",
        "label_zh": "情态动词",
        "label_en": "Modals",
        "category_zh": "语义属性",
        "description_zh": "统计 can、could、may、must、should 等情态表达。",
    },
    {
        "key": "epistemic_markers",
        "label_zh": "认知标记语",
        "label_en": "Epistemic markers",
        "category_zh": "语义属性",
        "description_zh": "统计 I think、it seems、in my opinion 等表达立场或确信程度的标记。",
    },
    {
        "key": "discourse_markers",
        "label_zh": "语篇标记语",
        "label_en": "Discourse markers",
        "category_zh": "语篇属性",
        "description_zh": "基于 PDTB 连接词列表统计 however、therefore、although 等语篇衔接标记。",
    },
]

_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_NOMINAL_RE = re.compile(r"\b[A-Za-z]+(?:tion|ment|ance|ence|ion|ity|ities|ness|ship)s?\b", re.I)
_CLAUSE_RE = re.compile(
    r"\b(?:although|because|since|while|whereas|if|unless|when|whenever|who|whom|whose|which|that|"
    r"where|whether|after|before|until|and|but|or|nor|yet|so)\b",
    re.I,
)
_MODAL_RE = re.compile(
    r"\b(?:can|could|may|might|must|shall|should|will|would|ought to|have to|has to|had to|need to)\b",
    re.I,
)
_EPISTEMIC_PATTERNS = [
    r"\b(?:I|we|one)\s+(?:strongly\s+)?(?:believe|think|guess|assume|know|worry)\b",
    r"\bit\s+is\s+(?:believed|known|assumed|thought|obvious|clear|unclear)\b",
    r"\bit\s+(?:seems|feels|looks)\b",
    r"\bin\s+(?:my|our)\s+(?:opinion|view|experience)\b",
    r"\bas\s+far\s+as\s+(?:I|we)\s+am|are\s+concerned\b",
    r"\bone\s+(?:can|could|may|might)\s+say\b",
]


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]


def _mtld_one_direction(tokens: list[str], threshold: float = 0.72) -> float:
    if not tokens:
        return 0.0

    factors = 0.0
    start = 0
    types: set[str] = set()
    token_count = 0

    for index, token in enumerate(tokens):
        types.add(token)
        token_count += 1
        ttr = len(types) / token_count
        if ttr <= threshold:
            factors += 1.0
            start = index + 1
            types = set()
            token_count = 0

    remainder = len(tokens) - start
    if remainder:
        ttr = len(types) / remainder if remainder else 1.0
        if ttr != 1.0:
            factors += (1.0 - ttr) / (1.0 - threshold)

    return len(tokens) / factors if factors else float(len(tokens))


def mtld(text: str) -> float:
    tokens = _tokens(text)
    if len(tokens) < 10:
        return float(len(set(tokens)))
    forward = _mtld_one_direction(tokens)
    backward = _mtld_one_direction(list(reversed(tokens)))
    return (forward + backward) / 2


@lru_cache(maxsize=1)
def _load_discourse_markers() -> list[str]:
    marker_path = MARKERS_DIR / "connectives_discourse_markers_PDTB.txt"
    if not marker_path.exists():
        return [
            "however",
            "therefore",
            "moreover",
            "furthermore",
            "although",
            "because",
            "for example",
            "in conclusion",
            "on the other hand",
        ]

    markers: list[str] = []
    for line in marker_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split("'")
        if len(parts) > 1:
            marker = parts[1].strip().lower().replace("_", " ")
            if marker:
                markers.append(marker)
    return sorted(set(markers), key=len, reverse=True)


def count_discourse_markers(text: str) -> int:
    normalized = f" {' '.join(_tokens(text))} "
    total = 0
    for marker in _load_discourse_markers():
        total += normalized.count(f" {marker} ")
    return total


def count_epistemic_markers(text: str) -> int:
    return sum(len(re.findall(pattern, text, flags=re.I)) for pattern in _EPISTEMIC_PATTERNS)


def _spacy_depth_and_clause_count(text: str) -> tuple[float | None, int | None]:
    try:
        import spacy
    except ImportError:
        return None, None

    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        return None, None

    doc = nlp(text)
    depths: list[int] = []
    clause_count = 0
    clause_deps = {"acl", "conj", "advcl", "ccomp", "csubj", "discourse", "parataxis"}

    def walk(node: Any, depth: int) -> int:
        children = list(node.children)
        if children:
            return max(walk(child, depth + 1) for child in children)
        return depth

    for sent in doc.sents:
        depths.append(walk(sent.root, 0))
        clause_count += sum(1 for token in sent if token.dep_ in clause_deps)

    if not depths:
        return None, clause_count
    return sum(depths) / len(depths), clause_count


def compute_objective_features(text: str) -> dict[str, Any]:
    clean_text = text.strip()
    tokens = _tokens(clean_text)
    sentences = _sentences(clean_text)
    sentence_count = max(1, len(sentences))
    word_count = len(tokens)

    spacy_depth, spacy_clause_count = _spacy_depth_and_clause_count(clean_text)
    clause_count = spacy_clause_count if spacy_clause_count is not None else len(_CLAUSE_RE.findall(clean_text))
    avg_sentence_length = word_count / sentence_count if sentence_count else 0.0
    fallback_depth = max(1.0, min(12.0, math.log2(max(avg_sentence_length, 1.0)) + clause_count / sentence_count))

    raw_values = {
        "lexical_diversity_mtld": mtld(clean_text),
        "syntactic_complexity_depth": spacy_depth if spacy_depth is not None else fallback_depth,
        "syntactic_complexity_clauses": clause_count,
        "nominalizations": len(_NOMINAL_RE.findall(clean_text)),
        "modals": len(_MODAL_RE.findall(clean_text)),
        "epistemic_markers": count_epistemic_markers(clean_text),
        "discourse_markers": count_discourse_markers(clean_text),
    }

    metrics = []
    for definition in OBJECTIVE_METRICS:
        key = definition["key"]
        value = raw_values[key]
        metrics.append(
            {
                **definition,
                "value": round(float(value), 3),
                "per_sentence": round(float(value) / sentence_count, 3),
            }
        )

    return {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "average_sentence_length": round(avg_sentence_length, 2),
        "parser": "spaCy en_core_web_sm" if spacy_depth is not None else "rule-based fallback",
        "metrics": metrics,
    }
