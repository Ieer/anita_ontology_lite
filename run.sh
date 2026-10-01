#!/usr/bin/env bash
# 一键启动：建种子数据 → 检测/启动本地 Ollama → 并行起 FastAPI + Dash。
# 本地 Ollama 可用时自动注入离线 LLM + 嵌入环境变量（air-gapped 模式）。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PY="${PY:-python3}"
[ -d .venv ] && PY=".venv/bin/python"

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_BIN="${OLLAMA_BIN:-}"
OLLAMA_MODELS="${OLLAMA_MODELS:-}"
UTOPIA_DATA_ROOT="${UTOPIA_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/utopia-lite}"
export UTOPIA_DATA_ROOT

resolve_ollama_bin() {
    for p in \
        "$OLLAMA_BIN" \
        "$SCRIPT_DIR/../ollama/bin/ollama" \
        "$SCRIPT_DIR/../../ollama/bin/ollama" \
        "$SCRIPT_DIR/../runtime-assets/ollama/bin/ollama" \
        "$SCRIPT_DIR/../../runtime-assets/ollama/bin/ollama" \
        "/home/pi/utopia/ollama/bin/ollama" \
        "/home/pi/utopia/runtime-assets/ollama/bin/ollama" \
        "$(command -v ollama 2>/dev/null || true)"; do
        [ -n "$p" ] && [ -x "$p" ] && { printf '%s\n' "$p"; return 0; }
    done
    return 1
}

resolve_ollama_models_dir() {
    for p in \
        "$OLLAMA_MODELS" \
        "$SCRIPT_DIR/../models" \
        "$SCRIPT_DIR/../../models" \
        "$SCRIPT_DIR/../runtime-assets/models" \
        "$SCRIPT_DIR/../../runtime-assets/models" \
        "/home/pi/utopia/models" \
        "/home/pi/utopia/runtime-assets/models" \
        "$HOME/.ollama/models"; do
        [ -n "$p" ] && [ -d "$p" ] && { printf '%s\n' "$p"; return 0; }
    done
    return 1
}

if [ -z "$OLLAMA_BIN" ]; then
    OLLAMA_BIN="$(resolve_ollama_bin || true)"
fi
if [ -z "$OLLAMA_MODELS" ]; then
    OLLAMA_MODELS="$(resolve_ollama_models_dir || true)"
fi
if [ -n "$OLLAMA_MODELS" ]; then
    export OLLAMA_MODELS
fi

ollama_up() { curl -s --max-time 2 "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; }
ollama_has_model() {
    [ -n "$OLLAMA_BIN" ] && [ -x "$OLLAMA_BIN" ] || return 1
    "$OLLAMA_BIN" list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fx "$1" >/dev/null 2>&1
}

# ---- 检测/启动本地 Ollama ----
if ollama_up; then
    echo "==> 检测到本地 Ollama (${OLLAMA_HOST})"
elif [ -n "$OLLAMA_BIN" ] && [ -x "$OLLAMA_BIN" ]; then
    echo "==> 启动本地 Ollama (${OLLAMA_HOST}) ..."
    OLLAMA_ENV=(env OLLAMA_HOST="${OLLAMA_HOST}")
    if [ -n "$OLLAMA_MODELS" ]; then
        OLLAMA_ENV+=(OLLAMA_MODELS="${OLLAMA_MODELS}")
    fi
    nohup "${OLLAMA_ENV[@]}" "$OLLAMA_BIN" serve > /tmp/ollama-serve.log 2>&1 &
    sleep 5
    if ollama_up; then echo "==> Ollama 已就绪"; else echo "!! Ollama 启动失败，跳过本地模型"; fi
else
    echo "!! 未找到本地 Ollama；跳过本地模型（可用远程 LLM 或纯检索）"
fi

# ---- 注入本地模型环境变量（供 LLM / 嵌入 / NL→SQL 使用）----
if ollama_up; then
    LLM_NAME="${LLM_MODEL:-qwen2.5:1.5b}"
    EMB_NAME="${EMBEDDING_MODEL:-nomic-embed-text}"
    if [ -n "${LLM_BASE_URL:-}" ] && [ -n "${LLM_MODEL:-}" ]; then
        echo "==> 使用显式配置的 LLM：${LLM_MODEL}"
    elif ollama_has_model "$LLM_NAME"; then
        export LLM_BASE_URL="http://${OLLAMA_HOST}/v1"
        export LLM_MODEL="$LLM_NAME"
        echo "==> 已启用本地 LLM：${LLM_MODEL}"
    else
        echo "!! 本地 LLM 模型 ${LLM_NAME} 未安装，LLM 将降级为纯检索"
        unset LLM_BASE_URL LLM_MODEL
    fi

    if ollama_has_model "$EMB_NAME"; then
        export EMBEDDING_BASE_URL="http://${OLLAMA_HOST}/v1"
        export EMBEDDING_MODEL="$EMB_NAME"
        export UTOPIA_EMBED_BACKEND="openai"
        echo "==> 已启用本地嵌入模型：${EMBEDDING_MODEL}"
    else
        echo "!! 本地嵌入模型 ${EMB_NAME} 未安装，回退到 hash 嵌入"
        export UTOPIA_EMBED_BACKEND="hash"
        unset EMBEDDING_BASE_URL EMBEDDING_MODEL
    fi
fi

echo "==> 初始化演示数据（已有状态将保留）"
"$PY" seed.py

HOST="${UTOPIA_HOST:-127.0.0.1}"
API_PORT="${UTOPIA_API_PORT:-18000}"
DASH_PORT="${UTOPIA_PORT:-18050}"
export UTOPIA_PORT="$DASH_PORT"
echo "==> 启动 FastAPI (:${API_PORT}, host=$HOST)"
"$PY" -m uvicorn app.api:app --host "$HOST" --port "$API_PORT" &
API_PID=$!

echo "==> 启动 Dash (:${DASH_PORT})"
"$PY" dash_app.py &
DASH_PID=$!

trap 'kill $API_PID $DASH_PID 2>/dev/null || true' EXIT INT TERM
wait
