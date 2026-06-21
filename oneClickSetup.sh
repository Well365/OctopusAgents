#!/usr/bin/env bash
# oneClickSetup.sh — mob-remote 一键安装入口
#
# 做三件 README 里需要手动完成的事，然后调用现成的 ./mob setup：
#   1. 给所有 CLI 入口和脚本加可执行权限
#   2. 若缺少 .env，则从 .env.example 复制一份
#   3. 运行 ./mob setup（透传所有参数）并 ./mob check
#
# 用法:
#   ./oneClickSetup.sh                      # 全栈安装（交互式）
#   ./oneClickSetup.sh --only tg,adb        # 仅 Telegram + Android
#   ./oneClickSetup.sh --only ios --with-ios-wda
#   ./oneClickSetup.sh --test               # 安装后跑冒烟测试
#   ./oneClickSetup.sh --skip-check         # 跳过结尾的 ./mob check
#   所有未识别参数都会原样透传给 ./mob setup（见 ./mob setup --help）

# ── 确保以真正的 bash 运行 ──
# 本脚本依赖 bash 特性（数组、进程替换、[[ ]] 等）。若被 sh/dash 调用，
# 或被 bash 的 POSIX 模式（即以 `sh oneClickSetup.sh` 形式）调用，
# 自动用真正的 bash 重新执行，避免 "syntax error near unexpected token" 等问题。
case "${BASH_VERSION:-}:${SHELLOPTS:-}" in
  :*)        exec bash "$0" "$@" ;;   # 无 BASH_VERSION → 非 bash（sh/dash）
  *:*posix*) exec bash "$0" "$@" ;;   # bash 处于 POSIX 模式（被当作 sh 调用）
esac

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ── 输出辅助 ──
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi
step() { echo "${BOLD}${GREEN}▸ $*${RESET}"; }
info() { echo "  ${DIM}$*${RESET}"; }
warn() { echo "${YELLOW}⚠ $*${RESET}" >&2; }
die()  { echo "${RED}✗ $*${RESET}" >&2; exit 1; }

# ── 参数：抽出本脚本自有选项，其余透传给 ./mob setup ──
SKIP_CHECK=0
SETUP_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-check) SKIP_CHECK=1 ;;
    -h|--help)
      # 打印顶部注释块（从第 2 行起，遇到第一行非注释即停）
      awk 'NR>1 && /^#/{sub(/^# ?/,"");print;next} NR>1{exit}' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) SETUP_ARGS+=("$arg") ;;
  esac
done

echo "${BOLD}mob-remote · oneClickSetup${RESET}"
echo "${DIM}root: $ROOT${RESET}"
echo

# ── 0. 前置检查 ──
step "0/4 检查前置依赖"
[[ "$(uname -s)" == "Darwin" ]] || warn "非 macOS：iOS 截图 / WDA / iTerm 注入等功能不可用"
command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装 Python ≥3.9（brew install python）"
command -v git     >/dev/null 2>&1 || warn "未找到 git（克隆 WDA 等步骤可能受影响）"
info "python3: $(python3 --version 2>&1)"

# ── 1. 赋予可执行权限 ──
step "1/4 赋予脚本可执行权限"
chmod +x \
  "$ROOT/mob" "$ROOT/mobagent" \
  "$ROOT/mob-compose/compose" \
  2>/dev/null || true
# 目录下的所有 .sh 与 setup-telegram.sh
while IFS= read -r -d '' f; do chmod +x "$f"; done < <(
  find "$ROOT/scripts" "$ROOT/mob-compose/scripts" "$ROOT/tg-relay" \
       -maxdepth 1 -name '*.sh' -type f -print0 2>/dev/null
)
info "已处理 mob / mobagent / compose 及 scripts、mob-compose、tg-relay 下的 *.sh"

# ── 2. 准备 .env ──
step "2/4 准备 .env 配置"
if [[ -f "$ROOT/.env" ]]; then
  info ".env 已存在，保留不动"
elif [[ -f "$ROOT/.env.example" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  warn "已从 .env.example 创建 .env —— 安装后请填入 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
  info "（或稍后运行 ./mob tg-setup --test 交互式填写）"
else
  warn "未找到 .env.example，跳过（./mob setup 可能会引导你配置）"
fi

# ── 3. 运行安装 ──
step "3/4 运行 ./mob setup ${SETUP_ARGS[*]:-}"
if [[ ${#SETUP_ARGS[@]} -gt 0 ]]; then
  "$ROOT/mob" setup "${SETUP_ARGS[@]}"
else
  "$ROOT/mob" setup
fi

# ── 4. 健康检查 ──
if [[ "$SKIP_CHECK" -eq 0 ]]; then
  step "4/4 运行 ./mob check"
  "$ROOT/mob" check || warn "check 报告了问题，请按上方提示处理"
else
  step "4/4 已跳过 ./mob check（--skip-check）"
fi

# ── 管道自检（含 macOS 自动化权限探测）──
# 复用 term-bridge/pipeline_doctor.py：首次安装即让用户知道需要授予 macOS
# 自动化权限，否则第一条手机消息会静默失败。仅 WARN，绝不中断安装
# （本脚本启用了 set -euo pipefail，故所有可能失败的命令都用 || true 兜底）。
DOCTOR="$ROOT/term-bridge/pipeline_doctor.py"
if [[ -f "$DOCTOR" ]]; then
  step "管道自检（pipeline doctor）"
  if python3 "$DOCTOR"; then
    info "管道自检通过 ✓"
  else
    warn "管道自检发现问题（见上）：常见为未授予 macOS 自动化权限 —— 请在「系统设置 ▸ 隐私与安全性 ▸ 自动化」中允许，再发第一条 Telegram 消息"
  fi || true
else
  warn "未找到 pipeline doctor（$DOCTOR），跳过管道自检"
fi

echo
echo "${BOLD}${GREEN}✓ 安装完成${RESET}"
cat <<EOF

${BOLD}下一步：${RESET}
  ${DIM}# 1. 配置 Telegram（若尚未填写 .env）${RESET}
  ./mob tg-setup --test

  ${DIM}# 2. 远程驱动 Claude Code / Codex（可选，一次性）${RESET}
  ./mob iterm-buffer-setup      # 增大 iTerm 滚动缓冲
  ./mob iterm-list              # 查看 tab 序号
  ./mob up                      # 启动 tg-relay + iterm-monitor

文档: docs/INSTALL.md · docs/TELEGRAM_SETUP.md · docs/TG_ITERM_AI_FLOW.html
EOF
