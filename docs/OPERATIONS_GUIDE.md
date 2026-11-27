# 可维护操作说明手册（OPERATIONS GUIDE）

本手册面向长期无人值守运行与日常维护，覆盖：**环境安装 → Day 1 探针标定 → 夜间自动值守 → 早间 2 分钟复核 SOP → 外部互证与脱敏导出 → 常见故障排查**。

---

## 1. 环境准备与安装

### macOS（兼容 Intel `x86_64` 与 Apple Silicon / Mac mini）
```bash
# 1. 创建隔离虚拟环境（推荐 Python 3.10+；sounddevice 通常已内置 PortAudio）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 仅当安装或运行报错找不到 PortAudio 时再执行：
# brew install portaudio
# 仅当需要直接导入 iPhone .m4a/.aac 离线录音时安装：
# brew install ffmpeg
```

#### macOS 麦克风启用与防误过滤细则（Mac mini / 台式机必读）
1. **无内置麦主机如何接入**：Mac mini / 台式机默认无内置麦克风（`probe_mic.py --list-devices` 会显示空列表）。可直插 **3.5mm/USB-C 有线线控耳机麦**（如 EarPods，系统识别为“外置麦克风”）、**USB 麦克风**，或搭配旧手机离线录制后在 Web 台导入。**USB 麦克风请直插主机接口**，勿插在显示器自带 USB Hub 上（显示器息屏后可能断电）。
2. **选定输入设备与调节增益**：打开「**系统设置 → 声音 → 输入**」，选中外接麦克风，将输入音量调至 **75%–90%**（既保留微弱闷响灵敏度，又防止近距离响声过载导致 `peak > 0 dBFS`）。
3. **关闭系统降噪（关键）**：当麦克风处于激活状态时，点击屏幕右上角**控制中心**的橙色麦克风磁贴，将麦克风模式设为 **「标准」**，**切勿开启「语音突显（Voice Isolation）」**，否则系统会把楼上低频脚步闷响当背景杂音滤掉。
4. **授予麦克风权限**：首次在「终端 / iTerm2」运行 `probe_mic.py` 时，弹窗请点击允许（也可在「**系统设置 → 隐私与安全性 → 麦克风**」手动勾选对应终端应用）。**若后续改用 `launchd` 后台运行，请务必先在终端前台跑通一次探针完成授权**。
5. **有线耳机麦防干扰摆放**：将线控背面拾音孔朝上垫软布/海绵稳固放置，避免耳机线垂吊摩擦桌沿产生听诊器噪声，也不要压住线控按键。
6. **锁屏与关显示器过夜说明**：配合 `caffeinate -is` 运行时，系统级 `coreaudiod` 与防休眠断言会阻止整机睡眠，按 `Control + Command + Q` **锁屏**或**关闭显示器物理电源**均**不会**中断主机直连麦克风收音；但**切勿退出登录或切换 macOS 用户**，否则桌面音频会话会挂起。

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

### 前台试跑（白天调试或单晚手动值守）
```bash
# 白天调试（忽略 22:30-07:00 时间窗限制）
python scripts/run_monitor.py --ignore-schedule -v

# Mac mini / 无内置麦主机接有线耳机麦过夜（配合 caffeinate -is 防休眠，到 07:00 自动退出）
caffeinate -is python scripts/run_monitor.py --config config/profiles/tier0_wired_headset_mic.yaml --device "外置麦克风" -v
```
按 `Ctrl+C` 时程序会优雅退出，并自动把正在录制中的事件完整落盘、生成日报。建议过夜值守在独立的系统「终端」窗口中运行，避免关闭编辑器/IDE 时连带终止后台进程。

### macOS 夜间定时无人值守（推荐：终端 `--daemon` 昼伏夜出模式）

> **macOS 隐私权限重要提示**：新版 macOS（Sequoia+）对 `launchd` 直接拉起无 `NSMicrophoneUsageDescription` 声明的 `Python.app` 会**静默拦截麦克风**（不弹窗、不亮右上角黄色麦克风圆点，音频流全为 `-200 dBFS` 纯数字静音）。
> 因此 macOS 最稳妥的全自动方式是直接在已授予麦克风权限的 **iTerm2 / 系统「终端」** 独立标签页运行 `--daemon`：

```bash
# 在 iTerm2 / 终端常驻运行（白天 07:00–22:30 自动释放麦克风与防休眠，每晚 22:30 自动开麦并拉起 caffeinate -ims）
python scripts/run_monitor.py --daemon --config config/profiles/tier0_wired_headset_mic.yaml --device "外置麦克风" -v
```
- **白天（`07:00–22:30`）**：自动关闭音频流与 `caffeinate`，黄色麦克风指示灯熄灭，不阻止系统待机，心跳文件标为 `standby_outside_schedule`；
- **夜间（`22:30–07:00`）**：自动开启麦克风与 `caffeinate -ims` 防系统/磁盘休眠，100% 继承终端权限零弹窗，次日 `07:00` 自动落盘生成日报并切回白天待机。

### macOS `launchd` 定时任务（仅限已验证不被 TCC 静音拦截的环境）
```bash
# 默认使用 config/default.yaml
bash deploy/install_launchd.sh

# 若使用有线耳机麦 / Tier 1 USB 麦等自定义模板，可作为参数传入：
bash deploy/install_launchd.sh config/profiles/tier0_wired_headset_mic.yaml
```
- 每天 **22:30** 通过 `/usr/bin/caffeinate -ims` 自动拉起监测，次日 **07:00** 自动退出并生成日报；
- **首次务必核验**：启动 30 秒后查看 `data/launchd_stderr.log` 与 `data/monitor_heartbeat.json`，若出现 `pure digital silence (<= -199 dBFS)` 或 `zero_block_streak` 持续增长，说明被 macOS TCC 静默拦截，请卸载 `launchd` 并改用上方 `--daemon` 模式；
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
| `probe_mic.py --list-devices` 列表为空 | 主机无内置麦克风（如 Mac mini / 台式机），或未插妥/未授予系统麦克风权限 | 插入 3.5mm/USB 麦并在「系统设置 → 声音 → 输入」确认可见，且在「隐私与安全性 → 麦克风」允许终端访问 |
| 有线耳机麦 20–80Hz 底噪过高 | 3.5mm 模拟线控麦存在极低频滚降与工频底噪 | 改用 `config/profiles/tier0_wired_headset_mic.yaml`（`low_band_hz: [80.0, 220.0]`），并确认输入音量未拉满（75%–90%） |
| 启动 Web 台报 `Address already in use` | 本机已有服务占用了默认 `8765` 端口 | 通过 `--port` 换用空闲端口，例如 `python scripts/run_web.py --port 8766` |
| 锁屏/关显示器后采集中断 | 退出登录/切换用户，或麦克风插在会随息屏断电的显示器 USB Hub 上 | 保持当前账号仅锁屏（`Ctrl+Cmd+Q`）不注销，USB 麦直插主机机身接口，并使用 `caffeinate -is` 或 `launchd` 运行 |
