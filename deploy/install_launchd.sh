#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
PLIST_TEMPLATE="${REPO_DIR}/deploy/com.ham.monitor.plist"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${TARGET_DIR}/com.ham.monitor.plist"
CONFIG_ARG="${1:-config/default.yaml}"

if [[ "${CONFIG_ARG}" = /* ]]; then
  CONFIG_PATH="${CONFIG_ARG}"
else
  CONFIG_PATH="${REPO_DIR}/${CONFIG_ARG}"
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "未找到虚拟环境解释器: ${PYTHON_BIN}"
  echo "请先执行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "未找到配置文件: ${CONFIG_PATH}"
  exit 1
fi

mkdir -p "${TARGET_DIR}" "${REPO_DIR}/data"

sed \
  -e "s|__REPO_DIR__|${REPO_DIR}|g" \
  -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
  -e "s|__CONFIG_PATH__|${CONFIG_PATH}|g" \
  "${PLIST_TEMPLATE}" > "${TARGET_PLIST}"

launchctl unload "${TARGET_PLIST}" 2>/dev/null || true
launchctl load "${TARGET_PLIST}"

echo "已安装并加载 launchd 夜间定时任务: ${TARGET_PLIST}"
echo "使用配置: ${CONFIG_PATH}"
echo "每天 22:30 将自动启动监测（配合 caffeinate -is 防休眠），次日 07:00 自动停止并输出日报。"
