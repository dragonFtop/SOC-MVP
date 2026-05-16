#!/bin/bash
# AI-SOC 测试告警注入 — 向 alerts.json 直接注入模拟告警，触发研判链路
#
# 与 trigger_syslog.sh 区别:
#   trigger_test.sh    → 直接注入 alerts.json (快速，不需要真实系统交互)
#   trigger_syslog.sh  → 触发真实系统日志 (SSH/sudo/scan)
#
# 用法:
#   bash scripts/trigger_test.sh [数量] [规则ID]
#   bash scripts/trigger_test.sh              # 默认 5 条 5760 (SSH auth failure)
#   bash scripts/trigger_test.sh 3 5503       # 3 条 PAM login failed
#   bash scripts/trigger_test.sh 5 5710       # 5 条 invalid user scan
#   bash scripts/trigger_test.sh 2 random     # 随机规则

COUNT="${1:-5}"
RULE="${2:-5760}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALERTS_FILE="$SCRIPT_DIR/../wazuh_logs/alerts/alerts.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "================================================"
echo "  AI-SOC 测试告警注入"
echo "  数量: $COUNT | 规则ID: $RULE"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# 获取注入前告警数
BEFORE=$(wc -l < "$ALERTS_FILE" 2>/dev/null || echo 0)

python3 -c "
import json, random, uuid, os
from datetime import datetime, timezone

ALERTS_FILE = '$ALERTS_FILE'
COUNT = $COUNT
RULE = '$RULE'

RULES = {
    '5503': {'level': 5,  'desc': 'PAM: User login failed.',                    'groups': ['pam','syslog','authentication_failed'], 'log_tpl': '{ts} {host} sshd[{pid}]: pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost={src_ip}  user={fake_user}'},
    '5760': {'level': 5,  'desc': 'sshd: authentication failed.',               'groups': ['syslog','sshd','authentication_failed'], 'log_tpl': '{ts} {host} sshd[{pid}]: Failed password for {fake_user} from {src_ip} port {sport} ssh2'},
    '5710': {'level': 10, 'desc': 'sshd: Attempt to login using a non-existent user.', 'groups': ['syslog','sshd','authentication_failed','recon'], 'log_tpl': '{ts} {host} sshd[{pid}]: Failed password for invalid user {fake_user} from {src_ip} port {sport} ssh2'},
    '5712': {'level': 8,  'desc': 'sshd: scan detected.',                       'groups': ['syslog','sshd','recon'], 'log_tpl': '{ts} {host} sshd[{pid}]: Did not receive identification string from {src_ip} port {sport}'},
    '5716': {'level': 5,  'desc': 'sshd: authentication failure (connection closed).', 'groups': ['syslog','sshd','authentication_failed'], 'log_tpl': '{ts} {host} sshd[{pid}]: Connection closed by authenticating user {fake_user} {src_ip} port {sport} [preauth]'},
    '2501': {'level': 5,  'desc': 'syslog: User authentication failure.',        'groups': ['syslog','access_control','authentication_failed'], 'log_tpl': '{ts} {host} sshd[{pid}]: error: PAM: Authentication failure for {fake_user} from {src_ip}'},
    '5402': {'level': 3,  'desc': 'sudo: Successful sudo to ROOT executed.',     'groups': ['syslog','sudo','privilege_escalation'], 'log_tpl': '{ts} {host} sudo[{pid}]: root : TTY=pts/0 ; PWD=/root ; USER=root ; COMMAND=/bin/bash'},
    '5403': {'level': 5,  'desc': 'sudo: Unsuccessful sudo attempt.',            'groups': ['syslog','sudo','authentication_failed'], 'log_tpl': '{ts} {host} sudo[{pid}]: pam_unix(sudo:auth): authentication failure; logname=uid=0 euid=0 tty=/dev/pts/0 ruser= rhost=  user={fake_user}'},
}

if RULE == 'random':
    rule_id = random.choice(list(RULES.keys()))
else:
    rule_id = RULE

rule = RULES.get(rule_id, RULES['5760'])

now = datetime.now(timezone.utc)
hostname = '37vwmu3rudbyc0v'
fake_users = ['admin', 'test', 'oracle', 'postgres', 'deploy', 'guest', 'root']

count = 0
with open(ALERTS_FILE, 'a') as f:
    for i in range(COUNT):
        ts = now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond:06d}+00:00'
        pid = random.randint(30000, 99999)
        fake_user = random.choice(fake_users)
        src_ip = f'10.0.0.{random.randint(1,254)}'
        sport = random.randint(40000, 65000)
        mid = f'{now.timestamp():.0f}.{random.randint(100000,999999)}'

        log_line = rule['log_tpl'].format(
            ts=datetime.now().strftime('%b %d %H:%M:%S'),
            host=hostname,
            pid=pid,
            fake_user=fake_user,
            src_ip=src_ip,
            sport=sport,
        )

        alert = {
            'timestamp': ts,
            'rule': {
                'level': rule['level'],
                'description': rule['desc'],
                'id': rule_id,
                'firedtimes': i + 1,
                'mail': False,
                'groups': rule['groups'],
            },
            'agent': {
                'id': '001',
                'name': hostname,
                'ip': src_ip,
            },
            'manager': {'name': 'f5e8a290159d'},
            'id': f'test-{uuid.uuid4().hex[:12]}',
            'full_log': log_line,
            'predecoder': {
                'program_name': 'sshd',
                'timestamp': datetime.now().strftime('%b %d %H:%M:%S'),
                'hostname': hostname,
            },
            'decoder': {'name': 'sshd'},
            'location': '/var/log/auth.log',
            '_test': True,
        }

        f.write(json.dumps(alert, ensure_ascii=False) + '\n')
        count += 1
        print(f'  ✅ [{rule_id}] Lv{rule[\"level\"]} | {rule[\"desc\"][:55]} | src={src_ip}')

print(f'')
print(f'📊 注入 {count} 条告警')
print(f'')
print(f'⏳ 若 Client 已启动，SignalWatcher 将在 2s 内检测到新告警')
print(f'   观察 Client 和 Server 终端窗口的实时输出')
"

AFTER=$(wc -l < "$ALERTS_FILE" 2>/dev/null || echo 0)
NEW=$((AFTER - BEFORE))

echo ""
echo "================================================"
echo -e "  ${GREEN}✅ ${NEW} 条测试告警已注入${NC}"
echo "================================================"
echo ""
echo "  下一步:"
echo "    - 如果 server/client 在运行: 观察终端输出"
echo "    - 如果没运行:"
echo "      Terminal 1: bash scripts/run_server.sh"
echo "      Terminal 2: bash scripts/run_client.sh"
echo "    - 然后: 访问 http://localhost:8501 查看 Dashboard"
echo "    - 或者: bash scripts/trigger_syslog.sh ssh 触发真实系统日志"
