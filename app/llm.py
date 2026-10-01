"""LLM 接入：流式问答 + 行内引用（对应 utopia-llm）。

环境变量（任意 OpenAI 兼容 chat completions 端点）：
    LLM_BASE_URL   — 例如 Ollama: http://localhost:11434/v1
    LLM_MODEL      — 模型名，例如 qwen2.5 / gpt-4o-mini
    LLM_API_KEY    — 可选

未配置时，/api/chat 会降级为「仅返回检索结果」，保证 Demo 无 LLM 也能跑。
"""

from __future__ import annotations

import json
import os
from typing import Iterator


def is_configured() -> bool:
    return bool(os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_MODEL"))


def build_prompt(query: str, chunks: list[dict]) -> str:
    """把检索到的分块编上 [n] 引用号，组进 prompt。"""
    context = "\n\n".join(f"[{i}] ({c['title']}) {c['content']}" for i, c in enumerate(chunks, start=1))
    return (
        "你是知识底座助手。只根据下面提供的资料回答，并在答案中用 [n] 标注引用来源"
        "（n 对应资料编号）。资料不足时明确说明，不要编造。\n\n"
        f"【资料】\n{context}\n\n"
        f"【问题】{query}\n\n"
        "【回答】"
    )


def stream_chat(query: str, chunks: list[dict]) -> Iterator[dict]:
    """逐 token 流式返回。yield 的 dict 形如 {"type": "token", "text": "..."}。"""
    import requests

    base = os.environ["LLM_BASE_URL"].rstrip("/")
    model = os.environ["LLM_MODEL"]
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("LLM_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是知识底座助手。"},
            {"role": "user", "content": build_prompt(query, chunks)},
        ],
        "stream": True,
    }
    r = requests.post(f"{base}/chat/completions", json=payload, headers=headers, stream=True, timeout=120)
    r.raise_for_status()

    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"].get("content")
            if delta:
                yield {"type": "token", "text": delta}
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


def complete(messages: list[dict], provider: dict[str, str] | None = None, max_tokens: int | None = None) -> str:
    """非流式补全，返回完整文本（用于 NL→SQL 等需要完整结果的任务）。"""
    import requests

    settings = provider if provider is not None else os.environ
    base = settings["LLM_BASE_URL"].rstrip("/")
    model = settings["LLM_MODEL"]
    headers = {"Content-Type": "application/json"}
    key = settings.get("LLM_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model, "messages": messages, "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    r = requests.post(
        f"{base}/chat/completions",
        json=payload,
        headers=headers,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
