# Terminal.app 双向后端支持（并行脚本版）

**日期**: 2026-06-19
**状态**: 已批准，待实现

## 目标

让 term-bridge 的「iTerm ↔ Telegram」双向桥接也能作用于 macOS 系统自带的
**Terminal.app**。通过 `TG_TERM_BACKEND=iterm|terminal` 选择后端。iTerm 路径
保持零改动（默认仍是 `iterm`）。

本次采用**并行脚本**方案（新增一套 `terminal-*.py`），先把 Terminal.app 双向
跑通；后续再重构成统一的「terminal backend 接口」抽象（增量第二步，不在本 spec
范围内）。

## 背景：Terminal.app 与 iTerm 的能力差异

| 能力 | iTerm | Terminal.app | 结论 |
|------|-------|--------------|------|
| capture（读输出） | `tell app "iTerm"` 读 session contents | `tell app "Terminal" to get history of selected tab` | 干净，无需权限、不抢焦点 |
| screenshot | `id of window` = CGWindowID → `screencapture -l` | 同样 `id of window` = CGWindowID | 干净，复用截图链 |
| inject（写入正在跑的 TUI） | 静默写入 session | **无静默 API**，须 System Events `keystroke` | 需 Accessibility 权限，会短暂抢焦点 |
| split pane / session | 有 | 无（仅 window + tab） | Terminal 后端忽略 session |

提取/去重/格式化逻辑解析的是 Claude Code 的 TUI 渲染，与终端 app 无关，因此终端
无关、直接复用。

## 架构

### 复用（终端无关）

- `iterm_extract.py` — 回复提取、`new_content_since` 去重、`should_text_fallback` 90s 兜底
- `tg_format*.py` — Telegram 格式化
- `iterm_log_buffer.py` — 本地日志缓冲；`combined_text()` 自动拼接历史，弥补
  Terminal.app scrollback 较短的问题
- `screencapture -l <window-id>` 无激活截图发送链

### 新增（Terminal.app 特定）

- `term_backend.py` — 后端选择 helper
  - `resolve_backend() -> "iterm" | "terminal"`：读 `TG_TERM_BACKEND`，默认
    `iterm`；非法值回退 `iterm` 并打印警告；大小写不敏感
  - `capture_script() / screenshot_script() / inject_script() -> Path`：按当前后端
    返回对应脚本绝对路径
- `terminal-capture.py` — `tell app "Terminal" to get history of selected tab of
  window N`，再经 `combined_text()` 与日志缓冲拼接。结构镜像 `iterm-capture.py`
- `terminal-inject.py` — 剪贴板 + Cmd+V + 还原焦点（见下）
- `terminal-screenshot.py` — 复用截图链，取 window-id 时 app 名为 `Terminal`

### 改动（最小接线）

- `iterm-monitor.py`：`CAPTURE` / `ITERM_SHOT` 常量改为经 `term_backend` 解析；
  其余编排逻辑不变
- `tg_relay_patches.py`：inject 脚本路径改为 `term_backend.inject_script()`
- `iterm_shot.py`：`build_window_id_script` 与 `get_window_id` / `capture_window_png`
  增加 `app` 参数（默认 `"iTerm"`，保持现有测试绿），Terminal 传 `"Terminal"`
- `.env`：新增 `TG_TERM_BACKEND` 文档行
- 目标解析复用现有 `resolve_target()`（`TG_ITERM_WINDOW/TAB`，命名暂不改）；
  Terminal 后端忽略 `session`

## Inject 机制（剪贴板 + Cmd+V + 还原焦点）

`terminal-inject.py` 的 AppleScript 流程：

1. 存当前剪贴板（best-effort，文本）
2. `set the clipboard to <文字>`
3. 记下当前前台 app（`frontmost process`）
4. `tell app "Terminal" to activate`，选中目标 window/tab
5. System Events `keystroke "v" using command down`
6. （`--submit` 时）`keystroke return`
7. 还原焦点：reactivate 之前的前台 app
8. 还原剪贴板（best-effort）

**权限**：需在「系统设置 → 隐私与安全性 → 辅助功能」授权控制 Terminal 的宿主
进程。首次失败报 `-25211 / assistive access`；脚本捕获后返回清晰中文提示，且消息
仍写入 `inbox` 备份，不丢失。

## 错误处理

- 后端非法值 → 回退 iterm + 警告，不崩
- inject 缺 Accessibility 权限 → 返回带授权指引的中文错误；relay 已有 `inbox 备份`
  路径兜底
- capture / screenshot osascript 失败 → 返回非零码 + stderr，由 monitor 既有错误
  日志路径处理
- 所有 spawn 的子进程沿用既有 `stdin=subprocess.DEVNULL`（守护进程 fd0 已关闭，
  否则子 Python 启动崩溃）

## 测试

### 纯函数单测（pytest，无需 GUI）

- `test_term_backend.py`：默认 iterm / `terminal` / 非法回退 / 大小写
- `test_terminal_capture.py`：AppleScript 构建 + 输出解析
- `test_terminal_inject.py`：AppleScript 构建（剪贴板 set、Cmd+V、焦点还原、
  `--submit` 分支）
- `test_iterm_shot.py`（扩充）：`build_window_id_script(app=...)` 的 Terminal 分支；
  现有 13 个测试保持绿

### 集成测试（手动，macOS，需 Terminal 运行 + Accessibility）

capture / inject / screenshot 三条链端到端各跑一次实拍验证。

## 回归保证

默认 `TG_TERM_BACKEND=iterm`，所有现有 iTerm 路径与当前 58 个测试不变。

## 不在本次范围

- 统一 terminal backend 接口抽象（增量第二步）
- Terminal.app 的 tab 名路由（对应 iTerm 的 `iterm_route`/`iterm_tabs`）
- 重命名 `TG_ITERM_*` 环境变量
