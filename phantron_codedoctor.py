# -*- coding: utf-8 -*-
"""
PHANTRON CODE DOCTOR  -  auto-refactor & self-healing for the codebase.

Capabilities:
  * analyze(path)              static health check of a source file
                               (syntax/compile, risky patterns, simple metrics)
  * refactor(path, goal)       AI-driven refactor of a file, with a safety net
  * auto_fix(path, error)      fix a file given an error/traceback
  * self_heal(command)         run a command; if it fails, read the traceback,
                               fix the offending file, and retry

Safety net for EVERY write:
  1. the original file is backed up to <name>.bak before any change
  2. for Python files the new content must COMPILE; if it does not, the change
     is rolled back automatically so the doctor never leaves broken code

Decoupled like the other modules: pass in ai_fn(prompt, system) -> str.
Works without an AI for `analyze` (pure static analysis).
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


class CodeDoctor:
    def __init__(self, config=None, ai_fn=None, on_event=None):
        self.config = config or {}
        self.ai_fn = ai_fn
        self.on_event = on_event or (lambda t, d: None)

    def _emit(self, etype, **data):
        try:
            self.on_event(etype, data)
        except Exception:
            pass

    # ── static analysis (no AI needed) ──────────────────────────────────
    def analyze(self, path):
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": "File nahi mili: %s" % path}
        src = p.read_text(encoding="utf-8", errors="replace")
        report = {
            "ok": True, "path": str(p), "lines": src.count("\n") + 1,
            "bytes": len(src.encode("utf-8")), "issues": [], "compiles": None,
        }
        is_py = p.suffix == ".py"
        if is_py:
            ok, err = _py_compiles(src, str(p))
            report["compiles"] = ok
            if not ok:
                report["issues"].append({"severity": "high", "kind": "syntax", "msg": err})
            else:
                report["issues"].extend(_py_lint(src))
        # language-agnostic smells
        for i, line in enumerate(src.splitlines(), 1):
            if len(line) > 120:
                report["issues"].append({"severity": "low", "kind": "long_line",
                                         "line": i, "msg": "Line %d bahut lambi (%d chars)" % (i, len(line))})
            if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", line):
                report["issues"].append({"severity": "info", "kind": "todo",
                                         "line": i, "msg": line.strip()[:80]})
        report["issue_count"] = len(report["issues"])
        self._emit("analyze", message="%s -> %d issue(s), compiles=%s" % (
            p.name, report["issue_count"], report["compiles"]))
        return report

    def analyze_summary(self, path):
        r = self.analyze(path)
        if not r.get("ok"):
            return r.get("error", "Analyze fail")
        sev = {}
        for it in r["issues"]:
            sev[it["severity"]] = sev.get(it["severity"], 0) + 1
        head = "%s: %d lines, compiles=%s, %d issue(s)." % (
            Path(path).name, r["lines"], r["compiles"], r["issue_count"])
        if sev:
            head += " (" + ", ".join("%s:%d" % (k, v) for k, v in sev.items()) + ")"
        top = [it["msg"] for it in r["issues"][:5]]
        return head + ("\n- " + "\n- ".join(top) if top else "")

    # ── AI refactor with safety net ─────────────────────────────────────
    def refactor(self, path, goal):
        return self._ai_rewrite(path, intent=goal or "code ko saaf aur readable banao",
                                mode="refactor")

    def auto_fix(self, path, error=""):
        return self._ai_rewrite(path, intent="is error/bug ko fix karo: " + (error or "unknown error"),
                                mode="fix")

    def _ai_rewrite(self, path, intent, mode):
        if not self.ai_fn:
            return "AI brain off hai - %s ke liye Ollama/Claude chahiye." % mode
        p = Path(path)
        if not p.exists():
            return "File nahi mili: %s" % path
        original = p.read_text(encoding="utf-8", errors="replace")
        lang = _lang_of(p)
        system = (
            "You are an expert %s engineer. Rewrite the file to satisfy the request. "
            "Output ONLY the complete, final file contents - no markdown fences, no "
            "explanation, no commentary. Preserve behavior unless the request says "
            "otherwise. Keep imports valid." % lang
        )
        prompt = "REQUEST (%s): %s\n\n--- CURRENT FILE: %s ---\n%s" % (
            mode, intent, p.name, original)
        self._emit("working", message="%s: %s" % (mode, p.name))
        out = self.ai_fn(prompt, system)
        if not out or str(out).startswith("__ERR__"):
            return "AI se output nahi mila (brain offline?). Kuch change nahi kiya."
        new_src = _strip_fences(out)
        if not new_src.strip():
            return "AI ne khaali output diya, kuch change nahi kiya."

        # validate before touching disk (Python must compile)
        if p.suffix == ".py":
            ok, err = _py_compiles(new_src, str(p))
            if not ok:
                self._emit("rejected", message="Naya code compile nahi hua, rollback. " + err[:80])
                return ("Naya version compile nahi hua, isliye apply NAHI kiya (safe). "
                        "Error: " + err[:120])

        backup = self.backup(p)
        p.write_text(new_src, encoding="utf-8")
        self._emit("applied", message="%s -> updated (backup: %s)" % (p.name, Path(backup).name))
        return ("%s ho gaya P7: %s. Backup: %s. "
                "(Python compile-check pass.)" % (mode, p.name, Path(backup).name))

    # ── self-healing: run -> fail -> fix -> retry ───────────────────────
    def self_heal(self, command, cwd=None, max_attempts=None):
        max_attempts = int(max_attempts or self.config.get("agent_max_fix_attempts", 3))
        attempts = []
        for attempt in range(1, max_attempts + 1):
            ok, output = _run(command, cwd)
            attempts.append({"attempt": attempt, "ok": ok, "output": output[:400]})
            self._emit("run", message="Attempt %d: %s" % (attempt, "OK" if ok else "FAIL"))
            if ok:
                return {"ok": True, "attempts": attempts,
                        "summary": "Command pass ho gaya (attempt %d) P7." % attempt}
            target = _file_from_traceback(output, cwd)
            if not target:
                return {"ok": False, "attempts": attempts,
                        "summary": "Fail hua par traceback me koi fixable file nahi mili.",
                        "output": output[:600]}
            if not self.ai_fn:
                return {"ok": False, "attempts": attempts,
                        "summary": "Fix ke liye AI brain chahiye (Ollama/Claude).",
                        "output": output[:600]}
            self._emit("fixing", message="%s ko fix kar raha hoon (attempt %d)" % (
                Path(target).name, attempt))
            self.auto_fix(target, error=output[-1200:])
        return {"ok": False, "attempts": attempts,
                "summary": "%d koshish ke baad bhi fix nahi hua P7." % max_attempts}

    # ── utilities ───────────────────────────────────────────────────────
    def backup(self, path):
        p = Path(path)
        bak = p.with_suffix(p.suffix + ".bak")
        try:
            shutil.copy2(p, bak)
        except Exception:
            pass
        return str(bak)

    def restore(self, path):
        p = Path(path)
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            shutil.copy2(bak, p)
            return "Restore ho gaya: " + p.name
        return "Backup nahi mila: " + bak.name


# ── module-level helpers ────────────────────────────────────────────────
def _py_compiles(src, filename="<string>"):
    try:
        compile(src, filename, "exec")
        return True, ""
    except SyntaxError as e:
        return False, "SyntaxError line %s: %s" % (e.lineno, e.msg)
    except Exception as e:
        return False, str(e)


def _py_lint(src):
    """Very small AST-based smell detector (no external deps)."""
    issues = []
    try:
        tree = ast.parse(src)
    except Exception:
        return issues
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({"severity": "medium", "kind": "bare_except",
                           "line": node.lineno, "msg": "Line %d: bare 'except:' - specific exception pakdo" % node.lineno})
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if isinstance(op, (ast.Is, ast.IsNot)):
                    for cmp in node.comparators:
                        if isinstance(cmp, ast.Constant) and isinstance(cmp.value, (int, str)):
                            issues.append({"severity": "low", "kind": "is_literal",
                                           "line": getattr(node, "lineno", 0),
                                           "msg": "Line %d: 'is' ke saath literal - '==' use karo" % getattr(node, "lineno", 0)})
    return issues


def _lang_of(p):
    return {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".html": "HTML", ".css": "CSS", ".java": "Java", ".cpp": "C++",
        ".c": "C", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    }.get(Path(p).suffix.lower(), "source-code")


def _strip_fences(text):
    t = str(text).strip()
    t = re.sub(r"^```[a-zA-Z0-9_+-]*\n?", "", t)
    t = re.sub(r"\n?```$", "", t)
    return t.strip("\n")


def _run(command, cwd=None):
    try:
        if isinstance(command, str):
            args = command
            shell = True
        else:
            args = command
            shell = False
        r = subprocess.run(args, shell=shell, cwd=cwd, capture_output=True,
                           text=True, timeout=120, encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Command timeout."
    except Exception as e:
        return False, "".join(traceback.format_exception_only(type(e), e))


def _file_from_traceback(output, cwd=None):
    """Pull the last user file path out of a Python traceback, if any."""
    candidates = re.findall(r'File "([^"]+\.py)", line \d+', output or "")
    # prefer files that exist and are not in the stdlib/site-packages
    for path in reversed(candidates):
        low = path.lower()
        if "site-packages" in low or "lib" + os.sep + "python" in low:
            continue
        if os.path.exists(path):
            return path
        if cwd and os.path.exists(os.path.join(cwd, path)):
            return os.path.join(cwd, path)
    return candidates[-1] if candidates and os.path.exists(candidates[-1]) else None


if __name__ == "__main__":
    doc = CodeDoctor(on_event=lambda t, d: print("  [%s] %s" % (t, d.get("message", ""))))
    target = sys.argv[1] if len(sys.argv) > 1 else __file__
    print(doc.analyze_summary(target))
