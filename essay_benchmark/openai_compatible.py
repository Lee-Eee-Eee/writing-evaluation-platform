from __future__ import annotations

import json
import os
from typing import Any

import requests


class ProviderError(RuntimeError):
    """Raised when an OpenAI-compatible provider call fails."""


def normalize_chat_endpoint(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def resolve_api_key(config: dict[str, Any]) -> str:
    direct_key = (config.get("api_key") or "").strip()
    if direct_key:
        return direct_key

    env_name = (config.get("api_key_env") or "").strip()
    if not env_name:
        raise ProviderError(f"{config.get('name', 'Provider')} is missing an API key.")

    env_value = os.getenv(env_name, "").strip()
    if not env_value:
        raise ProviderError(
            f"{config.get('name', 'Provider')} expected environment variable {env_name}, "
            "but it is not set."
        )
    return env_value


def call_chat_completion(
    config: dict[str, Any],
    *,
    messages: list[dict[str, str]],
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    endpoint = normalize_chat_endpoint(config["base_url"])
    api_key = resolve_api_key(config)

    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "temperature": config.get("temperature", 0.2),
    }

    if config.get("max_tokens"):
        payload["max_tokens"] = config["max_tokens"]

    if response_format:
        payload["response_format"] = response_format

    extra_body = config.get("extra_body")
    if isinstance(extra_body, str) and extra_body.strip():
        extra_body = json.loads(extra_body)
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    extra_headers = config.get("extra_headers")
    if isinstance(extra_headers, str) and extra_headers.strip():
        extra_headers = json.loads(extra_headers)
    if isinstance(extra_headers, dict):
        headers.update({str(key): str(value) for key, value in extra_headers.items()})

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
    except requests.RequestException as exc:
        raise ProviderError(f"{config.get('name', 'Provider')} request failed: {exc}") from exc

    if response.status_code == 400 and response_format and "response_format" in response.text:
        payload.pop("response_format", None)
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
        except requests.RequestException as exc:
            raise ProviderError(f"{config.get('name', 'Provider')} request failed: {exc}") from exc

    if not response.ok:
        raise ProviderError(
            f"{config.get('name', 'Provider')} returned {response.status_code}: {response.text[:600]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            f"{config.get('name', 'Provider')} did not return valid JSON: {response.text[:600]}"
        ) from exc


def extract_message_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Unexpected provider payload: {payload}") from exc

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_chunks.append(str(item.get("text", "")))
        joined = "\n".join(chunk for chunk in text_chunks if chunk.strip()).strip()
        if joined:
            return joined

    raise ProviderError("Provider response did not contain text content.")

