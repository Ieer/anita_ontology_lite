"""可插拔嵌入后端。

通过环境变量选择后端（默认 hash，零依赖开箱即用）：

    UTOPIA_EMBED_BACKEND = hash | openai | sentence

- hash    ：字符三元组哈希（默认，无需任何依赖/网络，64 维）
- openai  ：任意 OpenAI 兼容 API（Ollama / vLLM / DeepSeek / 硅基流动…）
            需 EMBEDDING_BASE_URL + EMBEDDING_MODEL，可选 EMBEDDING_API_KEY
            例：Ollama →  base=http://localhost:11434/v1  model=nomic-embed-text
- sentence：本地 sentence-transformers（需 pip install sentence-transformers）
            可选 EMBEDDING_MODEL 指定模型，默认 all-MiniLM-L6-v2（384 维）

get_dim() 返回当前后端的向量维度，db.py 据此创建 vec0 表。
配置缺失或探测失败时会打印告警并自动回退到 hash，保证 Demo 始终可跑。
"""

from __future__ import annotations

import hashlib
import math
import os
import re

HASH_DIM = 64

_backend = os.environ.get("UTOPIA_EMBED_BACKEND", "hash").strip().lower()
_dim: int | None = None

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+")

_openai_session = None
_st_model = None


# ---------- 对外接口 ----------

def get_dim() -> int:
    global _dim
    if _dim is None:
        _init()
    return _dim


def embed(text: str) -> list[float]:
    global _dim
    if _dim is None:
        _init()
    if _backend == "openai":
        return _embed_openai(text)
    if _backend == "sentence":
        return _embed_sentence(text)
    return _embed_hash(text, _dim or HASH_DIM)


# ---------- 初始化：决定后端 + 维度，失败回退 hash ----------

def _init() -> None:
    global _backend, _dim
    if _backend == "openai":
        base = os.environ.get("EMBEDDING_BASE_URL")
        model = os.environ.get("EMBEDDING_MODEL")
        if not base or not model:
            print("[embeddings] openai 后端缺 EMBEDDING_BASE_URL / EMBEDDING_MODEL，回退到 hash")
            _backend, _dim = "hash", HASH_DIM
            return
        try:
            _dim = len(_embed_openai("probe"))
        except Exception as e:  # noqa: BLE001
            print(f"[embeddings] openai 探测失败：{e}，回退到 hash")
            _backend, _dim = "hash", HASH_DIM
    elif _backend == "sentence":
        try:
            _dim = _load_st().get_sentence_embedding_dimension()
        except ImportError:
            print("[embeddings] 未安装 sentence-transformers，回退到 hash")
            _backend, _dim = "hash", HASH_DIM
        except Exception as e:  # noqa: BLE001
            print(f"[embeddings] sentence 模型加载失败：{e}，回退到 hash")
            _backend, _dim = "hash", HASH_DIM
    else:
        _backend, _dim = "hash", HASH_DIM


# ---------- 后端实现 ----------

def _embed_hash(text: str, dim: int) -> list[float]:
    text = (text or "").lower()
    vec = [0.0] * dim
    for token in _WORD_RE.findall(text):
        padded = "  " + token + "  "
        for i in range(len(padded) - 2):
            trigram = padded[i : i + 3]
            h = int.from_bytes(hashlib.md5(trigram.encode("utf-8")).digest()[:8], "little")
            idx = h % dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _embed_openai(text: str) -> list[float]:
    import requests

    global _openai_session
    base = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
    model = os.environ["EMBEDDING_MODEL"]
    if _openai_session is None:
        _openai_session = requests.Session()
        key = os.environ.get("EMBEDDING_API_KEY", "")
        if key:
            _openai_session.headers["Authorization"] = f"Bearer {key}"
    r = _openai_session.post(
        f"{base}/embeddings", json={"model": model, "input": text}, timeout=30
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def _load_st():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    return _st_model


def _embed_sentence(text: str) -> list[float]:
    return _load_st().encode(text).tolist()
