# mob-remote

**简体中文** | [English](README.en.md) | [日本語](README.ja.md)

---

**Telegram 远程 + Android (droid-ctl) + iOS (iphone-ctl/WDA)** 自包含封装包。

可单独克隆、分发、运行，不依赖任何外部业务项目。

> **Telegram 配置** → [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)（中/En/日）  
> **远程驱动 Claude Code / Codex** → [可视化说明页](docs/TG_ITERM_AI_FLOW.html) · [docs/ITERM_MULTI_TAB.md](docs/ITERM_MULTI_TAB.md)  
> **依赖与安装** → [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) · [docs/INSTALL.md](docs/INSTALL.md)

## 远程驱动 Claude Code / Codex（Terminal/iTerm 注入回传）

把 Telegram 当成「AI 编程助手的遥控器」：手机发一句话 → 自动注入到 Mac 上某个正在跑
**Claude Code / Codex** 的终端标签页 → AI 执行 → 助手回复自动回传 Telegram。人不在电脑前也能隔空指挥多个项目的 AI 会话。

> **终端后端可选**：`.env` 的 `TG_TERM_BACKEND` 选择注入/捕获后端，默认 `terminal`
> （系统自带 Terminal.app），也可设 `iterm` 使用 iTerm2。注入、截图、回传脚本各有两套实现，由 `term_backend.py` 统一切换。

![Telegram → iTerm2 Claude Code/Codex 流程图](docs/tg-iterm-flow.svg)

<details>
<summary>纯文本版流程图</summary>

```
📱 你发 [t3] 修登录bug
        │  tg-relay 解析前缀 (iterm_route.py)
        ▼
   iterm-inject  ── AppleScript 键入+回车 ──►  iTerm2 · tab 3
                                                 └─ Claude Code / Codex 执行
                                                       │ 输出 → inbox/iterm-session-*.log
        ┌────────────────────────────────────────────┘
        ▼
   iterm-monitor ── 提取助手回复 (iterm_extract.py) ──►  tg-notify send
        │
        ▼
📱 收到 AI 回复（+ 长时间无输出时改发截图兜底）
```

</details>

**消息前缀决定发给哪个 tab**（一段对话里随意切换，无需改配置）：

| 写法 | 示例 | 说明 |
|------|------|------|
| `[tN]` / `#N` / `@tN:` | `[t3] 列目录` | 按标签序号，最常用 |
| `[名字]` / `@名字:` | `[myapp] 跑测试` | 模糊匹配 tab 标题目录片段 |
| `[别名]` | `[fz] 看部署` | `.env` 的 `TG_ITERM_ALIASES` 精确映射 |
| 无前缀 | `看下 git 状态` | 落到 `.env` 默认 tab |

> **路由优先级**：单条消息前缀 `[t3]` > `/tab` 设的持久默认 > `.env` 的 `TG_ITERM_TAB`。
> 用 `/tab` 选一次后，后续无前缀消息的注入与回传都跟随该 tab（含卡住自动按 Enter / 空闲截图）。
>
> **稳定锁定（不会错窗口）**：选中的目标按**稳定标识**锚定——优先匹配会话 tty（位置无关），
> Terminal.app 还用窗口的稳定 `id`（不是会随焦点变化的 z-order 序号）。所以你切换窗口焦点、
> 重排或关闭其它 tab，消息仍然进当初选的那个终端，不会发岔。

**启用**（前置：已配好 Bot Token / Chat ID）：

```bash
# .env
TG_RELAY_ITERM_INJECT=1      # 自然语言 → 注入终端
TG_TERM_BACKEND=terminal     # 终端后端：terminal(默认, 系统自带 Terminal.app) | iterm
TG_ITERM_MONITOR_AFTER=45    # 注入后多久开始抓取回传

./mob iterm-buffer-setup     # 增大滚动缓冲，避免长回复被截断（一次）
./mob iterm-list             # 查看 tab 序号 / 推荐前缀
./mob up                     # 同时启动 tg-relay（收）+ iterm-monitor（回传）
```

### 手机一句话开新会话：`/new`

不必先在电脑上开好终端——手机发 `/new` 即可在新标签页里起一个全新 AI 会话：

```
/new claude 修复登录的 bug      # 新建 Terminal 标签 → 在 ~/fullStar/<时间戳> 里启动 claude，并把这句话作为首个 prompt
/new codex                      # 同理，启动 codex（不带 prompt）
```

会自动：新建标签页（无窗口则新建窗口）→ `mkdir` 一个带时间戳的工作目录 → 若未安装对应 CLI 则
先自动安装 → 启动 agent（`claude --permission-mode bypassPermissions` / `codex`）。开完会话后，
后续普通消息会自动注入到这个新标签页。

> 完整说明：**[可视化流程说明页](docs/TG_ITERM_AI_FLOW.html)**（架构图 + 消息生命周期 + 完成判定） ·
> **[docs/ITERM_MULTI_TAB.md](docs/ITERM_MULTI_TAB.md)**（多 tab 路由指南）。手机发 `/tabs` 可让 Bot 列出当前所有标签页。

### 谁能驱动 Bot：chat-id 白名单（fail-closed）

会话控制命令会直接键入到你的实时终端，因此 relay 内置 **chat-id 白名单**，只放行授权会话：

```bash
# .env
TG_RELAY_ALLOWED_CHAT_IDS=6226809975,123456789   # 逗号/分号分隔；留空则默认仅放行机主 TELEGRAM_CHAT_ID
# TG_RELAY_ALLOW_ALL_CHATS=1                       # 显式放行所有会话（不安全，谨慎）
```

- 白名单与 `TELEGRAM_CHAT_ID` **都为空** → relay **拒绝启动**（fail-closed），避免裸奔。
- 非白名单会话发来的消息一律忽略，不会注入终端。

### 卡住自动兜底 + 截图去重

`iterm-monitor` 回传时还做了两件让手机端更省心的事：

- **交互提示自动默认**：当 Claude Code / Codex 停在一个选择型提示（如 `❯ 1. Yes`）超过
  `TG_ITERM_MONITOR_AUTO_DEFAULT` 秒（默认 60）无人选择时，自动按 Enter 选第一项并回传一条提示，避免会话整夜卡死。设 `0/off` 关闭。
- **截图去重**：截图兜底时对比 32×32 灰度指纹，新帧与上次已发 ≥95% 相似则跳过，避免光标闪烁/重绘刷屏。

## 目录结构

```
mob-remote/                  # 仓库根（原 mobile-agent）
├── mob / mobagent           # 统一 CLI（mobagent 为兼容别名）
├── mob-remote-skill/        # 伞形 Agent Skill
├── tg-notify/               # Telegram 出站通知 (pip)
├── tg-notify-skill/
├── droid-ctl/               # Android 真机控制 (pip)
├── droid-ctl-skill/
├── iphone-ctl/              # iPhone 真机控制 (pip)
├── iphone-ctl-skill/
├── tg-relay/                # Telegram 入站 Bot + 守护进程
├── term-bridge/             # iTerm 注入/捕获/多 tab 路由
├── mob-compose/             # 组合安装、check、截图流水线
├── WebDriverAgent/          # iOS WDA（上游，不改名）
└── scripts/                 # install-skill 等横切脚本
```

文档：**[docs/README.md](docs/README.md)**（依赖 · 安装 · TG 配置）

## Skill 组合（可单独安装）

各 Skill **独立、可自由组合**，不必一次装全：

| 组合 | 安装 | 典型场景 |
|------|------|----------|
| 仅 TG | `--only tg` | CI 构建通知 |
| 仅 Android | `--only adb` | 本地 adb 自动化 |
| 仅 iOS | `--only ios` | 本地 iPhone 自动化 |
| TG + Android | `--only tg,adb` | 远程 Android 验收 |
| TG + iOS | `--only tg,ios` | 远程 iPhone 验收 |
| 双端 | `--only adb,ios` | 同 Mac 控两台设备 |
| 全栈 | 默认 `--all` | TG 收令 + 双端 + Agent |

```bash
# 按需安装 Skill
./mob install-skill --only tg,adb
./mob install-skill --list

# 按需安装 Python 包 + Skill
./mob setup --only ios --with-ios-wda
./mob setup --only tg,adb --test
```

完整组合说明见 **[docs/SKILL_COMPOSE.md](docs/SKILL_COMPOSE.md)**。

## 快速开始

### ★ 一键安装（推荐）

```bash
./oneClickSetup.sh            # 自动 chmod + 准备 .env + ./mob setup + ./mob check
./oneClickSetup.sh --test     # 安装后跑冒烟测试
./oneClickSetup.sh --only tg,adb   # 仅装某些组合，参数透传给 ./mob setup
```

`oneClickSetup.sh` 替你完成 README 里原本要手动做的步骤（赋可执行权限、从 `.env.example` 创建 `.env`），再调用现成的 `./mob setup`。

### 手动分步

```bash
chmod +x mob mobagent mob-compose/compose mob-compose/scripts/*.sh scripts/*.sh tg-relay/*.sh

# ★ Telegram 一键配置（交互式 + 测试消息）
./mob tg-setup --test

# 或完整安装（Python 包 + Skills + 设备环境）
./mob setup --test
./mob install-skill
./mob check
```

详细 Token / Chat ID 说明见 **[docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)**。

## 三种使用方式

### 1. Cursor Agent（推荐）

安装 Skill 后，对 Agent 说：

> 「mobile-agent check，然后 Android 和 iOS 都截图发 Telegram」

Agent 按 `SKILL.md` 中的 vision loop 操作设备并回传结果。

### 2. Telegram Bot 收令

```bash
./mob tg-start
```

| 命令 | 作用 |
|------|------|
| `/new claude\|codex [prompt]` | 新标签页起一个全新 AI 会话（见上） |
| `/tabs` | 列出当前终端标签页 + 路由提示 |
| `/tab [N]` | 选择转发目标终端（无参列出+弹按钮，`/tab 2`=选第 2 个，`/tab 1:1`=指定窗口:标签，`/tab off` 清除）。设为持久默认,后续无前缀消息都进该终端。按 `TG_TERM_BACKEND` 自动枚举 iTerm 或系统 Terminal.app（多窗口各一 tab 时用序号选） |
| `/format html\|markdown\|plain\|screenshot` | 设置回传格式（即时生效，无需重启） |
| `/stop` | 停止当前运行（向目标会话发一次 Esc） |
| `/reset` | 重置当前会话（注入 `/clear`） |
| `/compact` | 压缩会话上下文（注入 `/compact`） |
| `/model opus\|sonnet\|haiku\|fable` | 查看/切换 AI 模型 |
| `/think low\|medium\|high\|xhigh\|max\|auto` | 设置思考强度（等价 `/effort`） |
| `/shot android` | Android 截图 → TG |
| `/shot ios` | iOS 截图 → TG |
| `/tap 540 1200` | 点击（默认 Android） |
| `/tap 200 400 ios` | iOS 点击 |
| `/swipe x1 y1 x2 y2` | 滑动 |
| `/check` | 环境检查 |
| `/devices` | 列出设备 |
| `/help` | 显示可用命令 |
| 自然语言 | 注入当前目标标签页（或写入 `inbox/pending.txt`） |

> **命令菜单 + 子菜单**：Bot 启动时通过 `setMyCommands` 注册上述命令，输入框左侧出现「/」菜单可点选；
> 带候选项的命令（`/new` `/format` `/shot` `/model` `/think`）发裸命令会弹出 **inline 按钮子菜单**，点一下即执行，手机上无需手打参数。`/stop /reset /compact /model /think` 等会话控制命令直接作用于当前目标的 Claude Code / Codex 会话。

查看待办：`./mob tg-inbox`

### 3. 命令行直接操作

```bash
./mob shot-android -c "验收"
./mob ios-start
./mob shot-ios -c "验收"
adbkit tap 540 1200
ioskit tap 540 1200
```

## 配置

所有配置均在 **mobile-agent 包内**：

| 文件 | 用途 |
|------|------|
| `.env` | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、设备序列号等 |
| `mob-compose/compose.env` | iOS WDA（从 `devkit.env.example` 复制） |

也可通过环境变量 `TGKIT_ENV_FILE` 指定外部 `.env` 路径。

## iOS 注意

首次需在 Xcode 中 Run 一次 `WebDriverAgentRunner`（选 Team、信任证书）。之后每天：

```bash
./mob ios-start
```

## 依赖

- macOS（iOS 截图 / WDA / tg-notify 窗口截图）
- Python 3.10+
- `brew install libimobiledevice`（iOS USB）
- `pip install python-telegram-bot`（仅 `tg-start` 需要）

MIT

## 其他语言

- [文档索引](docs/README.md)
- [独立 Git 仓库说明](docs/GIT.md)
- [依赖说明（中/En/日）](docs/DEPENDENCIES.md)
- [安装指南（中/En/日）](docs/INSTALL.md)
- [Telegram 配置（中/En/日）](docs/TELEGRAM_SETUP.md)
- [日本語](README.ja.md)
- [English SKILL](SKILL.md) · [简体中文 SKILL](mob-remote-skill/SKILL.zh-CN.md) · [日本語 SKILL](mob-remote-skill/SKILL.ja.md)
