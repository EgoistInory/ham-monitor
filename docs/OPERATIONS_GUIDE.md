# 可维护操作说明手册（OPERATIONS GUIDE）

本手册面向长期无人值守运行与日常维护，覆盖：**环境安装 → Day 1 探针标定 → 夜间自动值守 → 早间 2 分钟复核 SOP → 外部互证与脱敏导出 → 常见故障排查**。

---

## 1. 环境准备与安装

### macOS（兼容 Intel `x86_64` 与 Apple Silicon）
```bash
# 1. 安装 PortAudio 底层音频库
brew install portaudio

# 2. 创建隔离虚拟环境（推荐 Python 3.10 / 3.11）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
> **macOS 权限提示**：首次在终端运行录音脚本时，系统会弹出“终端请求访问麦克风”，请务必点击允许；若使用 `launchd` 后台运行，请确保终端/Python 所在上下文已获麦克风权限，且已在系统控制中心关闭“语音突显”。

### Windows 10/11（PowerShell）
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux / 树莓派
```bash
sudo apt-get update && sudo apt-get install -y libportaudio2 libsndfile1 python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2. Day 1：声学探针标定（切勿跳过）

在正式开启夜间值守前，将设备摆放到实际监测位置，运行 60 秒探针：
```bash
# 查看可用麦克风索引
python scripts/probe_mic.py --list-devices

# 录制 60 秒（前 20 秒保持绝对安静，后半段可轻敲墙体/天花板或模拟人声）
python scripts/probe_mic.py --duration 60 --save-raw
```
1. 将控制台输出的 `20–80 Hz`、`80–180 Hz`、`300–4000 Hz` 底噪（P20）与突刺增量记入 `docs/FIELD_TEST.md`。
2. 参考脚本末尾打印的建议值，微调 `config/default.yaml` 中的 `trigger.low_min_dbfs` 与 `trigger.high_min_dbfs`。
3. 试听 `data/raw/probe_*.flac`，确认没有电流啸叫、爆音或被系统降噪吞掉低频。

---

## 3. 夜间自动值守部署

### 前台试跑（白天调试用）
```bash
python scripts/run_monitor.py --ignore-schedule -v
```
按 `Ctrl+C` 时程序会优雅退出，并自动把正在录制中的事件完整落盘、生成日报。

### macOS 夜间定时无人值守（推荐）
```bash
bash deploy/install_launchd.sh
```
- 每天 **22:30** 通过 `/usr/bin/caffeinate -is` 自动拉起监测（防系统休眠断采）；
- 次日 **07:00** 由程序内时间窗自动退出，并生成 `data/reports/YYYY-MM-DD_report.md`；
- 日志查看：`tail -f data/launchd_stderr.log`；
- 卸载定时任务：`launchctl unload ~/Library/LaunchAgents/com.ham.monitor.plist`。

---

## 4. 每日早间 2 分钟维护 SOP

> **可视化控制台快捷操作**：可直接启动本地 Web 工作台在浏览器中完成复核、试听、外部互证与脱敏导出（默认仅绑定 `127.0.0.1`，不对外暴露）：
> ```bash
> python scripts/run_web.py          # 正式证据库 (http://127.0.0.1:8765)
> python scripts/run_web.py --demo   # 隔离演示沙箱（预置合成样本）
> ```
> 如习惯纯命令行维护，可按以下步骤执行：

1. **查看早间日报**：打开 `data/reports/YYYY-MM-DD_report.md`，扫一眼昨夜触发次数、时段分布与频段特征。
2. **抽听并人工复核打标**（仅更新 `human_review`，绝不破坏原始证据哈希）：
   ```bash
   python scripts/review_event.py \
     --event-id 20261125_234218_primary_node \
     --label upstairs_impact \
     --notes "23:42 连续蹦跳并伴随喊叫，已向物业微信反馈"
   ```
   常用规范标签建议：`upstairs_impact`（楼上撞击/蹦跳）、`upstairs_vocal`（楼上叫喊/直播）、`upstairs_mixed`（混合）、`self_noise`（本户误触）、`street_noise`（窗外车流）、`appliance_hum`（设备共振）。
3. **录入外部互证**（如有联系物业、社区或报警，立刻入账）：
   ```bash
   python scripts/log_corroboration.py \
     --category property_notice \
     --ref "WY-20261125-01" \
     --summary "23:45 微信向物业报修，管家回复已电话提醒楼上" \
     --attachment /path/to/wechat_screenshot.png \
     --event-id 20261125_234218_primary_node
   ```
   支持类别：`property_notice` / `community_mediation` / `police_call` / `neighbor_witness` / `landlord_notice` / `cma_test` / `other`。
4. **刷新日报**：`python scripts/run_monitor.py --report-only --report-date 2026-11-25`，外部互证会自动合并进日报。

---

## 5. 完整性校验与脱敏对外提交

### 自检全库哈希链（防文件损坏或篡改）
```bash
python scripts/review_event.py --verify-chain
```

### 导出脱敏分享包（提交物业 / 房东 / 社区 / 12345）
```bash
# 仅导出指标、时间轴、哈希清单与脱敏摘要（零音频，最安全）
python scripts/export_share.py --date 2026-11-25

# 如需附带“已消除人声可懂度”的 250Hz 低通撞击音频：
python scripts/export_share.py --date 2026-11-25 --include-lowpass-audio
```
输出文件位于 `data/exports/share_bundle_YYYY-MM-DD.zip`。
如需做第三方防篡改时间锚定，可将包内的 `TIMESTAMP_MANIFEST.txt`（不含任何声音）提交至 OpenTimestamps (`ots stamp TIMESTAMP_MANIFEST.txt`) 或可信时间戳平台。

---

## 6. 存储管理与故障排查

| 现象 | 常见原因 | 排查与解决方法 |
| --- | --- | --- |
| 整晚 0 触发（但实际很吵） | `low_min_dbfs` / `high_min_dbfs` 设得太高，或系统开启了语音降噪 | 重跑 `probe_mic.py` 检查真实底噪，下调阈值 5–8 dB 并确认系统降噪已关闭 |
| 误报过多（空调/风声频触发） | `low_delta_db` 偏低或设备靠近空调/风扇 | 将 `low_delta_db` 提高至 `14–16 dB`，或将 `min_active_sec` 提高至 `1.0s` |
| 音频流中途断开 | USB 麦松动或系统短暂休眠 | `SoundDeviceAudioSource` 已内置每 3 秒自动重连；检查 `launchd_stderr.log` 并确认启用 `caffeinate -is` |
| 担心硬盘写满 | 仅命中事件写无损 FLAC（每分钟约 2–3 MB）；`raw/` 与 `exports/` 超 7 天自动清理；并内置 `max_evidence_mb`（5GB）/ `max_daily_evidence_mb`（500MB）/ `min_free_disk_mb`（1GB）三重熔断 | 在 Web 控制台「存储配额与空间管家」一键清理衍生包或已复核的本户/街噪误报音频，每月异地只读备份一次 `data/evidence/` 与 SQLite |
