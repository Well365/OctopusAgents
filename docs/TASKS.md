# mob-remote — Task 文档

> 来源：2026-06-20 项目 review（架构 / 缺陷 / 安全三维度）。
> 优先级：P0 安全阻断 · P1 高 · P2 中 · P3 低。
> 状态：✅ 已完成 · 🔲 待办。

---

## ✅ 已完成（本轮）

| # | Task | 实现 |
|---|------|------|
| S1 | **fail-closed 白名单**：移除 `TG_RELAY_ALLOW_ALL_CHATS` 逃生舱，空白名单一律拒绝 | `chat_allowlist.py` `is_allowed` 返回 False；`tg-relay.py` 启动强制白名单 |
| S2 | **注入护栏**：剥离 C0/DEL 控制字符 + 单条长度上限 | `message_guard.py`（`TG_RELAY_MAX_INJECT_CHARS=2000`） |
| S3 | **速率限制**：每 chat 最小消息间隔 | `rate_limit.py`（`TG_RELAY_MIN_INTERVAL_SECS=1`） |
| I1 | iterm-monitor 崩溃自愈（看护循环 + 防爆） | `tg-stack-daemon.sh` |
| I2 | `oneClickStart.sh --watch` 代码/配置热重载 | `oneClickStart.sh` |
| I3 | `/new` 启动命令可配置（`AGENT_<KEY>_LAUNCH`） | `agent_cli.py` |
| I4 | slash 命令可靠提交（Ctrl-U 清行 + 双回车 + `/model` 自动确认） | `terminal_inject_lib.py` / `iterm-inject.py` / `tg_relay_patches.py` |
| I5 | `/shot` 增加 mac 屏幕 / 终端截图 | `mac-screenshot.py` / `tg_menu.py` |
| I6 | 三语 README 切换（中/英/日） | `README*.md` |

---

## 🔲 待办

### P1 — 高优先

#### ✅ T1. token 不暴露于进程 argv / 子进程环境 — 完成
- **问题**：`tg_relay_patches.py` 用 `repr(env)` 把 token 拼进 `python -c`（argv 全局可见）；`tg-stack-daemon.sh` `set -a; source` 把 token 导出给 monitor 子进程。
- **实现**：
  - `tg_relay_patches.py`：新增 `_sanitized_env()` 剔除 `TELEGRAM_BOT_TOKEN`；两个调度子进程不再把 env 拼进 `-c`（argv 干净），需要 token 的 monitor 从 `.env` 自读；按回车子进程不带 token。
  - `tg-stack-daemon.sh`：`load_env` 后 `export -n TELEGRAM_BOT_TOKEN`，monitor 从 `.env` 自读。
  - 新增 `test_relay_patches_secret.py`（4 项）。
- **验收**：✅ relay/monitor argv 不含 token；monitor 仍正常发送（从 .env 读到 token）。
- **已知残留**：Python `_load_env` 用 `os.environ.setdefault`（putenv），主进程运行后 `ps -eww`（仅同用户可见）仍可能显示 token —— 彻底消除需改 `_load_env` 不污染 `os.environ`，改动大、收益边际，暂留。

#### ✅ T2. 消除 AppleScript f-string 注入面 — 完成
- **问题**：`screenshot.py`、`terminal_spawn_lib.py` 把 `process_name`/`runner` 直接 f-string 拼进 AppleScript，含 `"` 会越界（H-2、H-3）。
- **实现**：各加 `_as_applescript_literal()`（转义 `\` `"` 换行并加引号），所有插值改为转义后的字面量。
  - `screenshot.py` `_get_window_bounds`/`_get_window_id`：app/process 名转义；去掉 error 文案里的二次插值；window_index 强制 int。
  - `terminal_spawn_lib.py` `build_spawn_applescript`：`do script` 两处用转义后的 `runner_lit`（同时修复含单引号 path 的越界）。
- **测试**：`test_terminal_spawn_lib.py` +2、`tests/test_screenshot.py` +2（含注入越界用例）。term-bridge 280 passed。
- **说明**：采用转义而非 env 传参 —— 对返回值型脚本与 `do script` 字面量都适用，等效消除注入面。

> ⚠️ 预存测试隔离缺陷（非本 task）：`tg-notify/tests/test_config.py` 2 项因项目根存在 `.env`（被读到真 token）失败。属测试隔离问题，归入 T3。

#### 🟡 T3. 核心路径补测试 — 部分完成
- **已完成**：
  - 修复 `tg-notify/tests/test_config.py` 2 个隔离失败：`load_dotenv()` 经 `find_dotenv()` 从 config.py 向上找到项目根 `.env` 回填真 token；autouse fixture 把 `_load_dotenv` 设为 noop 隔离。
  - 核心安全/纯逻辑路径补测试（本会话累计）：`test_chat_allowlist`(fail-closed)、`test_message_guard`、`test_rate_limit`、`test_relay_patches_secret`(token 剔除)、`test_relay_singleton`(单实例)、`test_relay_commands`(命令分发 smoke)、`test_terminal_spawn_lib`/`test_screenshot`(AppleScript 转义/注入)。
  - 现状：term-bridge 287 + tg-notify 14 = **301 passed**。
- **未完成（backlog）**：完整行覆盖 ≥60% 需为带 I/O 的路径建 mock 基础设施（telegram bot、osascript、subprocess）+ 引入 `pytest-cov`。`iterm-monitor.py`(532 行轮询循环)、`iterm_extract.py`(提取)的端到端路径仍主要靠手测。ROI 较低，单列后续。

#### ✅ T4. 单实例约束 / getUpdates 冲突 — 完成
- **澄清**：review 称「两 bot 都 run_polling」**不准确** —— iterm-monitor 是**出站-only**（`tg-notify send`，不调 getUpdates/run_polling），relay 是唯一 updates 消费者。真实风险是**多个 relay 实例**抢同一 token（409，也是早期 tg-setup 抓不到 /start 的根因）。
- **实现**：
  - `tg-relay.py`：启动单实例守卫 `_other_relay_pids()`（pgrep 检测已有 relay，排除自身）→ 明确拒绝启动。
  - `run_polling` 捕获 `telegram.error.Conflict`（409）→ 清晰提示并退出（让看护防爆停而非崩溃刷屏）。
  - `iterm-monitor.py`：docstring 标注「出站-only，永不消费 updates」。
- **测试**：`test_relay_singleton.py`（3 项）。term-bridge 283 passed。
- **验收**：✅ 第二个 relay 启动被拒（实测报 pid 冲突）。

### P2 — 中优先

#### T5. 抽公共 `env_util.load_env()`
- **问题**：`_load_env()` 在 9 个文件重复。
- **做法**：抽到 `term-bridge/env_util.py`，各处引用。
- **验收**：重复实现归一，全测试通过。

#### T6. AppleScript 注入抗竞态
- **问题**：硬编码 `delay 0.05`×40 抢焦点，繁忙时粘贴可能落错窗口；剪贴板异常路径会丢失。
- **做法**：超时可配 + 退避；剪贴板恢复用 `try/finally` 等价结构兜底。
- **验收**：高负载下注入成功率提升；剪贴板必恢复。

#### T7. 命名统一 + backend-aware 文案
- **问题**：`mobile-agent` / `mob-remote` 混用；`iterm` 字样在 backend=terminal 时仍出现。
- **做法**：统一品牌名;所有用户可见文案按 `resolve_backend()` 显示。
- **验收**：grep 无残留错配名称。

#### T8. 状态文件原子写
- **问题**：`screenshot_dedup.py` 直接 `write_text` 非原子（崩溃→损坏）。
- **位置**：`screenshot_dedup.py:56`
- **做法**：统一 `tmp + os.replace`（参考 `reply_dedup.py`）。

#### T9. `curl|bash` 安装加固
- **位置**：`agent_cli.py:25`
- **做法**：`curl --proto '=https' --tlsv1.2`；或改 npm 钉版本;装前先探测二进制。

### P3 — 低优先 / 体验

| # | Task |
|---|------|
| T10 | 结构化日志（`logging` 取代 `print`，分级 + 文件轮转） |
| T11 | `inbox/pending.txt` 自动轮转/截断（防无限增长）；`inbox/` 目录权限 0700 |
| T12 | `iterm-monitor.py`（532 行）/ `iterm_extract.py`（447 行）拆分模块 |
| T13 | 启动校验 `TELEGRAM_CHAT_ID` 为正整数，非法即报错 |
| T14 | `verify-no-secrets.sh` 装成 git pre-commit hook，并扫工作树 |

---

## 🆕 候选新功能（按价值排序）

| # | 功能 | 说明 |
|---|------|------|
| ✅ F1 | `/unlock` 远程解锁 Android — 完成 | droid-ctl `unlock`（动态算坐标、逐位 keyevent 输 PIN、PIN 不进 argv）+ relay `/unlock` 命令 + 菜单；PIN 存 `.env` `ANDROID_UNLOCK_PIN`。测试 6 项；唤醒实测有效。仅数字 PIN，图案/指纹不支持。 |
| ✅ F9 | Mac 隐私遮罩 `/lockmac` — 完成 | 盖屏防窥但**不锁机**（远程操作/截图不受影响）。Swift `CGShieldingWindowLevel` 全屏遮罩（盖菜单栏/Dock/所有 Space）+ **三重解除防锁死**：本地密码(salted SHA-256，break-glass) / TG `/lockmac off` / `--timeout` 兜底 + LaunchAgent 开机自启（读 `enable_on_boot`）。`./mob veil setup` 一次设置。测试 10 项；遮罩+解除+LaunchAgent 装卸均实测。**边界**：用户态遮罩，Force-Quit/SSH kill/重启可绕过——隐私级非安全锁。 |
| F2 | 审计日志 + `/last` | 回看最近注入/回复，安全可追溯 |
| F3 | 高危命令二次确认 | 注入含 `rm -rf` 等模式时要求确认 |
| F4 | 多 Mac / 多设备编排 | 一个 bot 管多机，`/host` 切换 |
| F5 | 会话快照 `/save` `/resume` | 存档/恢复某 tab 的 AI 上下文 |
| F6 | iOS 截图免折腾 | 封装 WDA 自动拉起，绕过 iOS 17 隧道坑 |
| F7 | Web 仪表盘 | 浏览器看所有 tab 状态 + 一键注入 |
| F8 | 主动通知（CI/任务完成）带按钮 | 「查看 / 重试」inline 按钮 |

---

## 建议下一步

按性价比：**T1 → T2 → T4 → T3**（安全收尾 + 可靠性），其余排入常规迭代。
新功能优先 **F1（/unlock）** 与 **F2（审计日志）**。
