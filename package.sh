#!/usr/bin/env bash
# 打包标准版（源码 + venv + Ollama 裁 CUDA + 模型）为 tar.gz，供内网离线部署。
# 用法：./package.sh [输出目录]   （默认 /mnt/usb/PWT/app）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${1:-/mnt/usb/PWT/app}"
PKG_NAME="utopia-lite-demo-arm64.tar.gz"
BUILD="$WS_ROOT/staging/.utopia-pkg-build"

echo "==> 清理旧构建目录"
rm -rf "$BUILD"; mkdir -p "$BUILD"

echo "==> 1/5 复制运行所需源码 + .venv（不包含本地配置与数据）"
mkdir -p "$BUILD/utopia-lite-demo"
for item in app assets content ui .venv; do
    cp -r "$SCRIPT_DIR/$item" "$BUILD/utopia-lite-demo/$item"
done
for item in dash_app.py mcp_server.py README.md requirements.txt run.sh schema.sql seed.py; do
    cp "$SCRIPT_DIR/$item" "$BUILD/utopia-lite-demo/$item"
done
find "$BUILD/utopia-lite-demo" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$BUILD/utopia-lite-demo" -name '*.pyc' -delete 2>/dev/null || true
SUSPECT_FILE="$(find "$BUILD/utopia-lite-demo" -type f \( -name '.env' -o -name '.env.*' -o -name '.netrc' -o -name 'pip.conf' -o -name '*.key' -o -name '*credentials*.json' \) -print -quit)"
if [[ -n "$SUSPECT_FILE" ]]; then
    echo "!! 打包中止：检测到可能包含凭据的文件 $SUSPECT_FILE" >&2
    exit 1
fi

echo "==> 2/5 复制 Ollama（裁 CUDA）"
mkdir -p "$BUILD/ollama/bin" "$BUILD/ollama/lib/ollama"
cp "$WS_ROOT/runtime-assets/ollama/bin/ollama" "$BUILD/ollama/bin/ollama"
for item in "$WS_ROOT/runtime-assets/ollama/lib/ollama/"*; do
    base="$(basename "$item")"
    [ "$base" = "cuda_v12" ] || [ "$base" = "cuda_v13" ] || cp -r "$item" "$BUILD/ollama/lib/ollama/"
done

echo "==> 3/5 复制模型"
mkdir -p "$BUILD/models"
cp -r ~/.ollama/models/blobs "$BUILD/models/blobs"
cp -r ~/.ollama/models/manifests "$BUILD/models/manifests"

echo "==> 4/5 写 deploy.sh"
cat > "$BUILD/deploy.sh" <<'DEPLOY'
#!/usr/bin/env bash
# utopia-lite-demo 内网一键部署/启动（本地 Ollama + 内嵌模型，完全离线）
set -euo pipefail
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
export OLLAMA_BIN="$PKG_DIR/ollama/bin/ollama"
export OLLAMA_MODELS="$PKG_DIR/models"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export UTOPIA_HOST="${UTOPIA_HOST:-0.0.0.0}"
export UTOPIA_API_PORT="${UTOPIA_API_PORT:-18000}"
export UTOPIA_PORT="${UTOPIA_PORT:-18050}"

echo "==> 检查本地 Ollama"
if curl -s --max-time 2 "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    echo "    Ollama 已在运行"
else
    echo "    启动内嵌 Ollama ..."
    nohup env OLLAMA_HOST="${OLLAMA_HOST}" OLLAMA_MODELS="$OLLAMA_MODELS" "$OLLAMA_BIN" serve > /tmp/ollama-serve.log 2>&1 &
    sleep 6
fi
echo "    可用模型："
curl -s "http://${OLLAMA_HOST}/api/tags" | python3 -c "import sys,json; [print('      -', m['name']) for m in json.load(sys.stdin)['models']]" 2>/dev/null || echo "      (未就绪，见 /tmp/ollama-serve.log)"

echo "==> 启动 demo（FastAPI :${UTOPIA_API_PORT} + Dash :${UTOPIA_PORT}，监听 ${UTOPIA_HOST}）"
echo "!! 安全提示：除本地数据路由外，API 未统一鉴权；仅在可信内网使用。"
cd "$PKG_DIR/utopia-lite-demo"
exec ./run.sh
DEPLOY
chmod +x "$BUILD/deploy.sh"

echo "==> 5/5 打包到 $OUT_DIR/$PKG_NAME"
mkdir -p "$OUT_DIR"
tar czf "$OUT_DIR/$PKG_NAME" -C "$BUILD" .

echo "==> 完成"
ls -lh "$OUT_DIR/$PKG_NAME"
