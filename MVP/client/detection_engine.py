"""
DetectionEngine - Local auth.log detection engine.

Replaces SignalWatcher. Handles:
  1. Tailing auth.log via byte-offset polling
  2. Parsing lines via log_parser into structured records
  3. Inserting into DuckDB auth_events table
  4. Running SQL detection rules with threshold + cooldown
  5. Publishing micro-signals to NATS soc.signals.<node_id>
  6. Serving evidence queries for the DuckDB sidecar
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import duckdb
import yaml

from config import (
    DEFAULT_NODE_ID,
    NATS_SERVERS,
    NATS_SIGNAL_SUBJECT,
)
from common.nats_utils import ensure_stream, safe_ack
from common.monitor_events import MonitorEmitter

# Severity string -> numeric level mapping
SEVERITY_MAP = {"critical": 11, "high": 8, "medium": 5, "low": 2}


class DetectionEngine:
    """Local log-based detection engine using YAML rules + DuckDB SQL."""

    def __init__(
        self,
        node_id: str = DEFAULT_NODE_ID,
        auth_log_path: str = "/var/log/auth.log",
        rules_path: str = None,
        watch_interval: int = 2,
        retention_minutes: int = 60,
        db_path: str = None,
    ):
        self.node_id = node_id
        self.auth_log_path = auth_log_path
        self.watch_interval = watch_interval
        self.retention_minutes = retention_minutes

        # DuckDB connection (in-memory by default)
        self.con = duckdb.connect(db_path) if db_path else duckdb.connect()

        # Load rules
        if rules_path is None:
            from config import DETECTION_RULES_PATH as drp
            rules_path = drp
        self.rules_path = rules_path
        self.rules = self._load_rules()

        # State
        self.last_offset = 0
        self.cooldowns: Dict[str, float] = {}  # f"{rule_id}:{group_key}" -> expiry_ts
        self.nc = None
        self.js = None
        self.monitor = None
        self._running = False
        self._cleanup_counter = 0
        self._stats = {
            "lines_parsed": 0,
            "lines_inserted": 0,
            "signals_fired": 0,
            "signals_suppressed": 0,
            "errors": 0,
        }

        self._create_tables()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_rules(self) -> list:
        try:
            with open(self.rules_path, "r") as f:
                data = yaml.safe_load(f)
            rules = data.get("rules", []) if data else []
            print(f"   📋 Loaded {len(rules)} detection rules from {self.rules_path}")
            return rules
        except FileNotFoundError:
            print(f"   ⚠️ Rules file not found: {self.rules_path}, detection disabled")
            return []
        except yaml.YAMLError as e:
            print(f"   ⚠️ YAML parse error: {e}, detection disabled")
            return []

    def _create_tables(self):
        self.con.execute("""
            CREATE OR REPLACE SEQUENCE auth_events_seq START 1
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS auth_events (
                id BIGINT DEFAULT nextval('auth_events_seq'),
                timestamp TIMESTAMP,
                hostname VARCHAR,
                process VARCHAR,
                pid INTEGER,
                message VARCHAR,
                src_ip VARCHAR,
                src_port INTEGER,
                dst_user VARCHAR,
                log_type VARCHAR,
                raw_line VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ae_src_ip ON auth_events(src_ip)"
        )
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ae_ts ON auth_events(timestamp)"
        )

    # ------------------------------------------------------------------
    # NATS connection
    # ------------------------------------------------------------------

    async def _connect_nats(self):
        try:
            from nats import connect as nats_connect
        except ImportError:
            print("   [WARN] nats-py not installed; signals will not be published")
            return

        self.nc = await nats_connect(servers=NATS_SERVERS)
        self.js = self.nc.jetstream()
        await ensure_stream(self.js, "SIGNALS", subjects=[f"{NATS_SIGNAL_SUBJECT}.*"])
        self.monitor = MonitorEmitter(
            self.nc,
            "DetectionEngine",
            self.node_id,
        )
        print(f"   ✅ DetectionEngine connected to NATS")

    # ------------------------------------------------------------------
    # File tailing (byte-offset polling)
    # ------------------------------------------------------------------

    def _tail_log(self) -> List[str]:
        """Read new lines from auth.log using byte-offset tracking."""
        if not os.path.exists(self.auth_log_path):
            self.last_offset = 0
            return []

        try:
            with open(self.auth_log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                file_size = f.tell()

                # Handle file rotation (truncation)
                if file_size < self.last_offset:
                    self.last_offset = 0

                if file_size == self.last_offset:
                    return []

                f.seek(self.last_offset)
                new_content = f.read()
                self.last_offset = file_size

            lines = new_content.split("\n")
            return [l for l in lines if l.strip()]
        except (PermissionError, OSError) as e:
            self._stats["errors"] += 1
            return []

    # ------------------------------------------------------------------
    # Parsing & insertion
    # ------------------------------------------------------------------

    def _parse_and_insert(self, lines: List[str]) -> int:
        """Parse raw lines and insert into DuckDB. Returns count inserted."""
        from client.log_parser import parse_line

        inserted = 0
        for line in lines:
            self._stats["lines_parsed"] += 1
            parsed = parse_line(line)
            if parsed is None:
                continue
            try:
                ts = parsed["timestamp"]
                self.con.execute(
                    """
                    INSERT INTO auth_events
                        (timestamp, hostname, process, pid, message, src_ip, src_port, dst_user, log_type, raw_line)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ts if ts else None,
                        parsed["hostname"],
                        parsed["process"],
                        parsed["pid"],
                        parsed["message"],
                        parsed["src_ip"],
                        parsed["src_port"],
                        parsed["dst_user"],
                        parsed["log_type"],
                        parsed["raw_line"],
                    ],
                )
                inserted += 1
                self._stats["lines_inserted"] += 1
            except Exception:
                self._stats["errors"] += 1
        return inserted

    # ------------------------------------------------------------------
    # SQL rule builder
    # ------------------------------------------------------------------

    def _build_rule_sql(self, rule: dict) -> Optional[str]:
        """Convert a YAML rule definition into a DuckDB detection SQL query."""
        match = rule.get("match", {})
        threshold = rule.get("threshold", {})
        process = match.get("process")
        patterns = match.get("patterns", [])
        count = threshold.get("count", 1)
        window_seconds = threshold.get("window_seconds", 300)
        group_by = threshold.get("group_by")

        if not process or not patterns:
            return None

        pattern_conditions = " OR ".join(
            [f"message LIKE '%{p}%'" for p in patterns]
        )

        sql = f"""
            SELECT COUNT(*) as cnt"""
        if group_by:
            sql += f", {group_by}"
        sql += f"""
            FROM auth_events
            WHERE process = '{process}'
              AND ({pattern_conditions})
              AND timestamp >= CURRENT_TIMESTAMP - INTERVAL '{window_seconds} seconds'
        """
        if group_by:
            sql += f"""
            GROUP BY {group_by}
            HAVING COUNT(*) >= {count}
            ORDER BY cnt DESC
            """
        else:
            sql += f"""
            HAVING COUNT(*) >= {count}
            """

        return sql

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _run_detection(self) -> List[dict]:
        """Run all rules against auth_events. Returns signals to publish."""
        signals = []
        now = time.time()

        for rule in self.rules:
            sql = self._build_rule_sql(rule)
            if not sql:
                continue

            try:
                rows = self.con.execute(sql).fetchall()
            except Exception:
                self._stats["errors"] += 1
                continue

            for row in rows:
                count_val = int(row[0])
                if len(row) > 1 and row[1] is not None:
                    group_key = str(row[1])
                else:
                    group_key = "global"

                # Cooldown check
                cd_key = f"{rule['id']}:{group_key}"
                if cd_key in self.cooldowns:
                    if now < self.cooldowns[cd_key]:
                        self._stats["signals_suppressed"] += 1
                        continue

                # Build signal
                signal = self._build_signal(rule, group_key, count_val)
                signals.append(signal)
                self._stats["signals_fired"] += 1

                # Set cooldown
                cd = rule.get("cooldown_seconds", 600)
                if cd > 0:
                    self.cooldowns[cd_key] = now + cd

        return signals

    def _build_signal(self, rule: dict, group_key: str, count: int) -> dict:
        """Build a micro-signal dict from a fired rule."""
        signal_id = f"sig-{uuid.uuid4().hex[:8]}"
        severity = rule.get("severity", "medium")
        signal_cfg = rule.get("signal", {})
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Only set src_ip when group_key is an actual IP (not "global" placeholder)
        gb = rule.get("threshold", {}).get("group_by")
        src_ip = group_key if (gb == "src_ip" and group_key != "global") else "0.0.0.0"

        return {
            "signal_id": signal_id,
            "node_id": self.node_id,
            "detection_rule_id": rule["id"],
            "rule_id": rule["id"],
            "rule_level": SEVERITY_MAP.get(severity, 5),
            "rule_desc": f"{rule['name']}: {count} matches from {group_key}",
            "severity": severity,
            "category": rule.get("category", "unknown"),
            "src_ip": src_ip,
            "event_time": now_iso,
            "suggested_logs": signal_cfg.get("suggested_logs", ["auth_log"]),
            "raw_ref": f"{self.node_id}/auth_log#{group_key}#{now_iso}",
            "matched_count": count,
        }

    # ------------------------------------------------------------------
    # Signal publishing
    # ------------------------------------------------------------------

    async def _publish_signals(self, signals: List[dict]):
        if not self.js:
            return

        for signal in signals:
            subject = f"{NATS_SIGNAL_SUBJECT}.{self.node_id}"
            payload = json.dumps(signal, ensure_ascii=False).encode()
            try:
                ack = await self.js.publish(subject, payload)
                if self.monitor:
                    await self.monitor.signal_sent(
                        signal_id=signal["signal_id"],
                        rule_id=signal["detection_rule_id"],
                    )
                print(
                    f"   📡 Signal published: {signal['detection_rule_id']} "
                    f"({signal.get('src_ip') or 'global'}) "
                    f"count={signal['matched_count']} "
                    f"stream={ack.stream}, seq={ack.seq}"
                )
            except Exception as e:
                print(f"   ⚠️ Failed to publish signal: {e}")
                self._stats["errors"] += 1

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_old_events(self):
        try:
            self.con.execute(
                f"DELETE FROM auth_events WHERE timestamp < CURRENT_TIMESTAMP - INTERVAL '{self.retention_minutes} minutes'"
            )
        except Exception:
            pass

    def _cleanup_cooldowns(self):
        now = time.time()
        expired = [k for k, v in self.cooldowns.items() if v <= now]
        for k in expired:
            del self.cooldowns[k]

    def get_stats(self) -> dict:
        try:
            event_count = self.con.execute(
                "SELECT COUNT(*) FROM auth_events"
            ).fetchone()[0]
        except Exception:
            event_count = 0
        return {**self._stats, "events_in_memory": event_count}

    # ------------------------------------------------------------------
    # Public query interface (for DuckDB sidecar)
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_value(value):
        """Convert DuckDB/Database native types to JSON-safe Python types."""
        from datetime import date as dt_date, datetime as dt_datetime
        from decimal import Decimal
        if isinstance(value, dt_datetime):
            return value.isoformat()
        if isinstance(value, dt_date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def query_events(
        self, filters: dict = None, limit: int = 50
    ) -> List[dict]:
        """
        Query pre-parsed auth_events. Used by DuckDB sidecar for on-demand evidence.

        Returns list of dicts with auth event fields (all values JSON-safe).
        """
        filters = filters or {}
        sql = "SELECT * FROM auth_events WHERE 1=1"
        params = []

        if filters.get("src_ip"):
            sql += " AND src_ip = ?"
            params.append(filters["src_ip"])
        if filters.get("process"):
            sql += " AND process = ?"
            params.append(filters["process"])
        if filters.get("log_type"):
            sql += " AND log_type = ?"
            params.append(filters["log_type"])
        if filters.get("since"):
            sql += " AND timestamp >= ?"
            params.append(filters["since"])

        sql += " ORDER BY timestamp DESC"

        if limit and limit > 0:
            sql += f" LIMIT {limit}"

        try:
            result = self.con.execute(sql, params if params else None)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [
                {col: self._serialize_value(val) for col, val in zip(columns, row)}
                for row in rows
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Async main loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        print(f"🛡️  [DetectionEngine] Starting auth.log detection...")
        print(f"   📂 Log file: {self.auth_log_path}")
        print(f"   📋 Rules: {len(self.rules)} loaded")

        await self._connect_nats()

        # Bootstrap: record current file size as starting offset
        if os.path.exists(self.auth_log_path):
            with open(self.auth_log_path, "rb") as f:
                f.seek(0, 2)
                self.last_offset = f.tell()
                print(f"   📏 Starting offset: {self.last_offset} bytes")
        else:
            print(f"   ⚠️ auth.log not found, will wait for it...")

        self._running = True

        while self._running:
            try:
                # 1. Tail the log file
                new_lines = self._tail_log()

                # 2. Parse and insert into DuckDB
                if new_lines:
                    inserted = self._parse_and_insert(new_lines)
                    if inserted > 0:
                        print(
                            f"   📥 Ingested {inserted} events ({len(new_lines)} raw lines)"
                        )

                # 3. Run detection rules
                signals = self._run_detection()

                # 4. Publish signals
                if signals:
                    await self._publish_signals(signals)

                # 5. Periodic cleanup
                self._cleanup_counter += 1
                if self._cleanup_counter >= 30:
                    self._cleanup_old_events()
                    self._cleanup_cooldowns()
                    self._cleanup_counter = 0

                await asyncio.sleep(self.watch_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"   ⚠️ DetectionEngine loop error: {e}")
                self._stats["errors"] += 1
                await asyncio.sleep(self.watch_interval)

        print("🛡️  [DetectionEngine] Stopped")

    async def shutdown(self):
        self._running = False
        if self.nc:
            try:
                await self.nc.close()
            except Exception:
                pass
        try:
            self.con.close()
        except Exception:
            pass
        print("🛡️  [DetectionEngine] Shutdown complete")
