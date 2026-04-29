from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from essay_benchmark.grading import aggregate_results, chat_with_teacher, grade_essay
from essay_benchmark.objective_features import OBJECTIVE_METRICS, compute_objective_features
from essay_benchmark.openai_compatible import ProviderError
from essay_benchmark.study import (
    load_rubric,
    load_teacher_presets,
)
from essay_benchmark.text_utils import read_text_bytes, word_count

app = Flask(__name__, static_folder="web", static_url_path="")


def _load_study_config() -> dict:
    return {
        "rubric": load_rubric(),
        "objective_metrics": OBJECTIVE_METRICS,
        "teacher_presets": load_teacher_presets(),
    }


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

    objective = compute_objective_features(essay_text)
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
        except ProviderError as exc:
            failures.append({"teacher": name, "error": str(exc)})

    if not successes:
        return jsonify({"error": "All teacher calls failed.", "failures": failures}), 502

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
            "aggregate": aggregate_results([item["result"] for item in successes]),
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

    return jsonify({"essay": {"word_count": word_count(essay_text), "text": essay_text}, "objective": compute_objective_features(essay_text)})


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
