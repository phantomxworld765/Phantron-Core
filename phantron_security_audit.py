# -*- coding: utf-8 -*-
"""
PHANTRON  -  Security Engineering & System Auditing Framework
=============================================================

A DEFENSIVE (blue-team) endpoint monitoring + auditing toolkit for P7's own
host. It observes the local machine, raises anomalies, and parses access logs
to surface suspicious patterns. It does NOT attack anything - it is an
endpoint-defense *simulator* built on standard, native administration APIs
(psutil for the kernel socket/process tables, hashlib for integrity, and
regular expressions for log analysis).

Three pillars (as requested):

  1. NetworkTelemetryMonitor
       - enumerate active socket connections (native kernel table via psutil)
       - list listening ports / services
       - sample NIC counters and flag abnormal throughput spikes
       - flag fan-out / fan-in anomalies (one process -> many remotes, etc.)

  2. ProcessIntegrityAuditor
       - baseline snapshot of the running process set
       - reconstruct parent/child execution trees
       - SHA-256 hash binaries for tamper / integrity tracking
       - flag unknown binaries, processes from suspicious dirs, new spawns

  3. AccessLogAnalyzer
       - parse common access/auth log formats
       - detect repetitive failure loops (brute-force style)
       - signature-match known malicious request patterns

A SecurityAuditFramework orchestrates all three, produces a structured report,
and supports a continuous monitoring loop with an on_event callback so it can
narrate findings live inside PHANTRON's interface.

Import-safe on any OS (degrades gracefully without psutil / admin rights).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import psutil
except Exception:  # optional
    psutil = None

IS_WINDOWS = platform.system() == "Windows"


# ════════════════════════════════════════════════════════════════════════════
#  SEVERITY + FINDINGS
# ════════════════════════════════════════════════════════════════════════════
SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass
class Finding:
    category: str          # network | process | log
    severity: str          # INFO..CRITICAL
    title: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        return {
            "category": self.category, "severity": self.severity,
            "title": self.title, "detail": self.detail,
            "evidence": self.evidence, "timestamp": self.timestamp,
        }

    def __str__(self) -> str:
        return "[%s] (%s/%s) %s - %s" % (
            self.timestamp, self.severity, self.category, self.title, self.detail)


# ════════════════════════════════════════════════════════════════════════════
#  1) NETWORK TELEMETRY MONITOR
# ════════════════════════════════════════════════════════════════════════════
class NetworkTelemetryMonitor:
    """Inspects sockets, listening ports, and per-NIC throughput for anomalies."""

    # Ports that are normal to see listening; anything else listening on a
    # public bind is worth a low-severity note.
    COMMON_LISTEN = {80, 443, 53, 22, 3389, 445, 139, 135, 5353, 1900, 631,
                     8080, 8443, 3306, 5432, 27017, 6379, 11434}

    def __init__(self, spike_bps: int = 8 * 1024 * 1024,  # 8 MB/s sustained
                 fanout_threshold: int = 60):
        self.spike_bps = spike_bps
        self.fanout_threshold = fanout_threshold
        self._last_io = None
        self._last_ts = None

    # ---- raw enumeration ----
    def snapshot_connections(self) -> List[dict]:
        if not psutil:
            return []
        rows = []
        try:
            conns = psutil.net_connections(kind="inet")
        except Exception:
            return []
        for c in conns:
            try:
                rows.append({
                    "fd": c.fd,
                    "type": "TCP" if c.type == socket.SOCK_STREAM else "UDP",
                    "laddr": "%s:%s" % (c.laddr.ip, c.laddr.port) if c.laddr else "",
                    "raddr": "%s:%s" % (c.raddr.ip, c.raddr.port) if c.raddr else "",
                    "status": c.status,
                    "pid": c.pid,
                    "process": self._pname(c.pid),
                })
            except Exception:
                continue
        return rows

    @staticmethod
    def _pname(pid) -> str:
        if not psutil or not pid:
            return ""
        try:
            return psutil.Process(pid).name()
        except Exception:
            return ""

    def listening_ports(self) -> List[dict]:
        return [c for c in self.snapshot_connections() if c["status"] == "LISTEN"]

    def established(self) -> List[dict]:
        return [c for c in self.snapshot_connections() if c["status"] == "ESTABLISHED"]

    # ---- throughput spike detection ----
    def sample_throughput(self) -> Optional[dict]:
        """Returns bytes/sec since the previous sample (None on first call)."""
        if not psutil:
            return None
        try:
            io = psutil.net_io_counters()
        except Exception:
            return None
        now = time.time()
        result = None
        if self._last_io is not None and self._last_ts is not None:
            dt = max(0.001, now - self._last_ts)
            sent_bps = (io.bytes_sent - self._last_io.bytes_sent) / dt
            recv_bps = (io.bytes_recv - self._last_io.bytes_recv) / dt
            result = {"sent_bps": int(sent_bps), "recv_bps": int(recv_bps),
                      "total_bps": int(sent_bps + recv_bps)}
        self._last_io = io
        self._last_ts = now
        return result

    # ---- the audit ----
    def audit(self) -> List[Finding]:
        findings: List[Finding] = []
        if not psutil:
            findings.append(Finding("network", "INFO", "psutil unavailable",
                                    "Install psutil for live socket telemetry."))
            return findings

        conns = self.snapshot_connections()

        # listening ports
        for c in conns:
            if c["status"] != "LISTEN":
                continue
            try:
                port = int(c["laddr"].rsplit(":", 1)[-1])
            except Exception:
                continue
            public_bind = c["laddr"].startswith(("0.0.0.0", "::", "[::]"))
            if port not in self.COMMON_LISTEN and public_bind:
                findings.append(Finding(
                    "network", "LOW", "Uncommon public listening port",
                    "Service listening on %s (pid %s, %s)" % (c["laddr"], c["pid"], c["process"]),
                    evidence=c))

        # fan-out: one process holding an unusually large number of remote conns
        per_proc = defaultdict(list)
        for c in conns:
            if c["status"] == "ESTABLISHED" and c["raddr"]:
                per_proc[(c["pid"], c["process"])].append(c["raddr"])
        for (pid, name), remotes in per_proc.items():
            if len(remotes) >= self.fanout_threshold:
                findings.append(Finding(
                    "network", "MEDIUM", "High connection fan-out",
                    "%s (pid %s) holds %d established connections" % (name, pid, len(remotes)),
                    evidence={"pid": pid, "process": name, "count": len(remotes)}))

        # throughput spike (needs two samples)
        self.sample_throughput()
        time.sleep(1.0)
        tp = self.sample_throughput()
        if tp and tp["total_bps"] >= self.spike_bps:
            findings.append(Finding(
                "network", "MEDIUM", "Abnormal network throughput spike",
                "%.1f MB/s (recv %.1f, sent %.1f)" % (
                    tp["total_bps"] / 1048576, tp["recv_bps"] / 1048576,
                    tp["sent_bps"] / 1048576),
                evidence=tp))
        return findings

    def monitor_loop(self, interval: float, on_event: Callable[[Finding], None],
                     stop: Optional[threading.Event] = None):
        stop = stop or threading.Event()
        while not stop.is_set():
            for f in self.audit():
                on_event(f)
            stop.wait(interval)


# ════════════════════════════════════════════════════════════════════════════
#  2) SYSTEM PROCESS INTEGRITY AUDIT
# ════════════════════════════════════════════════════════════════════════════
class ProcessIntegrityAuditor:
    """Tracks the process set, builds execution trees, flags anomalies."""

    SUSPICIOUS_DIR_PARTS = [
        r"\\temp\\", r"\\tmp\\", r"\\appdata\\local\\temp",
        r"\\downloads\\", r"\\windows\\temp", r"\\$recycle.bin",
        r"\\users\\public\\", r"/tmp/", r"/dev/shm/",
    ]

    def __init__(self, allowlist: Optional[set] = None, hash_binaries: bool = True):
        self.allowlist = {a.lower() for a in (allowlist or set())}
        self.hash_binaries = hash_binaries
        self.baseline: Dict[int, dict] = {}
        self.known_hashes: Dict[str, str] = {}   # exe path -> sha256

    # ---- snapshot ----
    def _proc_info(self, p) -> Optional[dict]:
        try:
            with p.oneshot():
                info = {
                    "pid": p.pid, "ppid": p.ppid(), "name": p.name(),
                    "exe": (p.exe() if hasattr(p, "exe") else "") or "",
                    "cmdline": " ".join(p.cmdline()) if p.cmdline() else "",
                    "username": (p.username() if hasattr(p, "username") else "") or "",
                    "create_time": p.create_time(),
                }
            return info
        except Exception:
            return None

    def snapshot(self) -> Dict[int, dict]:
        snap: Dict[int, dict] = {}
        if not psutil:
            return snap
        for p in psutil.process_iter():
            info = self._proc_info(p)
            if info:
                snap[info["pid"]] = info
        return snap

    def set_baseline(self) -> int:
        self.baseline = self.snapshot()
        if self.hash_binaries:
            for info in self.baseline.values():
                self._record_hash(info.get("exe"))
        return len(self.baseline)

    # ---- integrity hashing ----
    @staticmethod
    def sha256_file(path: str) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def _record_hash(self, exe: str):
        if exe and exe not in self.known_hashes:
            digest = self.sha256_file(exe)
            if digest:
                self.known_hashes[exe] = digest

    # ---- execution tree ----
    def build_tree(self, snap: Optional[Dict[int, dict]] = None) -> Dict[int, List[int]]:
        snap = snap or self.snapshot()
        tree: Dict[int, List[int]] = defaultdict(list)
        for pid, info in snap.items():
            tree[info["ppid"]].append(pid)
        return tree

    @staticmethod
    def _is_suspicious_path(exe: str) -> bool:
        low = (exe or "").lower()
        return any(part in low for part in ProcessIntegrityAuditor.SUSPICIOUS_DIR_PARTS)

    # ---- audit ----
    def audit(self) -> List[Finding]:
        findings: List[Finding] = []
        if not psutil:
            findings.append(Finding("process", "INFO", "psutil unavailable",
                                    "Install psutil for process integrity auditing."))
            return findings

        current = self.snapshot()

        for pid, info in current.items():
            exe = info.get("exe", "")
            name = info.get("name", "")

            # binary running from a suspicious directory
            if self._is_suspicious_path(exe):
                findings.append(Finding(
                    "process", "HIGH", "Binary executing from suspicious location",
                    "%s (pid %s) -> %s" % (name, pid, exe),
                    evidence=info))

            # process with no backing executable on disk
            if exe and not os.path.exists(exe):
                findings.append(Finding(
                    "process", "MEDIUM", "Process exe path missing on disk",
                    "%s (pid %s) claims exe %s which is gone" % (name, pid, exe),
                    evidence=info))

            # integrity drift: known binary whose hash changed
            if self.hash_binaries and exe and exe in self.known_hashes:
                digest = self.sha256_file(exe)
                if digest and digest != self.known_hashes[exe]:
                    findings.append(Finding(
                        "process", "CRITICAL", "Binary integrity drift (hash mismatch)",
                        "%s changed on disk since baseline" % exe,
                        evidence={"exe": exe, "baseline": self.known_hashes[exe][:16],
                                  "current": digest[:16]}))

            # new process not present in baseline (and not allowlisted)
            if self.baseline and pid not in self.baseline \
                    and name.lower() not in self.allowlist:
                parent = current.get(info["ppid"], {}).get("name", "?")
                findings.append(Finding(
                    "process", "LOW", "New process since baseline",
                    "%s (pid %s) spawned by %s" % (name, pid, parent),
                    evidence=info))

        # orphaned execution trees (parent gone but children alive) - INFO
        tree = self.build_tree(current)
        for ppid, kids in tree.items():
            if ppid and ppid not in current and len(kids) >= 3:
                findings.append(Finding(
                    "process", "MEDIUM", "Orphaned execution tree",
                    "Dead parent pid %s still has %d children" % (ppid, len(kids)),
                    evidence={"ppid": ppid, "children": kids[:20]}))
        return findings

    def monitor_loop(self, interval: float, on_event: Callable[[Finding], None],
                     stop: Optional[threading.Event] = None):
        if not self.baseline:
            self.set_baseline()
        stop = stop or threading.Event()
        while not stop.is_set():
            for f in self.audit():
                on_event(f)
            stop.wait(interval)


# ════════════════════════════════════════════════════════════════════════════
#  3) NETWORK / ACCESS LOG ANALYZER
# ════════════════════════════════════════════════════════════════════════════
class AccessLogAnalyzer:
    """Parses access/auth logs to detect brute-force loops and attack signatures."""

    # Apache/Nginx "combined" log line
    COMBINED_RE = re.compile(
        r'(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
        r'"(?P<method>\S+)\s+(?P<path>\S+)[^"]*"\s+(?P<status>\d{3})\s+(?P<size>\S+)'
        r'(?:\s+"[^"]*"\s+"(?P<ua>[^"]*)")?')

    # SSH / auth.log style failed login
    SSH_FAIL_RE = re.compile(
        r'(?P<time>\w{3}\s+\d+\s+[\d:]+).*(?:Failed password|authentication failure|'
        r'Invalid user).*?(?:from\s+)?(?P<ip>\d+\.\d+\.\d+\.\d+)')

    # malicious request signatures
    SIGNATURES = [
        ("SQL injection attempt", "HIGH",
         re.compile(r"(?:union[\s/*+%20]+select|or\s+1\s*=\s*1|'\s*or\s*'|'--|/\*.*\*/|sleep\(\d+\)|benchmark\(|information_schema)", re.I)),
        ("Path traversal attempt", "HIGH", re.compile(r"(?:\.\./){2,}|(?:%2e%2e[%2f/]){2,}|/etc/passwd|\\windows\\win.ini", re.I)),
        ("XSS attempt", "MEDIUM", re.compile(r"(?:<script>|onerror\s*=|javascript:|%3cscript)", re.I)),
        ("Command injection attempt", "HIGH", re.compile(r"(?:;\s*nc\s|\|\s*nc\s|\bwget\s+http|\bcurl\s+http|/bin/sh|cmd\.exe\s*/c)", re.I)),
        ("Sensitive file probe", "MEDIUM", re.compile(r"(?:/\.env|/\.git/|/wp-admin|/phpmyadmin|/\.aws/|web\.config)", re.I)),
        ("Known scanner user-agent", "LOW", re.compile(r"(?:sqlmap|nikto|nmap|masscan|nessus|acunetix|dirbuster|hydra)", re.I)),
    ]

    FAILURE_STATUS = {"401", "403", "407", "429"}

    def __init__(self, brute_window_sec: int = 60, brute_threshold: int = 10,
                 notfound_threshold: int = 25):
        self.brute_window = brute_window_sec
        self.brute_threshold = brute_threshold
        self.notfound_threshold = notfound_threshold

    # ---- parsing ----
    def parse_line(self, line: str) -> Optional[dict]:
        m = self.COMBINED_RE.search(line)
        if m:
            d = m.groupdict()
            d["kind"] = "http"
            return d
        m = self.SSH_FAIL_RE.search(line)
        if m:
            d = m.groupdict()
            d.update({"kind": "auth", "status": "401", "method": "AUTH",
                      "path": "(login)", "ua": ""})
            return d
        return None

    # ---- analysis ----
    def analyze_lines(self, lines: List[str]) -> List[Finding]:
        findings: List[Finding] = []
        fail_by_ip = defaultdict(int)
        notfound_by_ip = defaultdict(int)
        first_seen: Dict[str, int] = {}
        last_seen: Dict[str, int] = {}

        for idx, raw in enumerate(lines):
            # Signature matching runs on the FULL raw line so it catches attacks
            # even when the access-log format doesn't parse cleanly.
            rec = self.parse_line(raw)
            ip = (rec or {}).get("ip", "?")
            for name, sev, rx in self.SIGNATURES:
                if rx.search(raw):
                    findings.append(Finding(
                        "log", sev, name,
                        "%s -> %s" % (ip, raw.strip()[:140]),
                        evidence={"ip": ip, "line": raw.strip()[:200]}))

            if not rec:
                continue
            status = str(rec.get("status", ""))

            first_seen.setdefault(ip, idx)
            last_seen[ip] = idx

            # failure accounting
            if rec.get("kind") == "auth" or status in self.FAILURE_STATUS:
                fail_by_ip[ip] += 1
            if status == "404":
                notfound_by_ip[ip] += 1

        # brute-force / repetitive failure loops
        for ip, count in fail_by_ip.items():
            if count >= self.brute_threshold:
                findings.append(Finding(
                    "log", "HIGH", "Repetitive failure loop (possible brute-force)",
                    "%s produced %d auth/permission failures" % (ip, count),
                    evidence={"ip": ip, "failures": count}))

        # aggressive scanning (many 404s)
        for ip, count in notfound_by_ip.items():
            if count >= self.notfound_threshold:
                findings.append(Finding(
                    "log", "MEDIUM", "Aggressive enumeration (many 404s)",
                    "%s hit %d missing paths" % (ip, count),
                    evidence={"ip": ip, "not_found": count}))
        return findings

    def analyze_file(self, path: str) -> List[Finding]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return [Finding("log", "INFO", "Log file unreadable", "%s: %s" % (path, e))]
        return self.analyze_lines(lines)

    def tail_follow(self, path: str, on_event: Callable[[Finding], None],
                    stop: Optional[threading.Event] = None, batch: int = 200):
        """Continuously follow a growing log file and analyse new lines."""
        stop = stop or threading.Event()
        buf: deque = deque(maxlen=batch)
        try:
            f = open(path, "r", encoding="utf-8", errors="replace")
        except Exception as e:
            on_event(Finding("log", "INFO", "Cannot open log", str(e)))
            return
        f.seek(0, os.SEEK_END)
        while not stop.is_set():
            line = f.readline()
            if not line:
                if buf:
                    for fnd in self.analyze_lines(list(buf)):
                        on_event(fnd)
                    buf.clear()
                stop.wait(1.0)
                continue
            buf.append(line)


# ════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ════════════════════════════════════════════════════════════════════════════
class SecurityAuditFramework:
    """Runs all three pillars and produces a structured, scored report."""

    def __init__(self, config: Optional[dict] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.cfg = config or {}
        self.on_event = on_event
        self.net = NetworkTelemetryMonitor(
            spike_bps=self.cfg.get("net_spike_bps", 8 * 1024 * 1024),
            fanout_threshold=self.cfg.get("net_fanout_threshold", 60))
        self.proc = ProcessIntegrityAuditor(
            allowlist=set(self.cfg.get("process_allowlist", [])),
            hash_binaries=self.cfg.get("hash_binaries", True))
        self.logs = AccessLogAnalyzer(
            brute_window_sec=self.cfg.get("brute_window_sec", 60),
            brute_threshold=self.cfg.get("brute_threshold", 10))
        self._stop = threading.Event()

    def _emit(self, etype: str, **data):
        if self.on_event:
            try:
                self.on_event(etype, data)
            except Exception:
                pass

    def _notify(self, f: Finding):
        self._emit("finding", **f.to_dict())

    def run_full_audit(self, log_paths: Optional[List[str]] = None) -> dict:
        self._emit("start", message="Security audit shuru...")
        findings: List[Finding] = []

        self._emit("phase", message="Network telemetry scan...")
        findings += self.net.audit()

        self._emit("phase", message="Process integrity audit...")
        if not self.proc.baseline:
            self.proc.set_baseline()
        findings += self.proc.audit()

        for lp in (log_paths or self.cfg.get("log_paths", [])):
            self._emit("phase", message="Analyzing log: %s" % lp)
            findings += self.logs.analyze_file(lp)

        for f in findings:
            self._notify(f)

        return self._report(findings)

    def _report(self, findings: List[Finding]) -> dict:
        counts = {s: 0 for s in SEVERITY_ORDER}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        risk = max((SEVERITY_ORDER[f.severity] for f in findings), default=0)
        risk_label = [k for k, v in SEVERITY_ORDER.items() if v == risk][0]
        report = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "host": platform.node(),
            "os": "%s %s" % (platform.system(), platform.release()),
            "total_findings": len(findings),
            "severity_counts": counts,
            "overall_risk": risk_label,
            "findings": [f.to_dict() for f in findings],
        }
        self._emit("done", message="Audit complete. Risk: %s, findings: %d"
                   % (risk_label, len(findings)), report=report)
        return report

    # ---- continuous monitoring ----
    def start_monitoring(self, interval: float = 15.0,
                         log_paths: Optional[List[str]] = None):
        self._stop.clear()
        self.proc.set_baseline()
        threading.Thread(target=self.net.monitor_loop,
                         args=(interval, self._notify, self._stop), daemon=True).start()
        threading.Thread(target=self.proc.monitor_loop,
                         args=(interval, self._notify, self._stop), daemon=True).start()
        for lp in (log_paths or self.cfg.get("log_paths", [])):
            threading.Thread(target=self.logs.tail_follow,
                             args=(lp, self._notify, self._stop), daemon=True).start()
        self._emit("monitor", message="Live monitoring active (interval %ss)" % interval)

    def stop_monitoring(self):
        self._stop.set()
        self._emit("monitor", message="Monitoring stopped.")


# ════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════
def _print_report(report: dict):
    print("\n" + "=" * 60)
    print(" PHANTRON Security Audit  -  %s" % report["generated"])
    print(" Host: %s   OS: %s" % (report["host"], report["os"]))
    print(" Overall risk: %s   Total findings: %d"
          % (report["overall_risk"], report["total_findings"]))
    print(" Severity:", report["severity_counts"])
    print("=" * 60)
    for f in report["findings"]:
        print(" [%s] %-9s %s" % (f["category"][:4], f["severity"], f["title"]))
        if f["detail"]:
            print("        %s" % f["detail"])
    if not report["findings"]:
        print(" No anomalies detected.")
    print("=" * 60 + "\n")


def _cli():
    args = sys.argv[1:]
    log_paths = [a for a in args if os.path.exists(a)]
    fw = SecurityAuditFramework(config={"log_paths": log_paths})
    if "--monitor" in args:
        fw.on_event = lambda t, d: print("[%s] %s" % (t.upper(), d.get("message") or d.get("title", "")))
        fw.start_monitoring(interval=float(os.environ.get("AUDIT_INTERVAL", "15")))
        print("Live monitoring... Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            fw.stop_monitoring()
        return
    report = fw.run_full_audit(log_paths)
    _print_report(report)
    if "--json" in args:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    _cli()
