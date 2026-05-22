#!/bin/bash
# AI-SOC Auth.log 触发器 — 模拟攻击行为触发本地检测
#
# 直接向 /var/log/auth.log 写入系统事件，被 DetectionEngine 检测。
# 无需 Wazuh Agent/Manager。
#
# 用法:
#   bash scripts/trigger_authlog.sh ssh [次数]      SSH 失败登录 (默认5次)
#   bash scripts/trigger_authlog.sh sudo [次数]      错误 sudo 尝试
#   bash scripts/trigger_authlog.sh scan [次数]      端口扫描
#   bash scripts/trigger_authlog.sh all [次数]       依次执行以上所有

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-all}"
ATTEMPTS="${2:-5}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "================================================"
echo "  AI-SOC Auth.log 触发器 (本地检测)"
echo "  模式: $MODE | 尝试次数: $ATTEMPTS"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# ---- 前置检查 ----
check_prereqs() {
    local ok=true

    if ! systemctl is-active --quiet ssh 2>/dev/null; then
        echo -e "  ${RED}❌ sshd 未运行 — SSH 爆破模拟将使用 logger 回退${NC}"
    else
        echo -e "  ${GREEN}✅ sshd 已运行${NC}"
    fi

    if [ ! -f /var/log/auth.log ]; then
        echo -e "  ${RED}❌ /var/log/auth.log 不存在${NC}"
        ok=false
    else
        echo -e "  ${GREEN}✅ /var/log/auth.log 存在${NC}"
        if [ -r /var/log/auth.log ]; then
            echo -e "  ${GREEN}✅ auth.log 可读${NC}"
        else
            echo -e "  ${YELLOW}⚠️ auth.log 不可读，尝试修复...${NC}"
            sudo chmod o+r /var/log/auth.log 2>/dev/null || true
        fi
    fi

    echo ""
    if [ "$ok" = false ]; then
        echo "请先确保以上条件满足后再运行本脚本"
        exit 1
    fi
}

# ---- SSH 登录失败 ----
trigger_ssh_bruteforce() {
    echo "🔑 模拟 SSH 登录失败 ($ATTEMPTS 次)..."
    echo ""

    USERS=('root' 'admin' 'deploy' 'oracle' 'postgres' 'test' 'guest' 'ubuntu')

    for i in $(seq 1 "$ATTEMPTS"); do
        idx=$(( (i - 1) % ${#USERS[@]} ))
        user="${USERS[$idx]}"

        if systemctl is-active --quiet ssh 2>/dev/null; then
            # Real SSH attempt with wrong password via PTY
            python3 << PYEOF
import pty, os, time, select
user = '${user}'
pid, fd = pty.fork()
if pid == 0:
    os.execvp('ssh', [
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=5',
        '-o', 'PreferredAuthentications=password',
        '-o', 'NumberOfPasswordPrompts=1',
        '-tt',
        f'{user}@localhost', 'exit'
    ])
    os._exit(1)
else:
    pw_sent = False
    deadline = time.time() + 8
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 2048)
                decoded = chunk.decode('utf-8', errors='replace').lower()
                if not pw_sent and ('assword' in decoded or 'password:' in decoded):
                    os.write(fd, b'wrong_password_123\n')
                    pw_sent = True
                    time.sleep(0.3)
                    os.write(fd, b'\x03')
                    break
            except Exception:
                break
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            break
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except Exception:
        pass
PYEOF
        else
            # Fallback: use logger to write to auth.log
            logger -p auth.warning -t sshd "Failed password for ${user} from 10.0.0.$((RANDOM % 254 + 1)) port $((RANDOM % 60000 + 1024)) ssh2"
        fi

        echo "  [$i/$ATTEMPTS] ${user}@localhost 尝试登录 (失败)"
        sleep 0.8
    done

    echo ""
    echo "  ✅ SSH 失败模拟完成"
    echo ""

    echo "  最新 auth.log (SSH 失败):"
    sudo grep -E 'sshd.*(Failed password|Invalid user)' /var/log/auth.log 2>/dev/null | tail -"$ATTEMPTS" | sed 's/^/     /' || true
    echo ""

    echo "⏳ DetectionEngine 将在数秒内检测到并发布信号"
    echo "   观察 Client 和 Server 终端窗口的实时输出"
}

# ---- Sudo 失败 ----
trigger_sudo_failure() {
    echo "🔐 模拟错误 sudo 尝试 ($ATTEMPTS 次)..."
    echo ""

    for i in $(seq 1 "$ATTEMPTS"); do
        sudo -u nobody sudo -k 2>/dev/null || true
        echo "  [$i/$ATTEMPTS] sudo 失败已写入 auth.log"
    done

    echo ""
    echo "  最新 auth.log (sudo 失败):"
    sudo grep 'sudo.*authentication failure' /var/log/auth.log 2>/dev/null | tail -"$ATTEMPTS" | sed 's/^/     /' || true
    echo ""
    echo "⏳ DetectionEngine 将在数秒内检测到并发布信号"
}

# ---- 端口扫描 ----
trigger_port_scan() {
    echo "🔍 模拟端口扫描..."
    echo ""

    PORTS=(22 80 443 3306 5432 6379 8080 8443 9090 27017)

    for port in "${PORTS[@]}"; do
        echo -n "  localhost:$port — "
        if command -v nc &>/dev/null; then
            nc -zv -w1 localhost "$port" 2>&1 || true
        else
            timeout 1 bash -c "echo >/dev/tcp/localhost/$port" 2>/dev/null && echo "open" || echo "closed"
        fi
    done

    echo ""
    echo "  ✅ 端口扫描完成"
    echo "  ⏳ 若 sshd 未运行，扫描仅产生连接拒绝；若 sshd 运行则写入 auth.log"
}

# ---- 全部测试 ----
trigger_all() {
    echo "🚀 运行完整端到端测试..."
    echo ""
    trigger_ssh_bruteforce
    echo ""
    echo "================================================"
    echo ""
    trigger_sudo_failure
}

# ---- 主流程 ----
check_prereqs

case $MODE in
    ssh|bruteforce)
        trigger_ssh_bruteforce
        ;;
    sudo)
        trigger_sudo_failure
        ;;
    scan)
        trigger_port_scan
        ;;
    all)
        trigger_all
        ;;
    *)
        echo "用法: bash scripts/trigger_authlog.sh [ssh|sudo|scan|all] [次数]"
        echo ""
        echo "  ssh   - 模拟 SSH 暴力破解"
        echo "  sudo  - 模拟错误 sudo 尝试"
        echo "  scan  - 模拟端口扫描"
        echo "  all   - 依次执行以上所有 (默认)"
        exit 1
        ;;
esac

echo ""
echo "================================================"
echo "  ✅ 系统操作已完成"
echo "================================================"
echo ""
echo "  日志已写入 /var/log/auth.log"
echo "  观察 Client 和 Server 终端窗口的实时输出"
echo ""
echo "  验证: sudo tail -20 /var/log/auth.log"
