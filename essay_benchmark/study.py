from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
WEB_DIR = ROOT_DIR / "web"
SCRIPTS_DIR = ROOT_DIR / "scripts"
OUTPUT_DIR = ROOT_DIR / "outputs"
ORIGINAL_STUDY_DIR = ROOT_DIR / "sherbold-chatgpt-student-essay-study-3f09052"
ORIGINAL_DATA_DIR = ORIGINAL_STUDY_DIR / "data"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_rubric() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "rubric.json")


def load_teacher_presets() -> list[dict[str, Any]]:
    return load_json(CONFIG_DIR / "teacher_presets.json")


def load_reference_stats() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "reference_stats.json")


def load_representative_topics() -> list[dict[str, Any]]:
    return load_json(CONFIG_DIR / "representative_topics.json")

