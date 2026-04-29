from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from .openai_compatible import ProviderError, call_chat_completion, extract_message_text
from .study import load_rubric
from .text_utils import word_count

RUBRIC = load_rubric()
CRITERIA = RUBRIC["criteria"]
CRITERIA_BY_KEY = {item["key"]: item for item in CRITERIA}


def build_grading_messages(
    *,
    essay_text: str,
    topic: str,
    teacher_config: dict[str, Any],
    objective_features: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    rubric_lines = []
    for item in CRITERIA:
        rubric_lines.append(
            f"- {item['key']}: {item['label_en']} ({item['label_zh']}), score {item['min_score']} to {item['max_score']}. "
            f"Definition: {item['definition_en']}"
        )

    system_prompt = (
        teacher_config.get("system_prompt")
        or (
            "You are an English essay scoring assistant. "
            "Return exactly one compact JSON object and no other text."
        )
    ).strip()

    objective_lines = []
    if objective_features:
        objective_lines.append(
            f"- Word count: {objective_features.get('word_count', '-')}; "
            f"sentences: {objective_features.get('sentence_count', '-')}; "
            f"average sentence length: {objective_features.get('average_sentence_length', '-')}"
        )
        for metric in objective_features.get("metrics", []):
            objective_lines.append(
                f"- {metric.get('label_en')} ({metric.get('label_zh')}): "
                f"value={metric.get('value')}, per_sentence={metric.get('per_sentence')}"
            )

    user_prompt = f"""
Score the essay using this rubric.

Rubric:
{chr(10).join(rubric_lines)}

Objective linguistic features from the Herbold et al. computational analysis:
{chr(10).join(objective_lines) if objective_lines else 'No objective features provided.'}

Scoring rules:
- Treat the topic as: "{topic or 'Topic not provided'}"
- Use only whole or half scores between 0 and 6.
- Be strict but fair.
- Output exactly the seven criteria below.
- Include concise Chinese feedback for the student.
- In the final comments, synthesize the objective linguistic features, your subjective rubric scores, and your overall impression.
- Keep reason and improvement fields short and actionable.
- Do not include markdown, code fences, or text outside JSON.
- Return valid compact JSON only.

Required JSON shape:
{{
  "summary": "用中文概括作文主要表现。",
  "overall_judgment": "用中文给出总体评价。",
  "criteria": [
    {{"key":"topic_and_completeness","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"logic_and_composition","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"expressiveness_and_comprehensibility","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"language_mastery","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"complexity","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"vocabulary_and_text_linking","score":4.5,"reason":"中文理由","improvement":"中文建议"}},
    {{"key":"language_constructs","score":4.5,"reason":"中文理由","improvement":"中文建议"}}
  ],
  "global_suggestions": ["中文建议1", "中文建议2", "中文建议3"]
}}

Essay:
\"\"\"
{essay_text}
\"\"\"
""".strip()

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_batch_grading_messages(
    *,
    topic: str,
    essay_items: list[dict[str, str]],
    teacher_config: dict[str, Any],
) -> list[dict[str, str]]:
    rubric_lines = []
    for item in CRITERIA:
        rubric_lines.append(
            f"- {item['key']}: {item['label_en']} ({item['label_zh']}), score {item['min_score']} to {item['max_score']}. "
            f"Definition: {item['definition_en']}"
        )

    system_prompt = (
        teacher_config.get("system_prompt")
        or (
            "You are an English essay scoring assistant. "
            "Return exactly one compact JSON object and no other text."
        )
    ).strip()

    essay_blocks = []
    for item in essay_items:
        essay_blocks.append(
            f"Essay ID: {item['essay_id']}\nEssay:\n\"\"\"\n{item['text']}\n\"\"\""
        )

    user_prompt = f"""
Score multiple essays on the same topic using this rubric.

Rubric:
{chr(10).join(rubric_lines)}

Scoring rules:
- Treat the topic as: "{topic or 'Topic not provided'}"
- Use only whole or half scores between 0 and 6.
- Be strict but fair.
- Score each essay independently.
- Output only valid compact JSON and no extra text.

Required JSON shape:
{{
  "results": [
    {{
      "essay_id": "e01",
      "criteria": [
        {{"key":"topic_and_completeness","score":4.5}},
        {{"key":"logic_and_composition","score":4.5}},
        {{"key":"expressiveness_and_comprehensibility","score":4.5}},
        {{"key":"language_mastery","score":4.5}},
        {{"key":"complexity","score":4.5}},
        {{"key":"vocabulary_and_text_linking","score":4.5}},
        {{"key":"language_constructs","score":4.5}}
      ]
    }}
  ]
}}

Essays to score:
{chr(10).join(essay_blocks)}
""".strip()

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    sanitized = "".join(char for char in text if char >= " " or char in "\n\r\t").strip()
    if sanitized.startswith("{") and sanitized.endswith("}"):
        try:
            return json.loads(sanitized)
        except json.JSONDecodeError:
            pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", sanitized, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    first = sanitized.find("{")
    last = sanitized.rfind("}")
    if first != -1 and last != -1 and last > first:
        return json.loads(sanitized[first : last + 1])

    raise ProviderError(f"Could not find a JSON object in model output: {sanitized[:500]}")


def validate_grade_payload(payload: dict[str, Any]) -> dict[str, Any]:
    criteria = payload.get("criteria")
    if not isinstance(criteria, list):
        raise ProviderError("Grader output is missing the criteria list.")

    normalized: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for item in criteria:
        key = str(item.get("key", "")).strip()
        if key not in CRITERIA_BY_KEY:
            continue

        score = float(item.get("score"))
        score = max(0.0, min(6.0, round(score * 2) / 2))

        normalized.append(
            {
                "key": key,
                "label_zh": CRITERIA_BY_KEY[key]["label_zh"],
                "label_en": CRITERIA_BY_KEY[key]["label_en"],
                "score": score,
                "reason": str(item.get("reason", "")).strip(),
                "improvement": str(item.get("improvement", "")).strip(),
            }
        )
        seen_keys.add(key)

    missing = [item["key"] for item in CRITERIA if item["key"] not in seen_keys]
    if missing:
        raise ProviderError(f"Grader output missed criteria: {', '.join(missing)}")

    overall = mean(item["score"] for item in normalized)
    return {
        "summary": str(payload.get("summary", "")).strip(),
        "overall_judgment": str(payload.get("overall_judgment", "")).strip(),
        "criteria": normalized,
        "global_suggestions": [
            str(item).strip() for item in payload.get("global_suggestions", []) if str(item).strip()
        ],
        "overall_score": round(overall, 2),
    }


def validate_batch_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ProviderError("Batch grader output is missing the results list.")

    normalized: dict[str, dict[str, Any]] = {}
    for item in results:
        essay_id = str(item.get("essay_id", "")).strip()
        if essay_id not in expected_ids:
            continue
        normalized[essay_id] = validate_grade_payload(item)

    missing_ids = sorted(expected_ids - set(normalized.keys()))
    if missing_ids:
        raise ProviderError(f"Batch grader missed essay ids: {', '.join(missing_ids)}")

    return normalized


def try_recover_payload_from_text(text: str) -> dict[str, Any] | None:
    # Recover key/score pairs from malformed JSON-like output.
    pattern = re.compile(
        r"(?P<key>topic_and_completeness|logic_and_composition|expressiveness_and_comprehensibility|language_mastery|complexity|vocabulary_and_text_linking|language_constructs)"
        r"[^0-9\-]{0,120}(?P<score>-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    recovered: dict[str, float] = {}
    for match in pattern.finditer(text):
        key = match.group("key").lower()
        if key in recovered:
            continue
        try:
            score = float(match.group("score"))
        except ValueError:
            continue
        recovered[key] = max(0.0, min(6.0, round(score * 2) / 2))

    if len(recovered) != len(CRITERIA):
        return None

    return {
        "criteria": [{"key": item["key"], "score": recovered[item["key"]]} for item in CRITERIA],
    }


def _heuristic_band(value: float, thresholds: list[float]) -> float:
    score = 0.0
    for threshold in thresholds:
        if value >= threshold:
            score += 1.0
    return min(6.0, score)


def mock_grade(essay_text: str, topic: str, teacher_config: dict[str, Any]) -> dict[str, Any]:
    words = word_count(essay_text)
    paragraphs = max(1, len([part for part in essay_text.splitlines() if part.strip()]))
    sentences = max(1, len(re.findall(r"[.!?]+", essay_text)))
    avg_sentence = words / sentences
    lexical_ratio = len(set(re.findall(r"[A-Za-z']+", essay_text.lower()))) / max(words, 1)
    topic_hits = 0
    if topic:
        topic_tokens = {token for token in re.findall(r"[A-Za-z']+", topic.lower()) if len(token) > 3}
        essay_tokens = set(re.findall(r"[A-Za-z']+", essay_text.lower()))
        topic_hits = len(topic_tokens & essay_tokens)

    scores = {
        "topic_and_completeness": _heuristic_band(topic_hits, [1, 2, 3, 4, 5, 6]),
        "logic_and_composition": _heuristic_band(paragraphs, [1, 2, 3, 4, 5, 6]),
        "expressiveness_and_comprehensibility": _heuristic_band(words, [120, 170, 220, 270, 330, 420]),
        "language_mastery": _heuristic_band(lexical_ratio, [0.24, 0.28, 0.32, 0.36, 0.40, 0.44]),
        "complexity": _heuristic_band(avg_sentence, [8, 10, 12, 14, 16, 18]),
        "vocabulary_and_text_linking": _heuristic_band(
            len(re.findall(r"\b(however|therefore|moreover|furthermore|although|because)\b", essay_text, re.I)),
            [1, 2, 3, 4, 5, 6],
        ),
        "language_constructs": _heuristic_band(
            len(re.findall(r"\b(if|when|while|which|that|because|although|whether)\b", essay_text, re.I)),
            [1, 2, 3, 4, 5, 6],
        ),
    }

    criteria = []
    for item in CRITERIA:
        score = scores[item["key"]]
        criteria.append(
            {
                "key": item["key"],
                "label_zh": item["label_zh"],
                "label_en": item["label_en"],
                "score": score,
                "reason": "Mock grading based on length, structure, and lexical signals.",
                "improvement": "Use a live model provider for research-grade feedback.",
            }
        )

    overall = round(mean(item["score"] for item in criteria), 2)
    return {
        "summary": "Mock grading result for local smoke testing.",
        "overall_judgment": f"Estimated overall quality: {overall}/6.",
        "criteria": criteria,
        "global_suggestions": [
            "Connect claims and evidence more explicitly.",
            "Tighten paragraph structure around one argument each.",
            "Use a live grader for publication-quality evaluation.",
        ],
        "overall_score": overall,
    }


def grade_essay(
    essay_text: str,
    topic: str,
    teacher_config: dict[str, Any],
    objective_features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not essay_text.strip():
        raise ProviderError("The uploaded essay is empty after text extraction.")

    if teacher_config.get("base_url", "").strip().lower() == "mock":
        return mock_grade(essay_text, topic, teacher_config)

    messages = build_grading_messages(
        essay_text=essay_text,
        topic=topic,
        teacher_config=teacher_config,
        objective_features=objective_features,
    )
    retries = int(teacher_config.get("retry_attempts", 3) or 3)
    backoff_seconds = float(teacher_config.get("retry_backoff_seconds", 0.8) or 0.8)
    last_error: Exception | None = None
    model_text = ""

    for attempt in range(1, max(1, retries) + 1):
        try:
            payload = call_chat_completion(
                teacher_config,
                messages=messages,
                response_format={"type": "json_object"},
            )
            model_text = extract_message_text(payload)
            parsed = extract_json_object(model_text)
            return validate_grade_payload(parsed)
        except (json.JSONDecodeError, ValueError, TypeError, ProviderError) as exc:
            recovered = try_recover_payload_from_text(model_text)
            if recovered is not None:
                return validate_grade_payload(recovered)
            last_error = exc
            if attempt < max(1, retries):
                time.sleep(backoff_seconds * attempt)
                continue
            break

    raise ProviderError(
        f"{teacher_config.get('name', teacher_config.get('model', 'Teacher'))} returned invalid grading JSON: "
        f"{model_text[:500] if model_text else str(last_error)}"
    ) from last_error


def grade_essays_batch(
    essay_items: list[dict[str, str]],
    topic: str,
    teacher_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not essay_items:
        return {}

    for item in essay_items:
        if not str(item.get("text", "")).strip():
            raise ProviderError("Batch includes an empty essay after text extraction.")

    if teacher_config.get("base_url", "").strip().lower() == "mock":
        return {
            item["essay_id"]: mock_grade(item["text"], topic, teacher_config)
            for item in essay_items
        }

    messages = build_batch_grading_messages(topic=topic, essay_items=essay_items, teacher_config=teacher_config)
    retries = int(teacher_config.get("retry_attempts", 3) or 3)
    backoff_seconds = float(teacher_config.get("retry_backoff_seconds", 0.8) or 0.8)
    expected_ids = {item["essay_id"] for item in essay_items}

    last_error: Exception | None = None
    model_text = ""

    for attempt in range(1, max(1, retries) + 1):
        try:
            payload = call_chat_completion(
                teacher_config,
                messages=messages,
                response_format={"type": "json_object"},
            )
            model_text = extract_message_text(payload)
            parsed = extract_json_object(model_text)
            return validate_batch_payload(parsed, expected_ids)
        except (json.JSONDecodeError, ValueError, TypeError, ProviderError) as exc:
            last_error = exc
            if attempt < max(1, retries):
                time.sleep(backoff_seconds * attempt)
                continue
            break

    raise ProviderError(
        f"{teacher_config.get('name', teacher_config.get('model', 'Teacher'))} returned invalid batch grading JSON: "
        f"{model_text[:500] if model_text else str(last_error)}"
    ) from last_error


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"overall_score": 0.0, "criteria": []}

    aggregated_criteria = []
    for item in CRITERIA:
        scores = []
        for result in results:
            for criterion in result["criteria"]:
                if criterion["key"] == item["key"]:
                    scores.append(float(criterion["score"]))
                    break
        aggregated_criteria.append(
            {
                "key": item["key"],
                "label_zh": item["label_zh"],
                "label_en": item["label_en"],
                "score": round(mean(scores), 2) if scores else 0.0,
            }
        )

    return {
        "overall_score": round(mean(result["overall_score"] for result in results), 2),
        "criteria": aggregated_criteria,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_chat_messages(
    *,
    essay_text: str,
    topic: str,
    grade_result: dict[str, Any] | None,
    chat_history: list[dict[str, str]],
    user_message: str,
    teacher_config: dict[str, Any],
) -> list[dict[str, str]]:
    criteria_lines = []
    if grade_result:
        for item in grade_result.get("criteria", []):
            criteria_lines.append(
                f"- {item.get('label_zh', item.get('key'))}: {item.get('score')}/6; "
                f"理由: {item.get('reason', '')}; 建议: {item.get('improvement', '')}"
            )

    context = f"""
作文题目：{topic or '未提供'}
作文文本：
\"\"\"
{essay_text}
\"\"\"

已有评分：
总分：{grade_result.get('overall_score') if grade_result else '尚未评分'}
{chr(10).join(criteria_lines) if criteria_lines else '无'}
""".strip()

    system_prompt = (
        teacher_config.get("chat_system_prompt")
        or "你是一位耐心、具体、严格但鼓励学生的英语写作老师。请用中文回答，必要时引用英文原句并给出修改示例。"
    )
    messages = [{"role": "system", "content": f"{system_prompt}\n\n{context}"}]
    for item in chat_history[-12:]:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def chat_with_teacher(
    *,
    essay_text: str,
    topic: str,
    grade_result: dict[str, Any] | None,
    chat_history: list[dict[str, str]],
    user_message: str,
    teacher_config: dict[str, Any],
) -> str:
    if teacher_config.get("base_url", "").strip().lower() == "mock":
        score = grade_result.get("overall_score") if grade_result else "未评分"
        return (
            f"我先基于当前文本回应：这篇作文目前总分参考为 {score}/6。"
            "你可以优先追问某一段的论证、语法或衔接，我会给出具体改写。"
        )

    messages = build_chat_messages(
        essay_text=essay_text,
        topic=topic,
        grade_result=grade_result,
        chat_history=chat_history,
        user_message=user_message,
        teacher_config=teacher_config,
    )
    payload = call_chat_completion(teacher_config, messages=messages)
    return extract_message_text(payload)


def flatten_paper_aligned_row(
    *,
    session_id: str,
    source_name: str,
    topic: str,
    teacher_name: str,
    teacher_model: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    mapping = {
        "topic_and_completeness": "themenbezogenheit",
        "logic_and_composition": "logik-des-aufbaus",
        "expressiveness_and_comprehensibility": "ausfuehrlichkeit-aussagekraft",
        "language_mastery": "sprachbeherrschung",
        "complexity": "komplexitaet",
        "vocabulary_and_text_linking": "wortschatz-textverknuepfung",
        "language_constructs": "gebrauch-sprachlicher-strukturen",
    }
    row: dict[str, Any] = {
        "session": session_id,
        "quelle": source_name,
        "thema": topic,
        "erstellt": datetime.now(timezone.utc).isoformat(),
        "teacher_name": teacher_name,
        "teacher_model": teacher_model,
        "overall_score": result["overall_score"],
    }
    for criterion in result["criteria"]:
        row[mapping[criterion["key"]]] = criterion["score"]
    return row
