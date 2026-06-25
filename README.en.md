# mob-remote

[简体中文](README.md) | **English** | [日本語](README.ja.md)

---

**Telegram remote + Android (droid-ctl) + iOS (iphone-ctl/WDA)** — a self-contained bundle.

Clone, distribute, and run it on its own; it depends on no external business project.

> **Telegram setup** → [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) (ZH/EN/JA)
> **Remote-drive Claude Code / Codex** → [visual guide](docs/TG_ITERM_AI_FLOW.html) · [docs/ITERM_MULTI_TAB.md](docs/ITERM_MULTI_TAB.md)
> **Dependencies & install** → [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) · [docs/INSTALL.md](docs/INSTALL.md)

## Remote-drive Claude Code / Codex (Terminal/iTerm inject + relay-back)

Turn Telegram into a "remote control for your AI coding assistant": send a message from your
phone → it is automatically injected into a terminal tab on your Mac that is running
**Claude Code / Codex** → the AI executes → the assistant's reply is relayed back to Telegram.
Even away from your computer, you can drive multiple projects' AI sessions remotely.

> **Pluggable terminal backend**: `TG_TERM_BACKEND` in `.env` selects the inject/capture backend.
> Default is `terminal` (the built-in Terminal.app); set it to `iterm` to use iTerm2. Inject,
> screenshot, and relay scripts each have two implementations, switched uniformly by `term_backend.py`.

![Telegram → iTerm2 Claude Code/Codex flow](docs/tg-iterm-flow.svg)

<details>
<summary>Plain-text flow diagram</summary>

```
📱 You send  [t3] fix the login bug
        │  tg-relay parses the prefix (iterm_route.py)
        ▼
   iterm-inject  ── AppleScript types + Enter ──►  iTerm2 · tab 3
                                                 └─ Claude Code / Codex runs
                                                       │ output → inbox/iterm-session-*.log
        ┌────────────────────────────────────────────┘
        ▼
   iterm-monitor ── extract assistant reply (iterm_extract.py) ──►  tg-notify send
        │
        ▼
📱 You receive the AI reply (falls back to a screenshot after a long silence)
```

</details>

**The message prefix decides which tab it goes to** (switch freely within one conversation, no config changes needed):

| Syntax | Example | Notes |
|--------|---------|-------|
| `[tN]` / `#N` / `@tN:` | `[t3] list dir` | By tab index, most common |
| `[name]` / `@name:` | `[myapp] run tests` | Fuzzy-match a tab-title directory fragment |
| `[alias]` | `[fz] check deploy` | Exact mapping from `TG_ITERM_ALIASES` in `.env` |
| no prefix | `show git status` | Falls to the default tab in `.env` |

> **Routing priority**: a per-message prefix `[t3]` > the persistent default set via `/tab` > `TG_ITERM_TAB` in `.env`.
> Once you pick a tab with `/tab`, subsequent prefix-less messages — inject and relay-back — follow that tab (including auto-Enter when stuck / idle screenshots).

**Enable** (prerequisite: Bot Token / Chat ID already configured):

```bash
# .env (natural-language → terminal injection is ON BY DEFAULT, no setting needed)
TG_TERM_BACKEND=terminal     # terminal backend: terminal (default, built-in Terminal.app) | iterm
TG_ITERM_MONITOR_AFTER=45    # how long after injecting to start capturing the reply
# TG_RELAY_ITERM_INJECT=0    # set this only to DISABLE injection (default is on)

./mob iterm-buffer-setup     # enlarge scrollback so long replies aren't truncated (once)
./mob iterm-list             # show tab indices / suggested prefixes
./mob up                     # start tg-relay (receive) + iterm-monitor (relay-back) together
```

### Start a new session from one phone message: `/new`

No need to open a terminal on your computer first — send `/new` from your phone to spin up a brand-new AI session in a new tab:

```
/new claude fix the login bug   # new Terminal tab → start claude in ~/fullStar/<timestamp>, with this line as the first prompt
/new codex                      # likewise, start codex (no prompt)
```

It automatically: opens a new tab (creates a window if none) → `mkdir`s a timestamped working
directory → auto-installs the CLI if missing → starts the agent
(`claude --permission-mode bypassPermissions` / `codex`). After the session opens, subsequent
ordinary messages are auto-injected into this new tab.

> Full details: **[visual flow guide](docs/TG_ITERM_AI_FLOW.html)** (architecture diagram + message lifecycle + completion detection) ·
> **[docs/ITERM_MULTI_TAB.md](docs/ITERM_MULTI_TAB.md)** (multi-tab routing guide). Send `/tabs` from your phone to have the Bot list all current tabs.

### Who can drive the Bot: chat-id allowlist (fail-closed)

Session-control commands are typed directly into your live terminal, so the relay has a built-in
**chat-id allowlist** that only admits authorized chats:

```bash
# .env
TG_RELAY_ALLOWED_CHAT_IDS=6226809975,123456789   # comma/semicolon separated; if empty, only the owner's TELEGRAM_CHAT_ID is admitted
```

> **By design there is no "allow all chats" switch**: this bot types messages straight into a live
> terminal running an agent with bypassed permissions, so there is no allow-all escape hatch. To let
> more people drive it, add their chat ids to `TG_RELAY_ALLOWED_CHAT_IDS`.

- If the allowlist **and** `TELEGRAM_CHAT_ID` are **both empty** → the relay **refuses to start** (fail-closed), preventing an exposed instance.
- Messages from non-allowlisted chats are ignored and never injected into the terminal.

> **Approval mode (optional, instead of bypassed permissions)**: send `/approve on` and agents started by
> *subsequent* `/new` will ask for each permission — the prompt arrives in Telegram as buttons you approve/deny.
> `/approve off` restores auto-allow (bypassPermissions). Useful as a human gate when an agent runs risky operations.

### Auto-fallback when stuck + screenshot de-duplication

When relaying back, `iterm-monitor` also does two things to make the phone side smoother:

- **Auto-default on interactive prompts**: when Claude Code / Codex stops at a choice prompt
  (e.g. `❯ 1. Yes`) for more than `TG_ITERM_MONITOR_AUTO_DEFAULT` seconds (default 60) with no
  selection, it auto-presses Enter to pick the first option and relays a notice, avoiding a session
  stuck all night. Set `0/off` to disable.
- **Screenshot de-dup**: on screenshot fallback it compares a 32×32 grayscale fingerprint; if a new
  frame is ≥95% similar to the last one sent, it is skipped — avoiding cursor-blink / redraw spam.

## Directory structure

```
mob-remote/                  # repo root (formerly mobile-agent)
├── mob / mobagent           # unified CLI (mobagent is a compat alias)
├── mob-remote-skill/        # umbrella Agent Skill
├── tg-notify/               # Telegram outbound notifications (pip)
├── tg-notify-skill/
├── droid-ctl/               # Android physical-device control (pip)
├── droid-ctl-skill/
├── iphone-ctl/              # iPhone physical-device control (pip)
├── iphone-ctl-skill/
├── tg-relay/                # Telegram inbound Bot + daemons
├── term-bridge/             # iTerm inject/capture/multi-tab routing
├── mob-compose/             # composable install, check, screenshot pipeline
├── WebDriverAgent/          # iOS WDA (upstream, not renamed)
└── scripts/                 # cross-cutting scripts like install-skill
```

Docs: **[docs/README.md](docs/README.md)** (dependencies · install · TG setup)

## Skill composition (install independently)

Each Skill is **standalone and freely composable** — you don't have to install them all at once:

| Combo | Install | Typical use |
|-------|---------|-------------|
| TG only | `--only tg` | CI build notifications |
| Android only | `--only adb` | Local adb automation |
| iOS only | `--only ios` | Local iPhone automation |
| TG + Android | `--only tg,adb` | Remote Android acceptance |
| TG + iOS | `--only tg,ios` | Remote iPhone acceptance |
| Both devices | `--only adb,ios` | Drive two devices from one Mac |
| Full stack | default `--all` | TG commands + both devices + Agent |

```bash
# install Skills as needed
./mob install-skill --only tg,adb
./mob install-skill --list

# install Python packages + Skills as needed
./mob setup --only ios --with-ios-wda
./mob setup --only tg,adb --test
```

See **[docs/SKILL_COMPOSE.md](docs/SKILL_COMPOSE.md)** for the full composition guide.

## Quick start

### ★ One-click install (recommended)

```bash
./oneClickSetup.sh            # auto chmod + prepare .env + ./mob setup + ./mob check
./oneClickSetup.sh --test     # run a smoke test after install
./oneClickSetup.sh --only tg,adb   # install only some combos; args pass through to ./mob setup
```

`oneClickSetup.sh` does the steps the README would otherwise ask you to do by hand (grant execute
permission, create `.env` from `.env.example`), then calls the existing `./mob setup`.

### ★ Start (recommended)

Once installed, use the friendly launcher to bring up all services in one shot (= `tg-relay`
receive + `iterm-monitor` relay-back, i.e. `./mob up`) and print a status summary:

```bash
./oneClickStart.sh           # start the TG full stack (relay + monitor) + status summary
./oneClickStart.sh --ios     # also start iproxy (needed for iOS WDA)
./oneClickStart.sh --watch   # after starting, watch *.py/*.sh/.env changes and auto-reload (for dev)
./oneClickStart.sh --stop    # stop all services (= ./mob down)
```

### Manual, step by step

```bash
chmod +x mob mobagent mob-compose/compose mob-compose/scripts/*.sh scripts/*.sh tg-relay/*.sh

# ★ Telegram one-click setup (interactive + test message)
./mob tg-setup --test

# or a full install (Python packages + Skills + device environment)
./mob setup --test
./mob install-skill
./mob check
```

See **[docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)** for detailed Token / Chat ID instructions.

## Three ways to use it

### 1. Cursor Agent (recommended)

After installing the Skill, tell the Agent:

> "Run mob check, then screenshot both Android and iOS and send to Telegram"

The Agent operates the devices via the vision loop in `SKILL.md` and relays the results back.

### 2. Telegram Bot commands

```bash
./mob tg-start
```

| Command | Effect |
|---------|--------|
| `/new claude\|codex [prompt]` | Start a brand-new AI session in a new tab (see above) |
| `/tabs` | List current terminal tabs + routing hints |
| `/tab [N]` | Choose the forwarding target terminal (no arg lists + shows buttons; `/tab 2` = pick the 2nd; `/tab 1:1` = specify window:tab; `/tab off` to clear). Set as the persistent default so subsequent prefix-less messages go there. Auto-enumerates iTerm or the built-in Terminal.app per `TG_TERM_BACKEND` (use the index when multiple windows each have one tab) |
| `/status` | Per-terminal agent status |
| `/format html\|markdown\|plain\|screenshot` | Set the relay-back format (takes effect instantly, no restart) |
| `/stop` | Stop the current run (send one Esc to the target session) |
| `/interrupt` | Interrupt the current run (Ctrl-C) |
| `/sel N` | Answer an agent's choice prompt (`/sel 2` picks option 2, or `/sel w:t:n` to target) |
| `/approve on\|off` | Approval mode toggle: when on, the agent's permissions are gated |
| `/reset` | Reset the current session (inject `/clear`) |
| `/compact` | Compact the session context (inject `/compact`) |
| `/model opus\|sonnet\|haiku\|fable` | View/switch the AI model |
| `/think low\|medium\|high\|xhigh\|max\|auto` | Set thinking effort (equivalent to `/effort`) |
| `/shot android\|ios\|mac\|term` | Screenshot: Android / iOS device · Mac screen · terminal → TG |
| `/tap 540 1200` | Tap (Android by default) |
| `/tap 200 400 ios` | iOS tap |
| `/swipe x1 y1 x2 y2` | Swipe |
| `/check` | Environment check |
| `/devices` | List devices |
| `/unlock` | Unlock Android (numeric PIN; set `ANDROID_UNLOCK_PIN` in `.env`; wake→swipe→type PIN→Enter, then screenshots to confirm) |
| `/lockmac on\|off\|boot` | Privacy veil: black out the screen but **not a lock** (remote ops/screenshots unaffected). Dismiss with the local password or `/lockmac off`; `boot on\|off` sets login default. Run `./mob veil setup` first |
| `/p [name]` | Quick-prompt library: `/p name` injects that prompt (no arg lists available prompts) |
| `/diff [path]` | Show git changes (optional path) |
| `/help` | Show available commands |
| natural language | Inject into the current target tab (or write to `inbox/pending.txt`) |

> **Command menu + submenus**: on startup the Bot registers the commands above via `setMyCommands`,
> so a "/" menu appears to the left of the input box to pick from; commands with options
> (`/new` `/format` `/shot` `/model` `/think`) pop up an **inline-button submenu** when sent bare —
> tap once to execute, no typing arguments on your phone. Session-control commands like
> `/stop /reset /compact /model /think` act directly on the current target's Claude Code / Codex session.

View the queue: `./mob tg-inbox`

### 3. Direct command line

```bash
./mob shot-android -c "acceptance"
./mob ios-start
./mob shot-ios -c "acceptance"
adbkit tap 540 1200
ioskit tap 540 1200
```

## Configuration

All configuration lives **inside the project root (fullStar)**:

| File | Purpose |
|------|---------|
| `.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, device serials, etc. |
| `mob-compose/compose.env` | iOS WDA (copy from `devkit.env.example`) |

You can also point to an external `.env` path via the `TGKIT_ENV_FILE` environment variable.

## iOS notes

The first time, Run `WebDriverAgentRunner` once in Xcode (select Team, trust the certificate). After that, daily:

```bash
./mob ios-start
```

## Dependencies

- macOS (iOS screenshots / WDA / tg-notify window capture)
- Python 3.10+
- `brew install libimobiledevice` (iOS USB)
- `pip install python-telegram-bot` (only `tg-start` needs it)

MIT

## Other languages

- [Documentation index](docs/README.md)
- [Standalone Git repo notes](docs/GIT.md)
- [Dependencies (ZH/EN/JA)](docs/DEPENDENCIES.md)
- [Install guide (ZH/EN/JA)](docs/INSTALL.md)
- [Telegram setup (ZH/EN/JA)](docs/TELEGRAM_SETUP.md)
- [日本語](README.ja.md)
- [English SKILL](mob-remote-skill/SKILL.md) · [简体中文 SKILL](mob-remote-skill/SKILL.zh-CN.md) · [日本語 SKILL](mob-remote-skill/SKILL.ja.md)
