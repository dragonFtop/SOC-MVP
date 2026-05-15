#!/bin/bash
# AI-SOC 端到端触发器
# 在系统中执行真实操作 → 产生 auth.log 日志 → Wazuh Agent 捕获 → 触发研判链路
#
# 用法:
#   bash scripts/trigger_syslog.sh ssh [次数]       SSH 暴力破解 (Python PTY 发送错误密码)
#   bash scripts/trigger_syslog.sh scan             端口扫描
#   bash scripts/trigger_syslog.sh all [次数]       以上全部

set -e

MODE="${1:-all}"
ATTEMPTS="${2:-3}"

echo "================================================"
echo "  AI-SOC 端到端触发器 (系统日志级别)"
echo "  模式: $MODE | 尝试次数: $ATTEMPTS"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""
echo "数据流:"
echo "  系统操作 → /var/log/auth.log → Wazuh Agent → Manager"
echo "     → alerts.json (追加新行) → SignalWatcher (2s内检测)"
echo "     → 信号 → NATS → Server → 查询 → 证据 → 研判"
echo ""

# ---- SSH 暴力破解 (Python PTY 自动发送错误密码) ----
trigger_ssh_bruteforce() {
    echo "🔑 模拟 SSH 暴力破解 ($ATTEMPTS 次)..."
    echo ""

    python3 << PYEOF
import pty, os, time, select

users  = ['root', 'admin', 'deploy', 'oracle', 'postgres', 'test', 'guest', 'ubuntu']
passwords = ['admin123', 'Password1!', '12345678', 'toortoor', 'qwerty123', 'letmein']
attempts = min($ATTEMPTS, len(users))

for i in range(attempts):
    user  = users[i]
    pw    = passwords[i]
    print(f"  [{i+1}/{attempts}] {user}@localhost:{pw}")

    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('ssh', [
            'ssh', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=3',
            '-o', 'PreferredAuthentications=password',
            '-tt',
            f'{user}@localhost', 'exit'
        ])
    else:
        pw_sent = False
        deadline = time.time() + 4
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.3)
            if r:
                try:
                    chunk = os.read(fd, 1024)
                    if not pw_sent and b'assword' in chunk:
                        os.write(fd, f'{pw}\n'.encode())
                        pw_sent = True
                        time.sleep(0.2)
                        os.write(fd, b'\x03')
                        break
                except Exception:
                    break
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                break
        os.close(fd)
        try:    os.waitpid(pid, 0)
        except: pass

print('')
print('  ✅ SSH 登录失败模拟完成')
print('  预期 Wazuh 规则: 5503 (PAM login failed) / 5710 (sshd invalid user) / 2501 (auth failure)')
PYEOF

    echo ""
    echo "  最新 auth.log 记录:"
    sudo grep 'sshd.*Failed password\|pam_unix(sshd:auth).*authentication failure' /var/log/auth.log | tail -$ATTEMPTS || true
}

# ---- 端口扫描 ----
trigger_port_scan() {
    echo "🔍 模拟端口扫描..."
    echo ""

    PORTS=(22 80 443 3306 5432 6379 8080 8443 9090 27017)

    # nc 快速扫描
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
    echo "  预期 Wazuh 规则: 5710 (scanning) / 5712 (reconnaissance)"
}

# ---- 全部测试 ----
trigger_all() {
    trigger_ssh_bruteforce
    echo ""
    trigger_port_scan
}

case $MODE in
    ssh|bruteforce)
        trigger_ssh_bruteforce
        ;;
    scan)
        trigger_port_scan
        ;;
    all)
        trigger_all
        ;;
    *)
        echo "用法: bash scripts/trigger_syslog.sh [ssh|scan|all] [次数]"
        echo ""
        echo "  ssh   - 模拟 SSH 暴力破解 (Python PTY 发送错误密码到 sshd)"
        echo "  scan  - 模拟端口扫描 (nc)"
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
echo "  Wazuh Agent 将检测到这些事件 → alerts.json → SignalWatcher → ..."
echo ""
echo "  观察 Client 和 Server 终端，几秒内应有新信号和查询结果"
