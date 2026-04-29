from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from essay_benchmark.grading import aggregate_results, chat_with_teacher, grade_essay
from essay_benchmark.objective_features import OBJECTIVE_METRICS, compute_objective_features
from essay_benchmark.openai_compatible import ProviderError
from essay_benchmark.study import (
    load_rubric,
    load_teacher_presets,
)
from essay_benchmark.text_utils import read_text_bytes, word_count

app = Flask(__name__, static_folder="web", static_url_path="")


@app.errorhandler(Exception)
def handle_exception(exc: Exception) -> object:
    if not request.path.startswith("/api/"):
        if isinstance(exc, HTTPException):
            return exc
        raise exc

    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description, "status": exc.code}), exc.code

    app.logger.exception("Unhandled API error")
    return jsonify({"error": "服务器内部错误，请查看 Render 日志或本地控制台。", "detail": str(exc)}), 500


def _load_study_config() -> dict:
    return {
        "rubric": load_rubric(),
        "objective_metrics": OBJECTIVE_METRICS,
        "teacher_presets": load_teacher_presets(),
    }


def _metric_by_key(objective: dict, key: str) -> dict:
    for metric in objective.get("metrics", []):
        if metric.get("key") == key:
            return metric
    return {}


def _score_band(score: float) -> str:
    if score >= 5:
        return "较强"
    if score >= 4:
        return "稳定"
    if score >= 3:
        return "有基础但仍需打磨"
    return "需要优先加强"


def build_writing_guidance(essay_text: str, topic: str, objective: dict, aggregate: dict) -> str:
    criteria = aggregate.get("criteria", [])
    sorted_criteria = sorted(criteria, key=lambda item: float(item.get("score", 0)))
    weakest = sorted_criteria[:2]
    strongest = sorted_criteria[-2:][::-1]

    lexical = _metric_by_key(objective, "lexical_diversity_mtld")
    discourse = _metric_by_key(objective, "discourse_markers")
    clauses = _metric_by_key(objective, "syntactic_complexity_clauses")
    modals = _metric_by_key(objective, "modals")
    word_count = objective.get("word_count", 0)
    sentence_count = objective.get("sentence_count", 0)
    avg_sentence = objective.get("average_sentence_length", 0)
    overall = float(aggregate.get("overall_score", 0))

    if word_count < 160:
        length_note = "篇幅偏短，观点容易显得概括，需要增加例证和解释。"
    elif word_count > 320:
        length_note = "篇幅较充分，下一步重点是压缩重复表达、提高论证密度。"
    else:
        length_note = "篇幅基本适中，可以把精力放在论证层次和语言精确度上。"

    if float(discourse.get("value", 0) or 0) <= 2:
        cohesion_note = "语篇标记语偏少，段落之间可以增加 therefore、however、moreover 等衔接。"
    else:
        cohesion_note = "文章已经使用了一些衔接表达，后续要避免机械堆叠连接词。"

    if float(clauses.get("per_sentence", 0) or 0) < 0.5:
        syntax_note = "句式复杂度偏低，可以加入让步、原因、条件等从句来提升表达层次。"
    else:
        syntax_note = "句式有一定变化，注意复杂句不要牺牲清晰度。"

    strongest_text = "、".join(
        f"{item.get('label_zh')}（{item.get('score')}/6）" for item in strongest
    ) or "暂未形成明显优势维度"
    weakest_text = "、".join(
        f"{item.get('label_zh')}（{item.get('score')}/6）" for item in weakest
    ) or "暂未发现明显短板"

    return f"""我先给出一版写作指导，后面你可以继续追问某一段怎么改。

整体判断：这篇作文的七维平均分为 {overall}/6，整体处于“{_score_band(overall)}”区间。文章主题是“{topic or '未提供题目'}”，目前内容围绕主题展开较清楚，但还可以在论证深度、衔接方式和语言变化上继续提升。

七维评分观察：相对优势是 {strongest_text}；优先改进项是 {weakest_text}。建议你下一轮修改时先处理最低的两个维度，而不是平均用力。

客观特征观察：全文约 {word_count} 词、{sentence_count} 句，平均句长约 {avg_sentence} 词。词汇多样性 MTLD 为 {lexical.get('value', '-')}，情态动词数量为 {modals.get('value', '-')}，语篇标记语数量为 {discourse.get('value', '-')}。{length_note}{cohesion_note}{syntax_note}

具体建议：
1. 每个主体段保留一个中心论点，并补上更具体的例子或原因链。
2. 在段落开头和句间加入更自然的逻辑衔接，让读者看见“为什么这一点推出下一点”。
3. 挑 2-3 个简单句改成包含 because、although、which、therefore 的复合表达。
4. 修改时优先保证准确和清楚，再追求更复杂的词汇。"""


@app.get("/")
def index() -> object:
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/study-config")
def study_config() -> object:
    return jsonify(_load_study_config())


@app.post("/api/grade")
def grade() -> object:
    topic = (request.form.get("topic") or "").strip()
    essay_text = (request.form.get("essay_text") or "").strip()
    teachers_raw = request.form.get("teachers") or "[]"

    file = request.files.get("file")
    if file and file.filename:
        essay_text = read_text_bytes(file.filename, file.read())

    if not essay_text:
        return jsonify({"error": "No essay text was provided."}), 400

    try:
        teachers = json.loads(teachers_raw)
    except json.JSONDecodeError:
        return jsonify({"error": "Teacher configuration is not valid JSON."}), 400

    if not isinstance(teachers, list) or not teachers:
        return jsonify({"error": "Please provide at least one teacher configuration."}), 400

    try:
        objective = compute_objective_features(essay_text)
    except Exception as exc:
        app.logger.exception("Objective feature computation failed")
        return jsonify({"error": "客观特征计算失败。", "detail": str(exc)}), 500
    successes = []
    failures = []
    for teacher in teachers:
        name = teacher.get("name") or teacher.get("model") or "Unnamed teacher"
        try:
            result = grade_essay(essay_text, topic, teacher, objective_features=objective)
            successes.append(
                {
                    "teacher": {
                        "name": name,
                        "model": teacher.get("model", ""),
                        "base_url": teacher.get("base_url", ""),
                    },
                    "result": result,
                }
            )
        except (ProviderError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            failures.append({"teacher": name, "error": str(exc)})

    if not successes:
        return jsonify({"error": "All teacher calls failed.", "failures": failures}), 502

    aggregate = aggregate_results([item["result"] for item in successes])
    guidance = build_writing_guidance(essay_text, topic, objective, aggregate)

    return jsonify(
        {
            "essay": {
                "topic": topic,
                "word_count": word_count(essay_text),
                "text": essay_text,
            },
            "objective": objective,
            "results": successes,
            "failures": failures,
            "aggregate": aggregate,
            "guidance": guidance,
        }
    )


@app.post("/api/objective-analysis")
def objective_analysis() -> object:
    essay_text = (request.form.get("essay_text") or "").strip()
    file = request.files.get("file")
    if file and file.filename:
        essay_text = read_text_bytes(file.filename, file.read())

    if not essay_text:
        return jsonify({"error": "No essay text was provided."}), 400

    try:
        objective = compute_objective_features(essay_text)
    except Exception as exc:
        app.logger.exception("Objective feature computation failed")
        return jsonify({"error": "客观特征计算失败。", "detail": str(exc)}), 500

    return jsonify({"essay": {"word_count": word_count(essay_text), "text": essay_text}, "objective": objective})


@app.post("/api/chat")
def chat() -> object:
    payload = request.get_json(silent=True) or {}
    essay_text = str(payload.get("essay_text") or "").strip()
    topic = str(payload.get("topic") or "").strip()
    user_message = str(payload.get("message") or "").strip()
    teacher = payload.get("teacher") or {}
    grade_result = payload.get("grade_result")
    chat_history = payload.get("history") or []

    if not essay_text:
        return jsonify({"error": "No essay text was provided."}), 400
    if not user_message:
        return jsonify({"error": "No chat message was provided."}), 400
    if not isinstance(teacher, dict) or not teacher:
        return jsonify({"error": "Please provide one teacher configuration for chat."}), 400
    if not isinstance(chat_history, list):
        chat_history = []
    if not isinstance(grade_result, dict):
        grade_result = None

    try:
        reply = chat_with_teacher(
            essay_text=essay_text,
            topic=topic,
            grade_result=grade_result,
            chat_history=chat_history,
            user_message=user_message,
            teacher_config=teacher,
        )
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"reply": reply})


@app.get("/<path:path>")
def static_proxy(path: str) -> object:
    candidate = Path(app.static_folder) / path
    if candidate.exists():
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
