#!/bin/bash
# AI-SOC 端到端触发器 — 触发真实系统日志 → Wazuh → 研判链路
#
# 用法:
#   bash scripts/trigger_syslog.sh ssh [次数]       SSH 登录失败 (默认3次)
#   bash scripts/trigger_syslog.sh scan             端口扫描
#   bash scripts/trigger_syslog.sh sudo             错误 sudo 尝试
#   bash scripts/trigger_syslog.sh all [次数]       以上全部
#
# 前提: Wazuh Agent 已配置监控 /var/log/auth.log
#   sudo grep auth.log /var/ossec/etc/ossec.conf

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALERTS_FILE="$SCRIPT_DIR/../wazuh_logs/alerts/alerts.json"

MODE="${1:-all}"
ATTEMPTS="${2:-5}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "================================================"
echo "  AI-SOC 端到端触发器 (系统日志级别)"
echo "  模式: $MODE | 尝试次数: $ATTEMPTS"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# ---- 前置检查 ----
check_prereqs() {
    local ok=true

    if ! systemctl is-active --quiet ssh 2>/dev/null; then
        echo -e "  ${RED}❌ sshd 未运行${NC}"
        ok=false
    else
        echo -e "  ${GREEN}✅ sshd 已运行${NC}"
    fi

    if ! systemctl is-active --quiet wazuh-agent 2>/dev/null; then
        echo -e "  ${RED}❌ wazuh-agent 未运行${NC}"
        ok=false
    else
        echo -e "  ${GREEN}✅ wazuh-agent 已运行${NC}"
    fi

    if ! sudo grep -q 'auth.log' /var/ossec/etc/ossec.conf 2>/dev/null; then
        echo -e "  ${RED}❌ Wazuh 未监控 /var/log/auth.log — 请先修复 ossec.conf${NC}"
        ok=false
    else
        echo -e "  ${GREEN}✅ Wazuh 已监控 /var/log/auth.log${NC}"
    fi

    if [ "$ok" = false ]; then
        echo ""
        echo "请先确保以上条件满足后再运行本脚本"
        exit 1
    fi
    echo ""
}

# ---- 记录触发前的告警数量 ----
get_alert_count() {
    if [ -f "$ALERTS_FILE" ] && [ -r "$ALERTS_FILE" ]; then
        wc -l < "$ALERTS_FILE"
    else
        echo 0
    fi
}

# ---- 验证是否产生了新告警 ----
verify_alerts() {
    local before=$1
    local label=$2
    local expected_rules=$3

    sleep 6  # 给 Wazuh 一些时间处理

    local after
    after=$(get_alert_count)
    local new=$((after - before))

    echo ""
    echo "  验证结果:"
    if [ "$new" -gt 0 ]; then
        echo -e "  ${GREEN}✅ 产生了 ${new} 条新告警${NC}"
        python3 -c "
import json
with open('$ALERTS_FILE') as f:
    alerts = [json.loads(l) for l in f if l.strip()]
for a in alerts[-${new}:]:
    ts = a.get('timestamp','?')
    rule = a.get('rule',{})
    loc = a.get('location','?')
    print(f'     [{ts}] rule={rule.get(\"id\",\"?\")} lv={rule.get(\"level\",\"?\")} loc={loc} desc={rule.get(\"description\",\"?\")[:50]}')
" 2>/dev/null
    else
        echo -e "  ${YELLOW}⚠️ 未检测到新告警 — 可能需等待更长时间 (Wazuh 扫描间隔 360s)${NC}"
    fi
}

# ---- SSH 登录失败 (真实 PTY 模拟) ----
trigger_ssh_bruteforce() {
    local before=$(get_alert_count)

    echo "🔑 模拟 SSH 登录失败 ($ATTEMPTS 次)..."
    echo ""

    python3 << PYEOF
import pty, os, time, select

users     = ['root', 'admin', 'deploy', 'oracle', 'postgres', 'test', 'guest', 'ubuntu']
passwords = ['admin123', 'Password1!', '12345678', 'toortoor', 'qwerty123', 'letmein', 'password', '123456']
attempts  = min($ATTEMPTS, len(users))

for i in range(attempts):
    user = users[i]
    pw   = passwords[i]
    print(f"  [{i+1}/{attempts}] {user}@localhost 尝试登录...")

    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('ssh', [
            'ssh',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=5',
            '-o', 'PreferredAuthentications=password',
            '-o', 'PasswordAuthentication=yes',
            '-o', 'NumberOfPasswordPrompts=1',
            '-tt',
            f'{user}@localhost', 'exit'
        ])
        os._exit(1)
    else:
        pw_sent = False
        deadline = time.time() + 8  # generous timeout
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(fd, 2048)
                    decoded = chunk.decode('utf-8', errors='replace').lower()
                    if not pw_sent and ('assword' in decoded or 'password:' in decoded):
                        os.write(fd, f'{pw}\n'.encode())
                        pw_sent = True
                        time.sleep(0.3)
                        # Send Ctrl+C to end the session
                        os.write(fd, b'\x03')
                        break
                    elif b'ermission denied' in chunk or b'ermission denied' in chunk.encode():
                        break
                except Exception:
                    break
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                break

        # Cleanup
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except Exception:
            pass

    # Small delay between attempts
    if i < attempts - 1:
        time.sleep(0.5)

print('')
print('  ✅ SSH 登录失败模拟完成')
PYEOF

    echo ""
    echo "  最新 auth.log (SSH 失败):"
    sudo grep -E 'sshd.*(Failed password|Invalid user|authentication failure)' /var/log/auth.log 2>/dev/null | tail -"$ATTEMPTS" || true
    echo ""

    verify_alerts "$before" "SSH" "5503|5710|5760|2501"
}

# ---- 端口扫描 ----
trigger_port_scan() {
    local before=$(get_alert_count)

    echo "🔍 模拟端口扫描..."
    echo ""

    PORTS=(22 80 443 3306 5432 6379 8080 8443 9090 27017)

    if command -v nc &>/dev/null; then
        for port in "${PORTS[@]}"; do
            echo -n "  nc -zv localhost $port: "
            nc -zv -w1 localhost "$port" 2>&1 || true
        done
    else
        for port in "${PORTS[@]}"; do
            echo -n "  port $port: "
            timeout 1 bash -c "echo >/dev/tcp/localhost/$port" 2>/dev/null && echo "open" || echo "closed/refused"
        done
    fi

    echo ""
    echo "  ✅ 端口扫描完成"
    verify_alerts "$before" "Scan" "5710|5712"
}

# ---- 错误 sudo ----
trigger_sudo_failure() {
    local before=$(get_alert_count)

    echo "🔐 模拟错误 sudo 尝试 ($ATTEMPTS 次)..."
    echo ""

    for i in $(seq 1 "$ATTEMPTS"); do
        # 用不存在的用户执行 sudo 会记录失败
        sudo -u nobody sudo -k 2>/dev/null || true
        echo "  [$i/$ATTEMPTS] sudo 失败已写入 auth.log"
    done

    echo ""
    verify_alerts "$before" "sudo" "5401|5402|5403"
}

# ---- 全部测试 ----
trigger_all() {
    echo "🚀 运行完整端到端测试..."
    echo ""
    trigger_ssh_bruteforce
    echo ""
    echo "================================================"
    echo ""
    trigger_port_scan
}

# ---- 主流程 ----
check_prereqs

case $MODE in
    ssh|bruteforce)
        trigger_ssh_bruteforce
        ;;
    scan)
        trigger_port_scan
        ;;
    sudo)
        trigger_sudo_failure
        ;;
    all)
        trigger_all
        ;;
    *)
        echo "用法: bash scripts/trigger_syslog.sh [ssh|scan|sudo|all] [次数]"
        echo ""
        echo "  ssh   - 模拟 SSH 暴力破解 (PTY 发送错误密码到 sshd)"
        echo "  scan  - 模拟端口扫描 (nc)"
        echo "  sudo  - 模拟错误 sudo 尝试"
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
echo "  查看告警: tail -5 $ALERTS_FILE | python3 -m json.tool"
