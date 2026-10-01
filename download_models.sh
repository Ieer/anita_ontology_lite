#!/usr/bin/env bash
# 下载本地模型（供离线 LLM + 嵌入使用）。
# 用法：
#   ./download_models.sh                    # 下载默认组合（qwen2.5:1.5b + nomic-embed-text）
#   ./download_models.sh qwen2.5:3b        # 自定义模型（可多个，空格分隔）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_BIN="${OLLAMA_BIN:-}"
if [ -z "$OLLAMA_BIN" ]; then
    for p in \
        "$SCRIPT_DIR/../ollama/bin/ollama" \
        "$SCRIPT_DIR/../../ollama/bin/ollama" \
        "$SCRIPT_DIR/../runtime-assets/ollama/bin/ollama" \
        "$SCRIPT_DIR/../../runtime-assets/ollama/bin/ollama" \
        "/home/pi/utopia/ollama/bin/ollama" \
        "/home/pi/utopia/runtime-assets/ollama/bin/ollama" \
        "$(command -v ollama 2>/dev/null || true)"; do
        [ -n "$p" ] && [ -x "$p" ] && { OLLAMA_BIN="$p"; break; }
    done
fi

if [ $# -eq 0 ]; then
    MODELS=(qwen2.5:1.5b nomic-embed-text)
else
    MODELS=("$@")
fi

ollama_up() { curl -s --max-time 2 "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; }

# 确保 Ollama 在运行
if ! ollama_up; then
    if [ -x "$OLLAMA_BIN" ]; then
        echo "==> 启动 Ollama (${OLLAMA_HOST}) ..."
        nohup env OLLAMA_HOST="${OLLAMA_HOST}" "$OLLAMA_BIN" serve > /tmp/ollama-serve.log 2>&1 &
        sleep 5
        ollama_up || { echo "!! Ollama 启动失败，见 /tmp/ollama-serve.log"; exit 1; }
    else
        echo "!! 未找到 ollama 二进制：${OLLAMA_BIN}"
        echo "   请先运行 ./install_ollama.sh（用户级安装，无需 root）"
        exit 1
    fi
fi

for m in "${MODELS[@]}"; do
    echo "==> 拉取 $m"
    OLLAMA_HOST="${OLLAMA_HOST}" "$OLLAMA_BIN" pull "$m"
done

echo ""
echo "==> 完成，当前模型："
curl -s "http://${OLLAMA_HOST}/api/tags" | python3 -c "import sys,json; [print('  -', m['name']) for m in json.load(sys.stdin)['models']]" 2>/dev/null \
  || echo "  （解析失败，可 curl http://${OLLAMA_HOST}/api/tags 查看）"
