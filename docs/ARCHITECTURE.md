# ham-monitor 架构与工程规范（Phase 1 MVP）

## 1. 设计目标与红线

- **被动监测原则**：本系统仅用于室内被动声学监测、客观日志记录与自证取证，不包含任何主动反制、发声干扰功能。
- **计量诚实原则**：未经校准的内置麦或普通 USB 麦只记录相对数字电平 `dBFS` 与超底噪增量 `delta_db`，严禁将未校准读数标注为国标 `dB SPL` 或 `dB(A)`。
- **隐私与证据边界**：仅在设定夜间窗口（默认 `22:30 - 07:00`）触发录制；对外提交物业/房东时优先提供时间轴日报、频段指标与脱敏摘要，避免传播他人清晰私密对话。

## 2. 双流分离架构（Raw vs Analysis）

```text
AudioSource (sounddevice / Synthetic)
   │
   ├──► [Raw 证据流：零滤波、零降噪]
   │       └──► AudioRingBuffer (预录 15s) ──► 事件命中时落盘无损 FLAC + SHA-256
   │
   └──► [Analysis 分析流：只读副本]
           └──► AnalysisPreprocessor (Phase 2 可插拔稳态降噪)
                   └──► DualBandAnalyzer (20-180Hz / 300-4000Hz SOS 滤波 + 动态分位数底噪)
                           └──► EventTrigger 状态机 (IDLE -> PENDING -> ACTIVE -> COOLDOWN)
```

- **Raw 证据流**与**Analysis 分析流**在内存中严格隔离：算法滤波和未来降噪绝不修改落盘 FLAC 的任何采样点。
- **状态机合并机制**：连续蹦跳或间歇鬼叫在 `post_roll_sec`（默认 10s）内再次触发时，自动合并为同一连续事件，避免产生大量碎片文件。

## 3. 事件数据契约（三组不可混写字段）

本地 SQLite (`ham_events.sqlite3`) 与单事件 JSON sidecar 严格分离三组信息：

1. `raw_evidence`（不可变事实）：`file`、`sha256`、`sample_rate`、`channels`、`ntp_synced`、`device_calibrated`、`node_id`、`location_tag`、`start_time`、`end_time`。
2. `algo_metrics`（算法自动推断）：`trigger_band`、`peak_dbfs`、`baseline_dbfs`、`delta_db`、`duration_sec`、`low_peak_dbfs`、`high_peak_dbfs`、`inferred_tag`。
3. `human_review`（人工复核）：`verified`、`label`、`notes`。人工打标接口只允许更新 `human_review`，严禁改写前两组字段。

## 4. Phase 2 / Phase 3 扩展接口预留

| 演进方向 | Phase 1 预留接口 | Phase 2/3 接入方式 |
| --- | --- | --- |
| 精度与校准 | `NodeConfig.calibrated` / `calibration_file` / `DeviceProfile` | 接入 UMIK-1 等测量麦校准曲线，切换为真实 `dB(A)`/`dB(C)` |
| 多节点对比与本户排除 | `node_id` + `location_tag` (`room_center` / `ceiling`) | 旧安卓机置顶对比跨节点能量比 + 100ms 粗窗事件合并 |
| 同源时钟定位 | `AudioSource` 抽象基类 | 接入多通道 USB 声卡（空气麦 + 接触式压电传感器）跑 GCC-PHAT |
| 稳态底噪抑制 | `AnalysisPreprocessor` 接口 | 在分析流中插入谱减法/维纳滤波，过滤空调、净化器与车流 |
