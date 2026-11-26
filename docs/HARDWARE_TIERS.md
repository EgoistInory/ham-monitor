# 分级硬件选型与部署指南（Tier 0 → Tier 2）

不同维权者的预算、闲置硬件与技术背景各不相同。`ham-monitor` 将硬件方案拆为三个平滑升级的层级，**强烈建议从 Tier 0（零成本）跑通 3 天验证信号后再决定是否花钱升级**，避免一上来购买高价硬件踩坑。

---

## 总览对比矩阵

| 层级 | 典型硬件组合 | 额外成本 | 计量状态 | 适用场景与核心能力 | 对应配置模板 |
| --- | --- | --- | --- | --- | --- |
| **Tier 0（初级 / 零成本）** | 闲置笔记本（macOS / 旧 Win / Linux）、Mac mini + 有线线控耳机麦，或闲置手机 | **0 元** | 未校准 (`dBFS`) | 今晚即可启动！抓取夜间人声鬼叫与楼板撞击谐波，摸清作息规律 | `config/profiles/tier0_laptop_builtin.yaml` / `tier0_wired_headset_mic.yaml` |
| **Tier 1（进阶 / 补强拾音与方位）** | 旧主机 + 百元级平直频响 USB 麦（架高朝天花板）**或** 旧笔记本 + 旧安卓机双节点 | **0 – 400 元** | 未校准 (`dBFS`) | 显著改善 40–180Hz 低频楼板闷响信噪比；支持 `ceiling` vs `room_center` 双节点能量比排除本户/马路干扰 | `config/profiles/tier1_usb_mic_ceiling.yaml` |
| **Tier 2（专业 / 量化与精准定位）** | 校准测量麦（如 miniDSP UMIK-1）或 多通道同源时钟声卡（空气麦 + 接触式压电传感器）+ CMA 现场检测衔接 | **700 – 2500+ 元** | 已校准 (`dB SPL` 参考) | 输出接近国标计权的长期声压趋势曲线；同源时钟支持互相关定位；为正式诉讼前的 CMA 机构精准选点定时间 | `config/profiles/tier2_calibrated_measurement.yaml` |

---

## Tier 0：零成本起步方案

### 方案 0A：闲置笔记本直录（推荐首选，兼容 Intel/ARM macOS、Windows、Linux）
- **系统准备（重要避坑）**：
  - **关闭系统语音降噪**：macOS 录音时务必关闭“语音突显（Voice Isolation）”与环境降噪；Windows 关闭“音频增强 / 噪音抑制”，否则系统会把楼板低频闷响当成背景杂音滤掉。
  - **老旧笔记本长期插电防鼓包**：macOS 笔记本可使用 AlDente 等工具将充电上限锁死在 **50%–60%**；Windows 笔记本在厂商电源管理中开启“养护模式（60%上限）”。
- **摆放建议**：尽量靠近主要受扰卧室区域，机身下方垫一本厚书或折叠毛巾，避免桌面风扇/硬盘共振传导至内置麦。

### 方案 0B：闲置手机接入（iPhone / 安卓，Edge-First 边缘自治模式）

> **架构原则（为什么不做“服务端检测到噪声后再远程唤醒手机录音”？）**
> 楼上突发重砸或尖叫只有几秒，若等主节点判定后再经 Wi-Fi 远程唤醒手机开麦，起爆瞬间与前 15 秒上下文必定丢失。本项目采用安防/地震台网标准的 **Edge-First Store-and-Forward（边缘先本地保底 + 可靠回传中心端）** 架构：

1. **手机端（iPhone / 安卓零折腾直传）**：
   - 将旧手机开飞行模式（仅留 Wi-Fi 校时）、底部垫厚海绵放衣柜顶（`ceiling`），用系统录音（如 iPhone 语音备忘录 `.m4a` 或无损 WAV App）整晚本地录存；
   - 次日连同一局域网 Wi-Fi，在中心电脑运行 `python scripts/run_web.py --host 0.0.0.0`（自动生成安全接入令牌 `X-HAM-Token`）；
   - 手机或电脑浏览器打开 Web 控制台 **「脱敏导出与双节点 → 手机/外部设备音频直传导入」**，直接上传音频（`.wav/.flac/.ogg/.mp3` 原生解码，`.m4a` 装有 `ffmpeg` 自动解码），后端自动切分超标事件并纳入哈希链；也可继续用命令行 [import_audio.py](../scripts/import_audio.py) 离线导入。
2. **第二台电脑 / 树莓派 / 安卓 Termux（全自动边缘同步节点）**：
   - 在边缘机 `config/default.yaml` 中开启 `sync.enabled: true` 并填好中心服务端 `server_url` 与 `token`；
   - 边缘机触发事件时**先在本地无损落盘 FLAC + SHA-256**，再异步推送至中心端 `POST /api/ingest/event`（校验 `client_sha256` 比特级一致 + 幂等去重）；若 Wi-Fi 断线，自动写入本地重试队列 `data/sync_queue.json`，网络恢复后自动补传。

### 方案 0C：无内置麦主机（Mac mini / 台式机）+ 有线线控耳机麦（如 EarPods 应急）
- **适用场景**：主机没有内置麦克风，临时插一根带线控麦的 3.5mm / USB-C 有线耳机即可当晚投入监测。
- **核心频段避坑**：此类通信耳机麦在 `20–80 Hz` 普遍存在硬件高通滚降与 50Hz 工频/模拟接口底噪，直接套用默认 `20–180 Hz` 会稀释楼板撞击突刺。推荐使用专用配置 [tier0_wired_headset_mic.yaml](../config/profiles/tier0_wired_headset_mic.yaml)（将 `dsp.low_band_hz` 收窄至 `[80.0, 220.0]` 聚焦撞击谐波）：
  ```bash
  caffeinate -is python scripts/run_monitor.py --config config/profiles/tier0_wired_headset_mic.yaml -v
  ```
- **摆放与系统建议**：
  - 将**线控模块（背面麦克风孔）**垫软布/折叠纸巾/海绵稳固放置并朝向房间/天花板；切勿让耳机线悬空晃动摩擦桌沿（听诊器效应），也不要压住播放/音量按键。
  - macOS 在控制中心关闭“语音突显（Voice Isolation）”，并在「系统设置 → 声音 → 输入」将外置麦克风增益调至 75%–90%。

---

## Tier 1：进阶拾音与双节点“本户排除”

### 何时需要升级到 Tier 1？
运行 `python scripts/probe_mic.py --duration 60` 后，如果发现：
- 笔记本内置麦在 `20–180 Hz` 对楼板撞击的突刺增量（`snr_headroom_db`）`< 6 dB`；或
- 需要有力反驳“声音是你自己家或窗外马路发出的”质疑。

### 推荐做法
1. **外置 USB 麦**：选择免驱、心形/超心形指向或全指向、频响覆盖 `20Hz–20kHz` 且自带减振架的 USB 麦克风，通过长 USB 线架高指向天花板（避开空调出风口直吹）。
2. **零成本双节点对比（笔记本 + 旧手机）**：
   - 节点 A（`location_tag: "ceiling"`）：旧手机垫海绵放衣柜顶，距天花板 20–40 cm；
   - 节点 B（`location_tag: "room_center"`）：笔记本放卧室中部桌面；
   - 次日将手机音频用 `import_audio.py` 导入同一个数据库后，运行：
     ```bash
     python scripts/compare_nodes.py --date 2026-11-25
     ```
   - 由于独立设备晶振存在分钟级毫秒漂移，系统采用 **±2 秒粗时间窗匹配 + 跨节点能量差对比**（而非易失效的跨设备 TDOA）：若 `ceiling` 能量显著高于 `room_center`（如 `+3 dB` 以上），可强力支持声源来自上方楼板结构。

---

## Tier 2：校准测量与司法衔接

- **校准测量麦（如 UMIK-1）**：使用 `config/profiles/tier2_calibrated_measurement.yaml`，填入厂商 `.txt` 校准文件及灵敏度偏移，记录更贴近物理声压的趋势数据。
- **独立离线分贝记录仪（Class 2，支持 A/C 计权）**：适合长期无人值守生成秒级客观曲线图。
- **司法终极衔接（CMA 资质检测）**：若案件进入民事起诉阶段，先用本工具前 2 周的日报精准锁定“楼上最稳态的高发闹腾时段”（例如每晚 23:30–01:30），再预约具备 CMA 资质的第三方机构在该黄金窗口上门一次测出法定报告，大幅节省数千元反复扑空的检测费。
