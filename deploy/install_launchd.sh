#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
PLIST_TEMPLATE="${REPO_DIR}/deploy/com.ham.monitor.plist"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${TARGET_DIR}/com.ham.monitor.plist"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "未找到虚拟环境解释器: ${PYTHON_BIN}"
  echo "请先执行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "${TARGET_DIR}" "${REPO_DIR}/data"

sed \
  -e "s|__REPO_DIR__|${REPO_DIR}|g" \
  -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
  "${PLIST_TEMPLATE}" > "${TARGET_PLIST}"

launchctl unload "${TARGET_PLIST}" 2>/dev/null || true
launchctl load "${TARGET_PLIST}"

echo "已安装并加载 launchd 夜间定时任务: ${TARGET_PLIST}"
echo "每天 22:30 将自动启动监测（配合 caffeinate -is 防休眠），次日 07:00 自动停止并输出日报。"
