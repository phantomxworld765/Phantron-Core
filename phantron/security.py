# -*- coding: utf-8 -*-
"""
PHANTRON security - the defensive / self-protection system.

This module is PHANTRON's "shield". It is read-only and protective by design:
it inspects the machine and reports, and only takes protective actions (like
toggling the firewall or quarantining a flagged file) when explicitly asked.
It NEVER attacks, scans others, or does anything offensive - this is purely
defence for P7's own PC.

Everything is best-effort and lazy. On Windows it leans on built-in tools
(Defender via PowerShell, netsh firewall, netstat). On other OSes, or when a
tool is unavailable, each method degrades gracefully with a helpful message.

Capabilities:
  * audit()            - one-shot security health report (score + findings)
  * antivirus_scan()   - trigger a Microsoft Defender quick/full scan
  * defender_status()  - real-time protection / definitions status
  * firewall()         - status / on / off (Windows Defender Firewall)
  * open_ports()       - list listening ports + owning process
  * suspicious()       - heuristic scan of running processes + autoruns
  * connections()      - active external network connections
  * quarantine(path)   - move a flagged file into a locked quarantine folder
  * harden()           - safe, reversible hardening tips + optional toggles
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

IS_WINDOWS = platform.system() == "Windows"


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _ps(cmd: str, timeout: int = 60) -> str:
    """Run a PowerShell command on Windows and return its text output."""
    if not IS_WINDOWS:
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        return ((out.stdout or "") + (out.stderr or "")).strip()
    except Exception as exc:
        return "ERR:" + str(exc)


# Process names that are worth a second look if they appear in odd places or
# in large numbers. This is a heuristic aid, NOT a verdict.
_WATCH_NAMES = [
    "powershell", "cmd", "wscript", "cscript", "mshta", "regsvr32",
    "rundll32", "certutil", "bitsadmin", "schtasks", "ssh", "nc", "ncat",
    "python", "node", "java", "telnet",
]
# Folders where executables are commonly dropped by unwanted software.
_RISKY_DIRS = ["temp", "appdata\\local\\temp", "downloads", "public", "programdata"]


class Security:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self.quarantine_dir = cfg.files_dir().parent / "quarantine"

    def _log(self, action: str, detail: str = "", kind: str = "security"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  Antivirus (Microsoft Defender)
    # ─────────────────────────────────────────────────────────────────────
    def defender_status(self) -> str:
        if not IS_WINDOWS:
            return "Defender sirf Windows pe hai. (Linux/Mac pe apna AV use karo.)"
        self._log("Defender", "status")
        out = _ps(
            "$s = Get-MpComputerStatus; "
            "'RealTime=' + $s.RealTimeProtectionEnabled + "
            "'; Antivirus=' + $s.AntivirusEnabled + "
            "'; Definitions=' + $s.AntivirusSignatureVersion + "
            "'; LastScan=' + $s.QuickScanEndTime"
        )
        if not out or out.startswith("ERR:"):
            return "Defender status nahi mila (admin PowerShell chahiye ho sakta hai)."
        return "Defender -> " + out

    def antivirus_scan(self, mode: str = "quick") -> str:
        if not IS_WINDOWS:
            return "Antivirus scan sirf Windows (Defender) pe."
        m = (mode or "quick").lower()
        scan_type = "2" if m in ("full", "deep") else "1"  # 1=Quick, 2=Full
        self._log("AV Scan", m, "security")
        # kick off asynchronously so we don't block; Defender reports in its UI
        out = _ps("Start-MpScan -ScanType %s" % ("FullScan" if scan_type == "2" else "QuickScan"),
                  timeout=20)
        if out.startswith("ERR:"):
            return "Scan start nahi hua: " + out[4:]
        return "Defender %s scan shuru kar diya, %s. (background me chalega)" % (
            m, self.cfg.get("user_name", "P7"))

    # ─────────────────────────────────────────────────────────────────────
    #  Firewall
    # ─────────────────────────────────────────────────────────────────────
    def firewall(self, action: str = "status") -> str:
        if not IS_WINDOWS:
            return "Firewall control abhi sirf Windows pe."
        act = (action or "status").lower()
        if act == "status":
            out = _ps("(Get-NetFirewallProfile | Select-Object Name,Enabled | Format-Table -AutoSize | Out-String)")
            return ("Firewall profiles:\n" + out) if out and not out.startswith("ERR:") \
                else "Firewall status nahi mila."
        if act in ("on", "enable"):
            out = _ps("Set-NetFirewallProfile -All -Enabled True")
            return "Firewall ON kar diya." if not out.startswith("ERR:") else \
                "Firewall on karne ke liye admin chahiye."
        if act in ("off", "disable"):
            out = _ps("Set-NetFirewallProfile -All -Enabled False")
            return "Firewall OFF kar diya (dhyaan rakhna)." if not out.startswith("ERR:") else \
                "Firewall off karne ke liye admin chahiye."
        return "Firewall action: status | on | off"

    # ─────────────────────────────────────────────────────────────────────
    #  Network exposure
    # ─────────────────────────────────────────────────────────────────────
    def open_ports(self, _: str = "") -> str:
        ps = _psutil()
        self._log("Ports", "listening")
        if ps:
            try:
                rows: List[str] = []
                for c in ps.net_connections(kind="inet"):
                    if c.status == "LISTEN":
                        pname = ""
                        if c.pid:
                            try:
                                pname = ps.Process(c.pid).name()
                            except Exception:
                                pname = str(c.pid)
                        laddr = "%s:%s" % (c.laddr.ip, c.laddr.port) if c.laddr else "?"
                        rows.append("%s  <- %s" % (laddr, pname))
                rows = sorted(set(rows))
                return "Listening ports:\n- " + "\n- ".join(rows[:25]) if rows else "Koi listening port nahi."
            except Exception:
                pass
        if IS_WINDOWS:
            out = _ps("Get-NetTCPConnection -State Listen | Select-Object LocalPort,OwningProcess | Sort-Object LocalPort -Unique | Format-Table -AutoSize | Out-String")
            return ("Listening ports:\n" + out) if out else "Ports nahi mile."
        return "Ports dekhne ke liye psutil chahiye (pip install psutil)."

    def connections(self, _: str = "") -> str:
        ps = _psutil()
        if not ps:
            return "Connections ke liye psutil chahiye."
        try:
            ext: List[str] = []
            for c in ps.net_connections(kind="inet"):
                if c.status == "ESTABLISHED" and c.raddr:
                    ip = c.raddr.ip
                    if not (ip.startswith(("127.", "::1", "0.")) or ip == "::"):
                        pname = ""
                        if c.pid:
                            try:
                                pname = ps.Process(c.pid).name()
                            except Exception:
                                pname = str(c.pid)
                        ext.append("%s:%s  (%s)" % (ip, c.raddr.port, pname))
            ext = sorted(set(ext))
            self._log("Connections", "%d external" % len(ext))
            return "Active external connections:\n- " + "\n- ".join(ext[:25]) if ext \
                else "Koi external connection active nahi."
        except Exception as exc:
            return "Error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Suspicious process / autorun heuristics
    # ─────────────────────────────────────────────────────────────────────
    def suspicious(self, _: str = "") -> str:
        ps = _psutil()
        if not ps:
            return "Suspicious scan ke liye psutil chahiye (pip install psutil)."
        self._log("Heuristic scan", "processes")
        findings: List[str] = []
        try:
            for p in ps.process_iter(["name", "exe", "cmdline", "username"]):
                try:
                    name = (p.info.get("name") or "").lower()
                    exe = (p.info.get("exe") or "").lower()
                    cmd = " ".join(p.info.get("cmdline") or []).lower()
                    base = name.replace(".exe", "")

                    # 1) watch-listed interpreter running from a risky dir
                    if base in _WATCH_NAMES and any(d in exe for d in _RISKY_DIRS):
                        findings.append("'%s' risky location se chal raha: %s" % (name, exe))
                    # 2) encoded / hidden PowerShell - classic malware pattern
                    if base in ("powershell", "cmd") and re.search(
                            r"-enc(odedcommand)?\b|-w(indowstyle)? hidden|frombase64string|downloadstring|iex ", cmd):
                        findings.append("Hidden/encoded command: %s" % cmd[:90])
                    # 3) executable directly in a Temp folder
                    if exe.endswith(".exe") and ("\\temp\\" in exe or "/tmp/" in exe):
                        findings.append("Temp se exe: %s" % exe)
                except Exception:
                    continue
        except Exception as exc:
            return "Scan error: " + str(exc)

        findings = sorted(set(findings))
        if not findings:
            return "Saaf lag raha hai, P7 - koi obvious suspicious process nahi mila. (Ye heuristic hai, full scan ke liye Defender chalao.)"
        return ("Dhyaan dene layak %d cheezein milin (zaroori nahi malware ho):\n- "
                % len(findings)) + "\n- ".join(findings[:15])

    # ─────────────────────────────────────────────────────────────────────
    #  Quarantine a flagged file (reversible, local)
    # ─────────────────────────────────────────────────────────────────────
    def quarantine(self, path: str = "") -> str:
        if not path:
            return "Kis file ko quarantine karun? (full path do)"
        src = Path(path)
        if not src.exists():
            return "File nahi mili: " + path
        try:
            self.quarantine_dir.mkdir(parents=True, exist_ok=True)
            dest = self.quarantine_dir / ("%d__%s" % (int(time.time()), src.name))
            shutil.move(str(src), str(dest))
            # best-effort: strip execute perms on non-Windows
            try:
                if not IS_WINDOWS:
                    os.chmod(dest, 0o600)
            except Exception:
                pass
            self._log("Quarantined", src.name, "security")
            return "Quarantine kar diya (locked): %s\nWapas chahiye to %s se nikaal lena." % (
                src.name, self.quarantine_dir)
        except Exception as exc:
            return "Quarantine fail: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Hardening tips (safe, advisory)
    # ─────────────────────────────────────────────────────────────────────
    def harden(self, _: str = "") -> str:
        tips = [
            "Windows Update auto-on rakho.",
            "Defender real-time protection on rakho.",
            "Firewall teeno profiles pe ON rakho.",
            "Admin account roz use mat karo - standard user better hai.",
            "Unknown email attachments / cracked software se bacho.",
            "Important data ka offline backup rakho (ransomware ke against).",
            "BitLocker se drive encrypt karo agar laptop hai.",
        ]
        return "Hardening checklist, P7:\n- " + "\n- ".join(tips)

    # ─────────────────────────────────────────────────────────────────────
    #  Full audit (score + findings)
    # ─────────────────────────────────────────────────────────────────────
    def audit(self, _: str = "") -> str:
        self._log("Security audit", "running")
        lines: List[str] = []
        score = 100

        # Defender / real-time protection
        if IS_WINDOWS:
            d = _ps("$s=Get-MpComputerStatus; ''+$s.RealTimeProtectionEnabled+'|'+$s.AntivirusEnabled")
            if "true" in d.lower():
                lines.append("[OK] Defender + real-time protection ON")
            elif d and not d.startswith("ERR:"):
                lines.append("[RISK] Defender/real-time protection OFF"); score -= 25
            else:
                lines.append("[?] Defender status nahi mila (admin chahiye)")

            # Firewall
            f = _ps("(Get-NetFirewallProfile).Enabled -join ','")
            if f and "false" in f.lower():
                lines.append("[RISK] Koi firewall profile OFF hai"); score -= 20
            elif f and not f.startswith("ERR:"):
                lines.append("[OK] Firewall profiles ON")

            # Windows Update service
            wu = _ps("(Get-Service wuauserv).Status")
            if wu and "running" in wu.lower():
                lines.append("[OK] Windows Update service running")
            elif wu and not wu.startswith("ERR:"):
                lines.append("[WARN] Windows Update service band hai"); score -= 10
        else:
            lines.append("[i] Non-Windows OS - Defender checks skip kiye")

        # Listening ports (exposure)
        ps = _psutil()
        if ps:
            try:
                listen = sum(1 for c in ps.net_connections(kind="inet") if c.status == "LISTEN")
                if listen > 25:
                    lines.append("[WARN] Bahut saare listening ports (%d)" % listen); score -= 10
                else:
                    lines.append("[OK] Listening ports normal (%d)" % listen)
            except Exception:
                pass

        # Heuristic suspicious check
        susp = self.suspicious()
        if susp.startswith("Dhyaan"):
            lines.append("[WARN] Heuristic scan me kuch flags - 'suspicious' command chalao"); score -= 15
        elif susp.startswith("Saaf"):
            lines.append("[OK] Heuristic process scan saaf")

        score = max(0, score)
        verdict = "Strong" if score >= 85 else "Theek-thaak" if score >= 60 else "Kamzor - dhyaan do"
        header = "PHANTRON Security Audit -> Score %d/100 (%s)\n" % (score, verdict)
        return header + "\n".join(lines)

    def status(self) -> Dict[str, bool]:
        return {"windows": IS_WINDOWS, "psutil": _psutil() is not None}
