#!/bin/bash
# AI-SOC 测试触发器 — 向 alerts.json 注入模拟告警，触发完整研判链路
# 用法: bash scripts/trigger_test.sh [数量] [规则ID]
#   bash scripts/trigger_test.sh              # 默认注入 5 条 SSH 暴力破解告警
#   bash scripts/trigger_test.sh 3 5710       # 注入 3 条端口扫描告警
#   bash scripts/trigger_test.sh 2 random     # 随机规则

COUNT="${1:-5}"
RULE="${2:-5503}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALERTS_FILE="$SCRIPT_DIR/../wazuh_logs/alerts/alerts.json"

echo "================================================"
echo "  AI-SOC 测试告警注入"
echo "  数量: $COUNT | 规则ID: $RULE"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

python3 -c "
import json, random, uuid, os
from datetime import datetime, timezone

ALERTS_FILE = '$ALERTS_FILE'
COUNT = $COUNT
RULE = '$RULE'

# 规则库
RULES = {
    '5503': {'level': 5,  'desc': 'PAM: User login failed.',                    'groups': ['pam','syslog','authentication_failed'], 'log_tpl': '{ts} {host} login[{pid}]: pam_unix(login:auth): authentication failure; logname=LOGIN uid=0 euid=0 tty=/dev/tty1 ruser= rhost='},
    '2501': {'level': 5,  'desc': 'syslog: User authentication failure.',        'groups': ['syslog','access_control','authentication_failed'], 'log_tpl': '{ts} {host} login[{pid}]: FAILED LOGIN (1) on \"/dev/tty1\" FOR \"UNKNOWN\", Authentication failure'},
    '5710': {'level': 10, 'desc': 'sshd: Attempt to login using a non-existent user.', 'groups': ['syslog','sshd','authentication_failed','recon'], 'log_tpl': '{ts} {host} sshd[{pid}]: Failed password for invalid user {fake_user} from {src_ip} port {sport} ssh2'},
    '5501': {'level': 3,  'desc': 'PAM: Login session opened.',                  'groups': ['pam','syslog','access_control'], 'log_tpl': '{ts} {host} login[{pid}]: pam_unix(login:session): session opened for user root by LOGIN(uid=0)'},
    '5502': {'level': 3,  'desc': 'PAM: Login session closed.',                  'groups': ['pam','syslog','access_control'], 'log_tpl': '{ts} {host} login[{pid}]: pam_unix(login:session): session closed for user root'},
    '5712': {'level': 8,  'desc': 'sshd: scan detected.',                        'groups': ['syslog','sshd','recon'], 'log_tpl': '{ts} {host} sshd[{pid}]: Did not receive identification string from {src_ip} port {sport}'},
    '5763': {'level': 12, 'desc': 'sudo: successful to ROOT.',                   'groups': ['syslog','sudo','privilege_escalation'], 'log_tpl': '{ts} {host} sudo[{pid}]: root : TTY=tty1 ; PWD=/root ; USER=root ; COMMAND=/bin/bash'},
    '5715': {'level': 10, 'desc': 'sshd: Accepted publickey for root.',          'groups': ['syslog','sshd','authentication_success'], 'log_tpl': '{ts} {host} sshd[{pid}]: Accepted publickey for root from {src_ip} port {sport} ssh2'},
}

# 如果指定了 random，随机选规则
if RULE == 'random':
    rule_id = random.choice(list(RULES.keys()))
else:
    rule_id = RULE

rule = RULES.get(rule_id, RULES['5503'])

now = datetime.now(timezone.utc)
hostname = '37vwmu3rudbyc0v'
sport = random.randint(40000, 65000)
fake_users = ['admin', 'test', 'oracle', 'postgres', 'deploy', 'guest']
src_ips = ['10.0.0.{}.{}'.format(random.randint(1,254), random.randint(1,254)) for _ in range(COUNT)]

count = 0
with open(ALERTS_FILE, 'a') as f:
    for i in range(COUNT):
        ts = now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond:06d}+00:00'
        pid = random.randint(30000, 99999)
        fake_user = random.choice(fake_users)
        src_ip = src_ips[i]
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
                'firedtimes': 1,
                'mail': False,
                'groups': rule['groups'],
            },
            'agent': {
                'id': '001',
                'name': hostname,
                'ip': src_ip,
            },
            'manager': {'name': '0f98319a2063'},
            'id': f'test-{uuid.uuid4().hex[:12]}',
            'full_log': log_line,
            'predecoder': {
                'program_name': 'login' if 'login' in rule['desc'].lower() else 'sshd',
                'timestamp': datetime.now().strftime('%b %d %H:%M:%S'),
                'hostname': hostname,
            },
            'decoder': {},
            'location': '/var/log/auth.log',
            '_test': True,
        }

        f.write(json.dumps(alert, ensure_ascii=False) + '\n')
        count += 1
        print(f'  ✅ 已注入告警: {alert[\"id\"]} | 规则={rule_id} Lv{rule[\"level\"]} | {rule[\"desc\"][:50]} | src_ip={src_ip}')

print(f'')
print(f'📊 共注入 {count} 条测试告警 → {ALERTS_FILE}')
print(f'')
print(f'⏳ SignalWatcher 将在 2 秒内检测到新告警并自动触发研判链路...')
print(f'   请观察 Client 和 Server 终端窗口的实时输出')
"

echo ""
echo "✅ 测试告警注入完成"
echo "================================================"
