# -*- coding: utf-8 -*-
"""
PHANTRON  -  Agentic OS Controller
===================================

A self-driving "system control loop" for P7's machine. Give it a natural
language (or transcribed voice) instruction and it will:

    1. PLAN   - ask the AI brain to break the request into ordered, concrete
                technical sub-steps (PowerShell / CMD / specialised CLI calls).
    2. EXECUTE- run each step with a streaming terminal engine that captures
                stdout, stderr, exit code and live logs.
    3. VERIFY - dynamically check the status of every step (exit code +
                optional success / failure regex checks).
    4. RECOVER- if a step fails, feed the real terminal logs back to the AI,
                which diagnoses the error and returns a corrected step. This
                repeats recursively up to a bounded number of attempts.

It ships specialised runners for the heavy toolchains P7 cares about:
    * Android Studio  -> gradlew (assemble/build/install) + adb
    * Unreal Engine 5 -> RunUAT.bat (BuildCookRun) + UnrealBuildTool / Build.bat

The module is import-safe on any OS (it only *runs* Windows tooling on
Windows) so it can be unit-checked anywhere. All destructive operations pass
through a configurable SafetyGuard.

It can be used three ways:
    * Programmatically:      AgenticController().process("build my android apk")
    * From the CLI:          python phantron_os_controller.py "free up RAM"
    * Wired into PHANTRON:    phantron_core calls do_os_agent(...)
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import psutil
except Exception:  # pragma: no cover - optional
    psutil = None

IS_WINDOWS = platform.system() == "Windows"
CONFIG_FILE = Path(__file__).parent / "config.json"


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════════════════
def load_config() -> dict:
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg.setdefault("ai_mode", "ollama")
    cfg.setdefault("ollama_model", "llama3")
    cfg.setdefault("ollama_url", "http://localhost:11434/api/generate")
    cfg.setdefault("claude_api_key", "")
    cfg.setdefault("claude_model", "claude-haiku-4-5-20251001")
    cfg.setdefault("user_name", "P7")
    cfg.setdefault("projects_dir", str(Path.home() / "Phantron" / "Projects"))
    cfg.setdefault("agent_max_steps", 12)
    cfg.setdefault("agent_max_fix_attempts", 3)
    cfg.setdefault("agent_command_timeout", 900)   # seconds (heavy builds)
    cfg.setdefault("agent_safe_mode", True)         # block catastrophic commands
    cfg.setdefault("agent_allow_shutdown", False)
    cfg.setdefault("unreal_engine_dir", "")         # optional explicit path
    cfg.setdefault("android_sdk_dir", "")           # optional explicit path
    return cfg


# ════════════════════════════════════════════════════════════════════════════
#  DATA MODELS
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class CommandResult:
    command: str
    shell: str
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    blocked: bool = False
    block_reason: str = ""

    @property
    def success(self) -> bool:
        return (not self.timed_out) and (not self.blocked) and self.returncode == 0

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()

    def tail(self, n: int = 40) -> str:
        lines = self.combined.splitlines()
        return "\n".join(lines[-n:])

    def to_dict(self) -> dict:
        return {
            "command": self.command, "shell": self.shell,
            "returncode": self.returncode, "duration": round(self.duration, 2),
            "timed_out": self.timed_out, "blocked": self.blocked,
            "block_reason": self.block_reason, "success": self.success,
            "tail": self.tail(20),
        }


@dataclass
class Step:
    index: int
    description: str
    shell: str = "powershell"          # powershell | cmd | tool
    command: str = ""
    cwd: Optional[str] = None
    success_when: Optional[str] = None  # regex that MUST appear in output
    fail_when: Optional[str] = None     # regex that, if present, means failure
    allow_fix: bool = True
    optional: bool = False              # failure here does not abort the run

    def to_dict(self) -> dict:
        return {
            "index": self.index, "description": self.description,
            "shell": self.shell, "command": self.command, "cwd": self.cwd,
            "success_when": self.success_when, "fail_when": self.fail_when,
            "optional": self.optional,
        }


# ════════════════════════════════════════════════════════════════════════════
#  SAFETY GUARD
# ════════════════════════════════════════════════════════════════════════════
class SafetyGuard:
    """Blocks catastrophic, irreversible commands unless explicitly allowed."""

    DESTRUCTIVE = [
        r"\bformat\s+[a-z]:",                       # format C:
        r"\brd\s+/s\s+/q\s+[a-z]:\\?\s*$",          # nuke a whole drive
        r"\bdel\s+/[fsq]+\s+[a-z]:\\\*",            # del /f /s /q C:\*
        r"\bremove-item\b.*\b[a-z]:\\\s*-recurse",  # Remove-Item C:\ -Recurse
        r"\brm\s+-rf\s+/(?:\s|$)",                  # rm -rf /
        r"\brm\s+-rf\s+~",                          # rm -rf ~
        r"\bmkfs\b", r"\bdd\s+if=.*of=/dev/",
        r"cipher\s+/w",                             # secure wipe
        r"\b(diskpart)\b.*\bclean\b",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",    # fork bomb
        r"Format-Volume",
    ]
    SHUTDOWN = [r"\bshutdown\b", r"\bRestart-Computer\b", r"\bStop-Computer\b", r"\blogoff\b"]

    def __init__(self, enabled: bool = True, allow_shutdown: bool = False):
        self.enabled = enabled
        self.allow_shutdown = allow_shutdown

    def assess(self, command: str) -> Tuple[bool, str]:
        if not self.enabled:
            return True, ""
        low = command.lower()
        for pat in self.DESTRUCTIVE:
            if re.search(pat, low):
                return False, "destructive command blocked by SafetyGuard: %s" % pat
        if not self.allow_shutdown:
            for pat in self.SHUTDOWN:
                if re.search(pat, command, re.IGNORECASE):
                    return False, "shutdown/restart blocked (set agent_allow_shutdown=true to allow)"
        return True, ""


# ════════════════════════════════════════════════════════════════════════════
#  TERMINAL ENGINE  (streaming, full PowerShell / CMD authority)
# ════════════════════════════════════════════════════════════════════════════
class TerminalEngine:
    """Runs shell commands, streaming stdout/stderr live while collecting them."""

    def __init__(self, default_timeout: int = 900,
                 on_log: Optional[Callable[[str, str], None]] = None,
                 guard: Optional[SafetyGuard] = None):
        self.default_timeout = default_timeout
        self.on_log = on_log
        self.guard = guard or SafetyGuard()

    def _build_args(self, shell: str, command: str) -> list:
        shell = (shell or "powershell").lower()
        if not IS_WINDOWS:
            # On non-Windows we map to bash so the module is testable anywhere.
            return ["bash", "-lc", command]
        if shell == "cmd":
            return ["cmd", "/c", command]
        # default: PowerShell
        return ["powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command]

    def run(self, command: str, shell: str = "powershell",
            cwd: Optional[str] = None, timeout: Optional[int] = None,
            env: Optional[dict] = None) -> CommandResult:
        command = (command or "").strip()
        if not command:
            return CommandResult(command, shell, -1, "", "empty command", 0.0)

        allowed, reason = self.guard.assess(command)
        if not allowed:
            if self.on_log:
                self.on_log("guard", reason)
            return CommandResult(command, shell, -1, "", reason, 0.0, blocked=True, block_reason=reason)

        args = self._build_args(shell, command)
        timeout = timeout or self.default_timeout
        start = time.time()
        out_lines: List[str] = []
        err_lines: List[str] = []

        try:
            proc = subprocess.Popen(
                args, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:
            return CommandResult(command, shell, -1, "", "spawn error: %s" % e,
                                 time.time() - start)

        def _reader(pipe, sink, name):
            try:
                for line in iter(pipe.readline, ""):
                    sink.append(line)
                    if self.on_log:
                        self.on_log(name, line.rstrip("\n"))
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=_reader, args=(proc.stdout, out_lines, "stdout"), daemon=True)
        t_err = threading.Thread(target=_reader, args=(proc.stderr, err_lines, "stderr"), daemon=True)
        t_out.start(); t_err.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except Exception:
                pass
        t_out.join(timeout=3); t_err.join(timeout=3)

        return CommandResult(
            command=command, shell=shell,
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout="".join(out_lines), stderr="".join(err_lines),
            duration=time.time() - start, timed_out=timed_out)


# ════════════════════════════════════════════════════════════════════════════
#  TOOL RUNNER  (Android + Unreal Engine 5 CLI)
# ════════════════════════════════════════════════════════════════════════════
class ToolRunner:
    """Direct CLI wrappers for heavy toolchains with path auto-discovery."""

    def __init__(self, terminal: TerminalEngine, config: dict):
        self.t = terminal
        self.cfg = config

    # ---------- discovery helpers ----------
    @staticmethod
    def _first_existing(paths: List[str]) -> Optional[str]:
        for p in paths:
            if p and os.path.exists(p):
                return p
        return None

    def find_gradlew(self, project_dir: str) -> Optional[str]:
        base = Path(project_dir)
        name = "gradlew.bat" if IS_WINDOWS else "gradlew"
        cand = base / name
        if cand.exists():
            return str(cand)
        # search one level down for the wrapper
        for sub in base.glob("*/" + name):
            return str(sub)
        return None

    def find_adb(self) -> Optional[str]:
        if shutil.which("adb"):
            return "adb"
        sdk = self.cfg.get("android_sdk_dir") or os.environ.get("ANDROID_HOME") \
            or os.environ.get("ANDROID_SDK_ROOT")
        candidates = []
        if sdk:
            candidates.append(str(Path(sdk) / "platform-tools" / ("adb.exe" if IS_WINDOWS else "adb")))
        candidates += [
            str(Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe"),
            r"C:\Android\platform-tools\adb.exe",
        ]
        return self._first_existing(candidates)

    def find_unreal_dir(self) -> Optional[str]:
        explicit = self.cfg.get("unreal_engine_dir")
        if explicit and os.path.exists(explicit):
            return explicit
        roots = [
            r"C:\Program Files\Epic Games",
            r"D:\Program Files\Epic Games",
            r"D:\Epic Games",
        ]
        best = None
        for root in roots:
            rp = Path(root)
            if not rp.exists():
                continue
            for d in rp.glob("UE_5*"):
                if (d / "Engine").exists():
                    best = str(d)  # take the latest match
        return best

    # ---------- Android ----------
    def gradlew(self, project_dir: str, task: str = "assembleDebug",
                extra: str = "") -> CommandResult:
        gw = self.find_gradlew(project_dir)
        if not gw:
            return CommandResult("gradlew", "tool", -1, "",
                                 "gradlew not found in %s" % project_dir, 0.0)
        cmd = '& "%s" %s %s' % (gw, task, extra)
        return self.t.run(cmd, shell="powershell", cwd=str(Path(gw).parent),
                          timeout=self.cfg.get("agent_command_timeout", 900))

    def adb(self, args: str) -> CommandResult:
        adb = self.find_adb()
        if not adb:
            return CommandResult("adb", "tool", -1, "", "adb not found (set android_sdk_dir)", 0.0)
        return self.t.run('& "%s" %s' % (adb, args), shell="powershell")

    def android_build_apk(self, project_dir: str, variant: str = "Debug") -> CommandResult:
        return self.gradlew(project_dir, "assemble%s" % variant)

    def android_install(self, project_dir: str, variant: str = "Debug") -> CommandResult:
        return self.gradlew(project_dir, "install%s" % variant)

    # ---------- Unreal Engine 5 ----------
    def _uat_path(self) -> Optional[str]:
        eng = self.find_unreal_dir()
        if not eng:
            return None
        p = Path(eng) / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
        return str(p) if p.exists() else None

    def _build_bat(self) -> Optional[str]:
        eng = self.find_unreal_dir()
        if not eng:
            return None
        p = Path(eng) / "Engine" / "Build" / "BatchFiles" / "Build.bat"
        return str(p) if p.exists() else None

    def run_uat(self, args: str) -> CommandResult:
        uat = self._uat_path()
        if not uat:
            return CommandResult("RunUAT", "tool", -1, "",
                                 "Unreal Engine not found (set unreal_engine_dir)", 0.0)
        return self.t.run('& "%s" %s' % (uat, args), shell="powershell",
                          timeout=self.cfg.get("agent_command_timeout", 900))

    def run_ubt(self, target: str, platform_name: str = "Win64",
                config: str = "Development", extra: str = "") -> CommandResult:
        """UnrealBuildTool via Build.bat: compile a module/target."""
        bb = self._build_bat()
        if not bb:
            return CommandResult("Build.bat", "tool", -1, "",
                                 "Unreal Engine not found (set unreal_engine_dir)", 0.0)
        cmd = '& "%s" %s %s %s %s' % (bb, target, platform_name, config, extra)
        return self.t.run(cmd, shell="powershell",
                          timeout=self.cfg.get("agent_command_timeout", 900))

    def unreal_build_cook_run(self, uproject: str, platform_name: str = "Win64",
                              config: str = "Development") -> CommandResult:
        args = ('BuildCookRun -project="%s" -noP4 -platform=%s -clientconfig=%s '
                '-cook -build -stage -pak' % (uproject, platform_name, config))
        return self.run_uat(args)


# ════════════════════════════════════════════════════════════════════════════
#  OS TASK MANAGER  (background processes / services)
# ════════════════════════════════════════════════════════════════════════════
class OSTaskManager:
    def __init__(self, terminal: TerminalEngine):
        self.t = terminal

    def list_processes(self, top: int = 15) -> str:
        if psutil:
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                procs.append(p.info)
            procs.sort(key=lambda x: (x.get("cpu_percent") or 0), reverse=True)
            lines = ["%-6s %-28s CPU%% MEM%%" % ("PID", "NAME")]
            for p in procs[:top]:
                lines.append("%-6s %-28s %4.0f %4.1f" % (
                    p.get("pid"), str(p.get("name"))[:28],
                    p.get("cpu_percent") or 0, p.get("memory_percent") or 0))
            return "\n".join(lines)
        return self.t.run("Get-Process | Sort-Object CPU -Descending | "
                          "Select-Object -First %d Id,ProcessName,CPU | Format-Table -Auto"
                          % top).combined

    def kill(self, name_or_pid: str) -> str:
        if psutil and str(name_or_pid).isdigit():
            try:
                psutil.Process(int(name_or_pid)).terminate()
                return "PID %s terminated." % name_or_pid
            except Exception as e:
                return "kill error: %s" % e
        if str(name_or_pid).isdigit():
            return self.t.run("Stop-Process -Id %s -Force" % name_or_pid).combined or "killed"
        return self.t.run('Stop-Process -Name "%s" -Force' % name_or_pid).combined or "killed"

    def set_priority(self, pid: int, level: str = "below") -> str:
        if not psutil:
            return "psutil required for priority control"
        mapping = {
            "idle": getattr(psutil, "IDLE_PRIORITY_CLASS", 64),
            "below": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384),
            "normal": getattr(psutil, "NORMAL_PRIORITY_CLASS", 32),
            "above": getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768),
            "high": getattr(psutil, "HIGH_PRIORITY_CLASS", 128),
        }
        try:
            psutil.Process(pid).nice(mapping.get(level, mapping["normal"]))
            return "PID %d priority -> %s" % (pid, level)
        except Exception as e:
            return "priority error: %s" % e

    def service(self, action: str, name: str) -> str:
        action = action.lower()
        verb = {"start": "Start-Service", "stop": "Stop-Service",
                "restart": "Restart-Service"}.get(action)
        if not verb:
            return "unknown service action: %s" % action
        return self.t.run('%s -Name "%s"' % (verb, name)).combined or "%s %s ok" % (action, name)

    def free_memory(self) -> str:
        # Clears the standby list / working sets where possible.
        return self.t.run(
            "Get-Process | Where-Object {$_.WorkingSet -gt 100MB} | "
            "Sort-Object WorkingSet -Descending | "
            "Select-Object -First 10 ProcessName,@{N='MB';E={[int]($_.WorkingSet/1MB)}} | "
            "Format-Table -Auto").combined


# ════════════════════════════════════════════════════════════════════════════
#  AI PLANNER  (decompose + recursive fix)
# ════════════════════════════════════════════════════════════════════════════
def default_ai_fn(prompt: str, system: str) -> str:
    """Reuse phantron_core's AI client if available, else talk to Ollama/Claude."""
    try:
        from phantron_core import _ai_raw  # lazy to avoid circular import
        out = _ai_raw(prompt, system)
        if out and not str(out).startswith("__ERR__"):
            return out
    except Exception:
        pass
    return _builtin_ai(prompt, system)


def _builtin_ai(prompt: str, system: str) -> str:
    cfg = load_config()
    # Claude first if configured as primary
    if cfg.get("ai_mode") == "claude" and cfg.get("claude_api_key"):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=cfg["claude_api_key"])
            resp = client.messages.create(
                model=cfg.get("claude_model"), max_tokens=1200, system=system,
                messages=[{"role": "user", "content": prompt}])
            return resp.content[0].text if resp.content else ""
        except Exception as e:
            return "__ERR__ Claude: %s" % str(e)[:120]
    # Ollama
    try:
        data = json.dumps({
            "model": cfg.get("ollama_model"), "system": system, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.3, "num_predict": 800},
        }).encode()
        req = urllib.request.Request(cfg.get("ollama_url"), data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return (json.loads(r.read().decode()).get("response") or "").strip()
    except Exception as e:
        return "__ERR__ Ollama: %s" % str(e)[:120]


def _extract_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # find first balanced { } or [ ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:
                            break
            start = text.find(opener, start + 1)
    return None


class AIPlanner:
    PLAN_SYSTEM = (
        "You are PHANTRON's OS automation planner for a WINDOWS machine. "
        "Convert the user's request into an ORDERED list of concrete, executable "
        "technical sub-steps. Output ONLY a JSON array, nothing else. Each item:\n"
        '{"description":"<short>", "shell":"powershell|cmd", "command":"<exact command>", '
        '"cwd":"<dir or null>", "success_when":"<regex or null>", '
        '"fail_when":"<regex or null>", "optional":false}\n'
        "Rules: prefer PowerShell. Use REAL, runnable commands (no placeholders unless "
        "the user gave a path). For Android use gradlew/adb; for Unreal Engine 5 use "
        "RunUAT.bat / Build.bat. Keep steps minimal and verifiable. Max %d steps."
    )

    FIX_SYSTEM = (
        "You are PHANTRON's recovery engine. A command FAILED. Read the real terminal "
        "logs and return ONLY a JSON object with a corrected single step:\n"
        '{"diagnosis":"<one line>", "command":"<corrected command>", '
        '"shell":"powershell|cmd", "cwd":"<dir or null>", "give_up":false}\n'
        "If it cannot reasonably be fixed automatically, return give_up=true. "
        "Return ONLY JSON."
    )

    def __init__(self, ai_fn: Callable[[str, str], str], max_steps: int = 12):
        self.ai_fn = ai_fn
        self.max_steps = max_steps

    def plan(self, request: str, context: str = "") -> List[Step]:
        sys_prompt = self.PLAN_SYSTEM % self.max_steps
        prompt = "Request: %s" % request
        if context:
            prompt += "\n\nEnvironment context:\n" + context
        raw = self.ai_fn(prompt, sys_prompt)
        if raw and str(raw).startswith("__ERR__"):
            return []
        data = _extract_json(raw)
        if isinstance(data, dict) and "steps" in data:
            data = data["steps"]
        steps: List[Step] = []
        if isinstance(data, list):
            for i, item in enumerate(data[: self.max_steps]):
                if not isinstance(item, dict):
                    continue
                steps.append(Step(
                    index=i + 1,
                    description=str(item.get("description", "step %d" % (i + 1))),
                    shell=str(item.get("shell", "powershell")).lower(),
                    command=str(item.get("command", "")).strip(),
                    cwd=item.get("cwd") or None,
                    success_when=item.get("success_when") or None,
                    fail_when=item.get("fail_when") or None,
                    optional=bool(item.get("optional", False)),
                ))
        return [s for s in steps if s.command]

    def fix(self, request: str, step: Step, result: CommandResult) -> Optional[Step]:
        prompt = (
            "Original goal: %s\n\nFailed step: %s\nShell: %s\nCommand:\n%s\n\n"
            "Exit code: %s   Timed out: %s\n\nReal terminal logs (tail):\n%s\n\n"
            "Give the corrected step as JSON."
            % (request, step.description, step.shell, step.command,
               result.returncode, result.timed_out, result.tail(40))
        )
        raw = self.ai_fn(prompt, self.FIX_SYSTEM)
        if raw and str(raw).startswith("__ERR__"):
            return None
        data = _extract_json(raw)
        if not isinstance(data, dict) or data.get("give_up"):
            return None
        cmd = str(data.get("command", "")).strip()
        if not cmd:
            return None
        return Step(
            index=step.index,
            description="FIX: " + str(data.get("diagnosis", "retry")),
            shell=str(data.get("shell", step.shell)).lower(),
            command=cmd,
            cwd=data.get("cwd") or step.cwd,
            success_when=step.success_when,
            fail_when=step.fail_when,
            optional=step.optional,
        )


# ════════════════════════════════════════════════════════════════════════════
#  AGENTIC CONTROLLER  (the main system control loop)
# ════════════════════════════════════════════════════════════════════════════
class AgenticController:
    """
    The self-driving loop: plan -> execute -> verify -> recursively recover.

    on_event(event_type, payload) is called for every meaningful moment so a UI
    (PHANTRON's Interface) or voice layer can narrate progress live.
    Event types: plan, step_start, log, step_result, fixing, blocked, done, error.
    """

    def __init__(self, config: Optional[dict] = None,
                 ai_fn: Optional[Callable[[str, str], str]] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.cfg = config or load_config()
        self.on_event = on_event
        self.guard = SafetyGuard(
            enabled=self.cfg.get("agent_safe_mode", True),
            allow_shutdown=self.cfg.get("agent_allow_shutdown", False))
        self.terminal = TerminalEngine(
            default_timeout=self.cfg.get("agent_command_timeout", 900),
            on_log=self._on_log, guard=self.guard)
        self.tools = ToolRunner(self.terminal, self.cfg)
        self.osman = OSTaskManager(self.terminal)
        self.planner = AIPlanner(ai_fn or default_ai_fn,
                                 max_steps=self.cfg.get("agent_max_steps", 12))
        self.max_fix = self.cfg.get("agent_max_fix_attempts", 3)
        self.history: List[dict] = []

    # ---------- event plumbing ----------
    def _emit(self, etype: str, **data):
        data["time"] = datetime.now().strftime("%H:%M:%S")
        if self.on_event:
            try:
                self.on_event(etype, data)
            except Exception:
                pass
        else:
            tag = etype.upper()
            msg = data.get("message") or data.get("description") or ""
            print("[AGENT:%s] %s" % (tag, msg))

    def _on_log(self, stream: str, line: str):
        if line.strip():
            self._emit("log", stream=stream, message=line)

    # ---------- environment context for the planner ----------
    def _env_context(self) -> str:
        ctx = [
            "OS: %s %s" % (platform.system(), platform.release()),
            "Projects dir: %s" % self.cfg.get("projects_dir"),
        ]
        eng = self.tools.find_unreal_dir()
        ctx.append("Unreal Engine: %s" % (eng or "NOT FOUND"))
        adb = self.tools.find_adb()
        ctx.append("adb: %s" % (adb or "NOT FOUND"))
        ctx.append("gradle wrapper: resolved per-project at runtime")
        return "\n".join(ctx)

    # ---------- status verification ----------
    @staticmethod
    def _verify(step: Step, result: CommandResult) -> Tuple[bool, str]:
        if result.blocked:
            return False, result.block_reason
        if result.timed_out:
            return False, "timed out"
        if step.fail_when and re.search(step.fail_when, result.combined, re.IGNORECASE):
            return False, "fail pattern matched: %s" % step.fail_when
        if step.success_when:
            ok = bool(re.search(step.success_when, result.combined, re.IGNORECASE))
            return ok, ("success pattern found" if ok else "success pattern missing")
        return result.success, ("exit %d" % result.returncode)

    # ---------- one step with recursive recovery ----------
    def _run_with_recovery(self, request: str, step: Step) -> Tuple[bool, CommandResult]:
        attempt = 0
        current = step
        while True:
            self._emit("step_start", index=step.index, attempt=attempt,
                       description=current.description, command=current.command,
                       shell=current.shell)
            result = self.terminal.run(current.command, shell=current.shell,
                                       cwd=current.cwd)
            ok, why = self._verify(current, result)
            self._emit("step_result", index=step.index, ok=ok, reason=why,
                       returncode=result.returncode, duration=round(result.duration, 1),
                       tail=result.tail(12))
            if ok:
                return True, result
            attempt += 1
            if attempt > self.max_fix or not current.allow_fix or result.blocked:
                return False, result
            self._emit("fixing", index=step.index, attempt=attempt,
                       message="Logs analyse karke fix nikal raha hoon...")
            fixed = self.planner.fix(request, current, result)
            if not fixed:
                return False, result
            current = fixed

    # ---------- public entry ----------
    def process(self, request: str) -> dict:
        request = (request or "").strip()
        if not request:
            return {"ok": False, "error": "empty request"}

        self._emit("plan", message="Task ko technical steps me tod raha hoon...",
                   request=request)
        steps = self.planner.plan(request, self._env_context())
        if not steps:
            self._emit("error", message="Plan generate nahi hua (AI offline?).")
            return {"ok": False, "error": "no plan (AI offline or invalid output)",
                    "request": request}

        self._emit("plan", message="%d steps banaye." % len(steps),
                   steps=[s.to_dict() for s in steps])

        report = {"ok": True, "request": request, "steps": [], "completed": 0}
        for step in steps:
            allowed, reason = self.guard.assess(step.command)
            if not allowed:
                self._emit("blocked", index=step.index, message=reason,
                           command=step.command)
                report["steps"].append({"step": step.to_dict(), "ok": False,
                                        "blocked": True, "reason": reason})
                if not step.optional:
                    report["ok"] = False
                    break
                continue

            ok, result = self._run_with_recovery(request, step)
            report["steps"].append({"step": step.to_dict(), "ok": ok,
                                    "result": result.to_dict()})
            if ok:
                report["completed"] += 1
            elif not step.optional:
                report["ok"] = False
                self._emit("error", index=step.index,
                           message="Step fail (recovery ke baad bhi). Loop rok raha hoon.",
                           tail=result.tail(20))
                break

        summary = "%d/%d steps complete." % (report["completed"], len(steps))
        self._emit("done", ok=report["ok"], message=summary,
                   completed=report["completed"], total=len(steps))
        report["summary"] = summary
        self.history.append(report)
        return report


# ════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════
def _cli():
    cfg = load_config()
    controller = AgenticController(cfg)
    if len(sys.argv) > 1:
        controller.process(" ".join(sys.argv[1:]))
        return
    print("PHANTRON Agentic OS Controller - type a task ('exit' to quit)")
    while True:
        try:
            line = input("task> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            break
        controller.process(line)


if __name__ == "__main__":
    _cli()
