# ham-monitor 架构与工程规范（v0.3）

## 1. 设计目标与工程红线

- **被动监测原则**：本系统仅用于室内被动声学监测、客观日志记录与自证取证，永久拒绝集成任何主动反制或发声干扰功能。
- **计量诚实原则**：未经法定计量校准的消费级麦克风（笔记本内置麦、手机、普通 USB 麦）只记录相对数字电平 `dBFS` 与超静音基线增量 `delta_db`，严禁冒充国标 `dB SPL` 或 `dB(A)`。
- **隐私优先原则（Privacy-by-Design）**：100% 本地离线运行，无云端上传；对外分享强制走脱敏导出包，仅导出时间轴、哈希摘要及可选 `≤250 Hz` 低通撞击音频，彻底剥离人声对话可懂度与本机绝对路径。

## 2. 全栈分层架构（采集 → 存证 → 脱敏 → 控制台）

```text
[1. 前端采集层] AudioSource (SoundDeviceAudioSource 实时自愈 / SyntheticAudioSource 离线导入)
   │
   ├──► [Raw 证据流：零滤波、零降噪]
   │       └──► AudioRingBuffer (预录 15s) ──► 命中落盘无损单声道 FLAC + SHA-256
   │
   └──► [Analysis 分析流：只读副本]
           └──► AnalysisPreprocessor (Phase 2 可插拔稳态降噪接口)
                   └──► DualBandAnalyzer (20–180Hz / 300–4000Hz SOS 滤波 + 20% 分位数动态基线)
                           └──► EventTrigger 状态机 (IDLE -> PENDING -> ACTIVE -> COOLDOWN)
                                   │
[2. 存证与互证层]                  ▼
   EvidenceStore (SQLite + JSON Sidecar)
   ├── events 表：严格隔离 raw_evidence (不可变) / algo_metrics / human_review (仅人工可写)
   ├── 追加哈希链：prev_chain_hash -> chain_hash，防事后增删改
   └── corroboration_logs 表：物业劝阻 / 社区调解 / 110 回执 / 邻居证言 / CMA 互证台账
                                   │
[3. 脱敏与多节点层]                ▼
   ├── ham/desensitize.py：8 阶零相位低通 (≤250Hz) + PII 路径/手机号/房号清洗 + ZIP 打包 + OpenTimestamps 清单
   └── ham/correlate.py：Phase 2 ceiling vs room_center ±2s 粗窗跨节点能量差对比
                                   │
[4. 本地 Web 管理台]               ▼
   ham/web/app.py (FastAPI，默认仅绑定 127.0.0.1:8765)
   ├── REST API：/api/overview, /api/events, /api/events/{id}/waveform, /api/events/{id}/audio (HTTP 206 Range),
   │             /api/events/{id}/review, /api/chain/verify, /api/corroborations, /api/correlate, /api/export/share
   └── ham/web/static/：零 Node 构建依赖的原生 ES Module 档案级前端工作台（详见 DESIGN.md）
```

## 3. 事件数据契约（三组不可混写字段）

1. `raw_evidence`（不可变事实）：`file`、`sha256`、`prev_chain_hash`、`chain_hash`、`sample_rate`、`channels`、`ntp_synced`、`device_calibrated`、`node_id`、`location_tag`、`start_time`、`end_time`、`trigger_time`。
2. `algo_metrics`（算法自动推断）：`trigger_band`、`peak_dbfs`、`baseline_dbfs`、`delta_db`、`low_peak_dbfs`、`high_peak_dbfs`、`duration_sec`、`inferred_tag`。
3. `human_review`（人工复核）：`verified`、`label`、`notes`。人工打标接口（CLI 与 Web `PATCH /review`）只允许更新 `human_review`，严禁改写前两组字段。

## 4. Phase 2 / Phase 3 扩展接口预留

| 演进方向 | 已预留接口 | 演进接入方式 |
| --- | --- | --- |
| 精度与校准 | `DeviceProfile.calibrated` / `calibration_file` / `sensitivity_offset_db` | 接入 UMIK-1 等测量麦校准曲线，切换为参考 `dB(A)`/`dB(C)` |
| 多节点对比与本户排除 | `node_id` + `location_tag` + `ham/correlate.py` | 备用手机置顶（`ceiling`）+ 主机室内中部（`room_center`）粗窗对比 |
| 同源时钟精确定位 | `AudioSource` 抽象基类 | 接入多通道 USB 声卡（空气麦 + 接触式压电片）跑 GCC-PHAT |
| 稳态底噪抑制 | `AnalysisPreprocessor` 抽象接口 | 在分析流插入谱减法/维纳滤波，过滤空调、净化器与窗外车流 |
