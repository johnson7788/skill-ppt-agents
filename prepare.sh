#!/usr/bin/env bash
# 准备 OpenSandbox 环境（Docker 后端）。详见 docs/OpenSandBox本地部署.md
set -euo pipefail

# 加载 .env 配置
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/.env" ]; then
  set -a
  source "${SCRIPT_DIR}/.env"
  set +a
fi

MIRROR="${OSB_MIRROR:-docker.1ms.run}"
API_KEY="${OSB_API_KEY:-123456}"
OSB_PORT="${OSB_PORT:-8080}"
# 必须与 opensandbox-server 期望的 execd 版本一致，否则每次建沙箱都要冷拉镜像、
# 30s 超时重试，产生一堆孤儿沙箱容器。当前服务端期望 v1.0.21。
EXECD_VER="${EXECD_VER:-v1.0.21}"
CONFIG="${HOME}/.sandbox.toml"

echo "==> 检查 Docker..."
docker info >/dev/null 2>&1 || { echo "Docker 未运行，请先启动 Docker Desktop。"; exit 1; }

echo "==> 拉取镜像..."
echo "==> 拉取 execd 镜像（opensandbox 注入每个沙箱的执行守护进程）..."
docker pull "${MIRROR}/opensandbox/execd:${EXECD_VER}"
docker tag  "${MIRROR}/opensandbox/execd:${EXECD_VER}" "opensandbox/execd:${EXECD_VER}"

echo "==> 构建沙箱镜像 my-sandbox:latest（python:3.12 + pandoc + 技能脚本）..."
docker build -t my-sandbox:latest "${SCRIPT_DIR}/sandbox-image"

echo "==> 写入服务端配置: ${CONFIG}"
if [ ! -f "${CONFIG}" ]; then
  uvx opensandbox-server init-config "${CONFIG}" --example docker
fi
# 确保 [server] 包含 host/port/api_key
python3 - "$CONFIG" "$API_KEY" "$OSB_PORT" <<'PY'
import sys, re, pathlib
path, key, port = sys.argv[1], sys.argv[2], sys.argv[3]
t = pathlib.Path(path).read_text()
if "[server]" not in t:
    t += f'\n[server]\nhost = "0.0.0.0"\nport = {port}\napi_key = "{key}"\n'
else:
    def ensure(text, k, v):
        pat = re.compile(rf'^{k}\s*=.*$', re.M)
        return pat.sub(f'{k} = {v}', text) if pat.search(text) else re.sub(r'(\[server\][^\[]*)', rf'\1{k} = {v}\n', text, count=1)
    t = ensure(t, "host", '"0.0.0.0"')
    t = ensure(t, "port", port)
    t = ensure(t, "api_key", f'"{key}"')
pathlib.Path(path).write_text(t)
print("配置写入完成")
PY

# 注：不再配置 osb CLI。应用（backend）通过 OpenSandbox Python SDK 连接，
# 连接参数走 .env 的 SANDBOX_DOMAIN/SANDBOX_PROTOCOL/SANDBOX_API_KEY，
# 无需安装 osb 命令行，也无需 ~/.opensandbox/config.toml。
# ponytail: osb CLI 仅用于手动调试沙箱；需要时 `uv tool install` 单独装。

cat <<EOF

完成（一次性准备）。下一步：
  1. 启动沙箱服务端：  ./start_sandbox.sh   （保持运行）
  2. 启动应用：        ./start.sh           （后端会自动向服务端创建每租户沙箱）
EOF
