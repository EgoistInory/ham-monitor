# ham-monitor

> **开源、离线、隐私优先的室内被动声学监测与可验证维权取证工具**
> 将夜间主观的“楼上又在吵”，转化为**不可篡改、结构化、可隐私脱敏外发**的多维证据链。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 为什么做这个项目？

长期夜间邻里噪声（楼板蹦跳、深夜直播鬼叫、违规住改商等）的核心痛点往往不是“听不见”，而是：
1. **精力内耗**：半夜惊醒手动举手机录像，长期摧毁全家睡眠与情绪；
2. **证据孤立**：零散手机录音难以证明长期性与规律性，缺乏前置劝阻与外部互证；
3. **隐私反噬**：直接把录有清晰人声的原始音频发给物业/房东/群里，存在侵犯他人隐私的法律风险；
4. **设备门槛高**：普通人既不需要花数万元砸吊顶，也不必一开始就买专业声级计。

`ham-monitor` 借鉴环境公民科学（**NoiseCapture** / **Sensor.Community**）、数字取证保管链（**ProofMode** / **OpenTimestamps**）与行车记录仪预录架构，提供一套**从 0 成本旧设备到专业校准麦**平滑演进的开源自救工具箱。

## 核心能力

- **严格双流分离**：原始 PCM 经 15 秒内存环形缓冲直接存为无损单声道 `FLAC`（零滤波、零修改）；所有频段滤波与阈值判定仅在隔离分析副本中运行。
- **双频带动态触发**：低频撞击带（`20–180 Hz` 抓蹦跳/拖拽）+ 中高频人声带（`300–4000 Hz` 抓叫喊/喊麦），结合 20% 分位数滚动底噪基线与事件期基线冻结，支持连续事件自动合并。
- **防篡改哈希链 + 外部互证台账**：SQLite 严格隔离 `raw_evidence` / `algo_metrics` / `human_review`，每条事件写入 `SHA-256` 与追加式 `chain_hash`，并支持关联物业劝阻、社区调解、110 出警回执、邻居证言等非声学互证记录。
- **隐私脱敏一键导出**：内置 `export_share.py`，自动剥离本机绝对路径与手机号/房号 PII，仅导出客观时间轴、哈希清单（支持 OpenTimestamps 零音频时间戳锚定）及可选 **≤250 Hz 消除人声可懂度的低通撞击音频**。
- **分级硬件全覆盖**：支持旧笔记本直录（Tier 0）、旧安卓机离线音频导入（Tier 0）、外置 USB 麦与双节点能量比对比排除本户干扰（Tier 1）、校准测量麦接口预留（Tier 2）。

## 文档导航

| 文档 | 内容说明 |
| --- | --- |
| [操作说明手册 (OPERATIONS_GUIDE.md)](docs/OPERATIONS_GUIDE.md) | 安装、探针标定、夜间值守、早间 2 分钟复核 SOP、脱敏导出与故障排查 |
| [分级硬件选型指南 (HARDWARE_TIERS.md)](docs/HARDWARE_TIERS.md) | Tier 0（0 成本）→ Tier 1（百元补强/双节点）→ Tier 2（专业校准与 CMA 衔接） |
| [问题边界与跨领域案例 (PROBLEM_BOUNDARIES_AND_CASES.md)](docs/PROBLEM_BOUNDARIES_AND_CASES.md) | 声源归因盲区批判、孤证天花板、跨领域开源取证参考与自查清单 |
| [数据安全与隐私红线 (SECURITY_AND_PRIVACY.md)](SECURITY_AND_PRIVACY.md) | 法律红线、隐私脱敏机制、开源分享自查清单与免责声明 |
| [架构与扩展契约 (ARCHITECTURE.md)](docs/ARCHITECTURE.md) | 双流架构、数据库契约与 Phase 2/3 插件扩展点 |
| [现场实测模板 (FIELD_TEST.md)](docs/FIELD_TEST.md) | 探针实测底噪与信噪比记录表 |

## 3 分钟快速开始

```bash
# 1. 创建虚拟环境并安装依赖（macOS 需先 brew install portaudio）
python3 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Day 1：检查麦克风并跑 60 秒现场探针
python scripts/probe_mic.py --list-devices
python scripts/probe_mic.py --duration 60

# 3. Day 2：前台试运行（忽略夜间时间窗，Ctrl+C 优雅落盘）
python scripts/run_monitor.py --ignore-schedule

# 4. Day 3：macOS 安装每晚 22:30 自动启动的 launchd 定时任务
bash deploy/install_launchd.sh
```

### 常用日常命令

```bash
# 校验全库哈希链与所有 FLAC 文件未被篡改
python scripts/review_event.py --verify-chain

# 人工复核打标（仅改 human_review，不动原始证据）
python scripts/review_event.py --event-id <event_id> --label upstairs_impact --notes "已反馈物业"

# 录入 110 / 物业 / 社区 / 邻居互证记录
python scripts/log_corroboration.py --category property_notice --ref "WY-01" --summary "管家已上门劝阻"

# 导出隐私脱敏分享包（供物业/房东/社区/12345 提交）
python scripts/export_share.py --date 2026-11-25 --include-lowpass-audio

# 导入旧安卓机整晚录音并做 ceiling vs room_center 双节点对比
python scripts/import_audio.py --input night.wav --start-time "2026-11-25T23:00:00+08:00" --node-id android_ceiling --location-tag ceiling
python scripts/compare_nodes.py --date 2026-11-25
```

## 运行离线测试

```bash
pytest -v
```
