#!/usr/bin/env bash
# 用户级安装 Ollama（arm64，无需 root），下载解压到 runtime-assets/ollama/。
# 依赖：curl + 一个带 zstandard 的 python（默认用本 demo 的 .venv）。
# 用法：
#   ./install_ollama.sh                     # 安装最新 arm64 版
#   OLLAMA_VERSION=v0.34.0 ./install_ollama.sh   # 指定版本
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -d "$WS_ROOT/ollama" ]; then
    DEST="$WS_ROOT/ollama"
elif [ -d "$WS_ROOT/runtime-assets/ollama" ]; then
    DEST="$WS_ROOT/runtime-assets/ollama"
else
    DEST="$WS_ROOT/ollama"
fi
VERSION="${OLLAMA_VERSION:-v0.34.0}"
URL="https://github.com/ollama/ollama/releases/download/${VERSION}/ollama-linux-arm64.tar.zst"

echo "==> 下载 Ollama ${VERSION} (arm64)"
mkdir -p "$DEST"
curl -fSL --progress-bar -o "$DEST/ollama.tar.zst" "$URL"

echo "==> 解压（python zstandard）"
PY="${PY:-$SCRIPT_DIR/.venv/bin/python}"
"$PY" -c "import zstandard" 2>/dev/null || { echo "    安装 zstandard..."; "$PY" -m pip install -q zstandard; }

cd "$DEST"
"$PY" - <<'PY'
import zstandard, tarfile
with open('ollama.tar.zst', 'rb') as f:
    with zstandard.ZstdDecompressor().stream_reader(f) as r:
        with tarfile.open(fileobj=r, mode='r|') as t:
            t.extractall()
print('解压完成')
PY
rm -f "$DEST/ollama.tar.zst"

echo "==> 验证"
"$DEST/bin/ollama" --version 2>&1 | grep -i version || true
echo "完成：$DEST/bin/ollama"
echo "下一步：./download_models.sh 拉取模型"
