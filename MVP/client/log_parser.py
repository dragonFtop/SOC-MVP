"""
auth.log syslog line parser.

Handles sshd, sudo, su log entries. Two-stage parsing:
  1. Extract syslog header (timestamp, hostname, process, pid, message)
  2. Extract process-specific fields (user, src_ip, src_port, etc.)

Returns structured dicts or None for unmatched lines.
"""

import re
from datetime import datetime
from typing import Optional, Dict

# --------------- Stage 1: syslog header ---------------
_SYSLOG_RE = re.compile(
    r"^(\w{3}\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+(\w+)(?:\[(\d+)\])?:\s+(.*)$"
)

# --------------- Stage 2: process-specific message parsers ---------------
_SSHD_FAILED_PW = re.compile(
    r"Failed password for (?:invalid user )?(\S+) from (\S+) port (\d+)"
)
_SSHD_ACCEPTED = re.compile(
    r"Accepted (?:password|publickey) for (\S+) from (\S+) port (\d+)"
)
_SSHD_NO_ID = re.compile(
    r"Did not receive identification string from (\S+) port (\d+)"
)
_SSHD_CONN_CLOSED = re.compile(
    r"Connection closed by (?:authenticating user (\S+) )?(\S+) port (\d+)"
)
_SSHD_AUTH_FAILURE = re.compile(
    r"(?:error: PAM: )?[Aa]uthentication failure"
)
_SSHD_PREAUTH = re.compile(
    r"(?:error: )?Received disconnect from (\S+) port (\d+):.*\[preauth\]"
)
_SUDO_PAM_FAILURE = re.compile(
    r"authentication failure"
)
_SUDO_TTY = re.compile(
    r"TTY=(\S+).*USER=(\S+).*COMMAND=(.*)"
)
_SU_FAILED = re.compile(r"FAILED SU")
_SU_SUCCESS = re.compile(r"\(to (\S+)\) (\S+) on")


# Month abbreviation to number
_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _syslog_to_iso(ts_str: str) -> str:
    """Convert 'May 22 10:15:32' to ISO format using current year."""
    try:
        parts = ts_str.split()
        month = _MONTH_MAP.get(parts[0][:3])
        if month is None:
            return ts_str
        day = int(parts[1])
        time_parts = parts[2].split(":")
        hour, minute, second = int(time_parts[0]), int(time_parts[1]), int(time_parts[2])
        year = datetime.now().year
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
    except (IndexError, ValueError):
        return ts_str


def parse_line(line: str) -> Optional[Dict]:
    """
    Parse a single auth.log syslog line into a structured dict.

    Returns None for blank lines or lines that don't match any known pattern.
    """
    line = line.strip()
    if not line:
        return None

    m = _SYSLOG_RE.match(line)
    if not m:
        return None

    ts_str, hostname, process, pid_str, message = m.groups()
    pid = int(pid_str) if pid_str else 0
    timestamp = _syslog_to_iso(ts_str)

    result = {
        "timestamp": timestamp,
        "hostname": hostname,
        "process": process,
        "pid": pid,
        "message": message,
        "src_ip": None,
        "src_port": None,
        "dst_user": None,
        "log_type": "unknown",
        "raw_line": line,
    }

    # ---- sshd messages ----
    if process == "sshd":
        # Failed password (including invalid user)
        m2 = _SSHD_FAILED_PW.search(message)
        if m2:
            result["dst_user"] = m2.group(1)
            result["src_ip"] = m2.group(2)
            result["src_port"] = int(m2.group(3))
            result["log_type"] = "ssh_failed_password"
            return result

        # Accepted password/publickey
        m2 = _SSHD_ACCEPTED.search(message)
        if m2:
            result["dst_user"] = m2.group(1)
            result["src_ip"] = m2.group(2)
            result["src_port"] = int(m2.group(3))
            result["log_type"] = "ssh_accepted"
            return result

        # Did not receive identification string (scan)
        m2 = _SSHD_NO_ID.search(message)
        if m2:
            result["src_ip"] = m2.group(1)
            result["src_port"] = int(m2.group(2))
            result["log_type"] = "ssh_scan"
            return result

        # Connection closed
        m2 = _SSHD_CONN_CLOSED.search(message)
        if m2:
            user, ip, port = m2.groups()
            if user:
                result["dst_user"] = user
            result["src_ip"] = ip
            result["src_port"] = int(port)
            result["log_type"] = "ssh_connection_closed"
            return result

        # Authentication failure
        m2 = _SSHD_AUTH_FAILURE.search(message)
        if m2:
            result["log_type"] = "ssh_auth_failure"
            return result

        # Disconnect preauth
        m2 = _SSHD_PREAUTH.search(message)
        if m2:
            result["src_ip"] = m2.group(1)
            result["src_port"] = int(m2.group(2))
            result["log_type"] = "ssh_preauth_disconnect"
            return result

        # Catch-all for other sshd messages
        result["log_type"] = "ssh_other"
        return result

    # ---- sudo messages ----
    if process == "sudo":
        if _SUDO_PAM_FAILURE.search(message):
            result["log_type"] = "sudo_auth_failure"
            return result
        m2 = _SUDO_TTY.search(message)
        if m2:
            result["dst_user"] = m2.group(2)
            result["log_type"] = "sudo_success"
            return result
        result["log_type"] = "sudo_other"
        return result

    # ---- su messages ----
    if process == "su":
        if _SU_FAILED.search(message):
            result["log_type"] = "su_failed"
            return result
        m2 = _SU_SUCCESS.search(message)
        if m2:
            result["dst_user"] = m2.group(1)
            result["log_type"] = "su_success"
            return result
        result["log_type"] = "su_other"
        return result

    # ---- generic ----
    result["log_type"] = f"{process}_other"
    return result
