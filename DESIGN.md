# DESIGN.md — HAM Monitor 老钱档案级声学存证设计系统（v0.3 Archival Edition）

> 参考 `Personal Budget Dashboard (Willowsum)` 的私行档案美学、`E-commerce Admin Dashboard` 的滑出抽屉与微动效纪律、以及 `Service Status Page` 的高权威状态展示，专为“长期室内声学监测与防篡改存证”量身定制。

## 1. 审美定位：私人银行级司法存证档案馆（Old-Money Archival Platform）

- **拒绝“电商轻飘感”与“廉价开发面板感”**：不用亮靛蓝、不用大面积深灰压迫、不用廉价大阴影卡片堆砌。
- **核心视觉语言**：
  - **深森林绿架构侧栏（`#15332B`）** 建立沉稳、克制、机构级的视觉锚点；
  - **温润象牙档案纸底色（`#F4F1E8`）** 搭配 **羊皮白主平面（`#FFFDF8`）** 与 `1px solid #DFDDD4` 极细装订线，营造高雅印刷品般的留白与呼吸感；
  - **衬线大标题与核心数值**（`Georgia`, `"Noto Serif SC"`, `"Songti SC"`, serif）+ **清晰无衬线正文** + **等宽对齐数据**（`tabular-nums`），形成一眼可辨的老钱大平台级排版层级；
  - **克制动效纪律（`<=320ms`）**：`240ms cubic-bezier(0.22, 0.61, 0.36, 1)` 视图轻浮入场、`320ms cubic-bezier(0.65, 0, 0.35, 1)` 右侧存证抽屉平滑滑出、`prefers-reduced-motion` 自动降级。

## 2. 色彩 Token 契约

### 日间模式：Archival Ivory（默认）
| Token | Hex | 角色 |
| --- | --- | --- |
| `--bg-canvas` | `#F4F1E8` | 温润象牙档案纸画布 |
| `--bg-surface` | `#FFFDF8` | 羊皮白主卡片与顶栏 |
| `--bg-rail` | `#15332B` | 深英国森林绿固定左侧栏 |
| `--rail-active` | `#E6EFE9` | 侧栏当前激活柔雾绿胶囊 |
| `--border-rule` | `#DFDDD4` | `1px` 装订线细边框 |
| `--ink-primary` | `#1C2420` | 深墨绿炭黑主字色 |
| `--ink-secondary` | `#5B6660` | 柔和次级说明字色 |
| `--accent` | `#183B32` | 主操作森林绿按钮与聚焦环 |
| `--spotlight-bg` | `#E4ECC6` | 关键指标卡柔黄绿聚焦底色 |
| `--signal-low` | `#1D4E6B` | 低频楼板撞击信号（深普鲁士蓝） |
| `--signal-high` | `#A34F39` | 中高频人声/告警信号（赤陶赭红） |
| `--status-ok` | `#1F7A53` | 哈希链完整 / 已人工复核 |

### 深夜模式：Nocturnal Forest（凌晨卧室核查暗色）
- `--bg-canvas`: `#0C1210` / `--bg-surface`: `#121C19` / `--bg-rail`: `#090E0C`
- `--border-rule`: `#22322D` / `--ink-primary`: `#F0EFE9` / `--accent`: `#6DC49E`

## 3. 空间节奏与交互架构

- `8px` 基准网格，主内容最大宽 `1440px`，外边距 `32px`，卡片内边距 `24px`，圆角 `14px`（卡片）/ `8px`（按钮输入）/ `999px`（状态胶囊）。
- **双路径高效复核**：在“总览与研判”或“声学证据库”点击任意事件行，即可直接从右侧平滑滑出 **480px 存证检验抽屉**（含点击寻址示波器、默认 `≤250 Hz` 脱敏播放器、SHA-256 与一键人工复核），无需反复跳转页面。
