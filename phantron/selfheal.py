# -*- coding: utf-8 -*-
"""
PHANTRON self-healing - detect errors and repair code automatically.

Two jobs:

  1. guard(label, fn, *args)  - run any internal callable inside a safety net.
     If it raises, the traceback is logged (and optionally sent to the brain
     for a plain-language explanation) instead of crashing PHANTRON. This is
     what makes the assistant resilient: one broken skill never takes down the
     whole system.

  2. heal_file(path, error)   - ask the brain to FIX a Python file. The flow is
     deliberately careful and reversible:
        a) read the current source,
        b) if no error was given, compile it to discover the real error,
        c) ask the brain for a corrected full-file version,
        d) verify the candidate actually compiles,
        e) back up the original, then write the fix.
     If the candidate doesn't compile, nothing is changed.

The brain is optional. Without it, guard() still protects against crashes and
heal_file() reports the precise syntax error so P7 can fix it manually.
"""

from __future__ import annotations

import py_compile
import shutil
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, List, Optional

from .brain import Brain, ERR


_HEAL_SYSTEM = (
    "You are a senior Python engineer fixing a single file for the PHANTRON "
    "project. You will be given the file's current contents and an error. "
    "Return ONLY the complete corrected file contents - no markdown fences, no "
    "explanation, no commentary. Preserve the original style, behaviour and all "
    "working code; change the minimum needed to fix the error."
)


class SelfHeal:
    def __init__(self, cfg, brain: Optional[Brain] = None,
                 on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.brain = brain
        self.on_event = on_event
        self.error_log: List[str] = []

    def _log(self, action: str, detail: str = "", kind: str = "heal"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  Crash guard
    # ─────────────────────────────────────────────────────────────────────
    def guard(self, label: str, fn: Callable, *args, **kwargs) -> Any:
        """Run fn safely; on error, log it and return a friendly message."""
        try:
            return fn(*args, **kwargs)
        except Exception:
            tb = traceback.format_exc()
            self.record(label, tb)
            self._log("Recovered", "%s: %s" % (label, tb.strip().splitlines()[-1][:80]), "error")
            return "'%s' me dikkat aayi par main chaalu hoon, %s. (self-heal log me note kar liya)" % (
                label, self.cfg.get("user_name", "P7"))

    def record(self, label: str, tb: str):
        entry = "[%s] %s" % (label, tb.strip().splitlines()[-1] if tb.strip() else tb)
        self.error_log.append(entry)
        if len(self.error_log) > 50:
            self.error_log = self.error_log[-50:]
        # best-effort: persist to a log file under the Phantron folder
        try:
            logp = self.cfg.files_dir().parent / "selfheal.log"
            with open(logp, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass

    def explain_last(self) -> str:
        """Plain-language explanation of the most recent error (uses brain)."""
        if not self.error_log:
            return "Abhi tak koi error record nahi hua, %s." % self.cfg.get("user_name", "P7")
        last = self.error_log[-1]
        if not self.brain or not self.brain.online:
            return "Last error: " + last
        out = self.brain.ask(
            "Explain this Python error to a developer in 2-3 short lines, in Hinglish, "
            "and suggest the likely fix.",
            last,
        )
        if out.startswith(ERR):
            return "Last error: " + last
        return out.strip()

    # ─────────────────────────────────────────────────────────────────────
    #  File healing
    # ─────────────────────────────────────────────────────────────────────
    def _compile_error(self, path: Path) -> Optional[str]:
        """Return a compile error string for a .py file, or None if it's fine."""
        try:
            py_compile.compile(str(path), doraise=True)
            return None
        except py_compile.PyCompileError as exc:
            return str(exc)
        except Exception as exc:
            return str(exc)

    def heal_file(self, path: str = "", error: str = "") -> str:
        if not path:
            return "Kis file ko fix karun? (path do)"
        p = Path(path)
        if not p.is_absolute():
            # resolve relative to the project package first, then files dir
            cand = Path(__file__).resolve().parent / p.name
            p = cand if cand.exists() else (self.cfg.files_dir() / p)
        if not p.exists():
            return "File nahi mili: " + str(p)
        if p.suffix != ".py":
            return "Abhi sirf Python (.py) files heal kar sakta hoon."

        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return "File read fail: " + str(exc)

        # discover the error if not provided
        err = (error or "").strip() or (self._compile_error(p) or "")
        if not err:
            return "Ye file already theek compile ho rahi hai, %s - kuch fix karne ko nahi." % \
                self.cfg.get("user_name", "P7")

        self._log("Healing", p.name, "heal")
        if not self.brain or not self.brain.online:
            return ("Brain offline hai, isliye auto-fix nahi kar paunga. Error ye hai:\n" + err +
                    "\nBrain (Ollama/OpenRouter) on karo to main khud theek kar dunga.")

        prompt = (
            "FILE: %s\n\nERROR:\n%s\n\nCURRENT CONTENTS:\n%s" % (p.name, err, source)
        )
        candidate = self.brain.ask(_HEAL_SYSTEM, prompt)
        if candidate.startswith(ERR):
            return "Brain se fix nahi mila: " + candidate[len(ERR):].strip()

        # strip accidental code fences
        candidate = candidate.strip()
        if candidate.startswith("```"):
            candidate = candidate.split("\n", 1)[-1]
            if candidate.rstrip().endswith("```"):
                candidate = candidate.rstrip()[:-3]
        candidate = candidate.strip("\n")
        if len(candidate) < 20:
            return "Fix bahut chhota laga, safety ke liye apply nahi kiya."

        # validate the candidate compiles before touching the original
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(candidate)
                tmp_path = Path(tf.name)
            verify_err = self._compile_error(tmp_path)
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            return "Fix verify nahi kar paya: " + str(exc)

        if verify_err:
            return ("Brain ka fix bhi compile nahi hua, isliye file ko haath nahi lagaya "
                    "(safe). Naya error:\n" + verify_err[:300])

        # back up the original, then write the verified fix
        try:
            backup = p.with_suffix(p.suffix + ".bak")
            shutil.copy2(str(p), str(backup))
            p.write_text(candidate, encoding="utf-8")
            self._log("Healed", p.name, "done")
            return ("%s fix kar diya aur verify bhi (compile pass). Backup yahan: %s"
                    % (p.name, backup.name))
        except Exception as exc:
            return "Fix likhne me dikkat: " + str(exc)

    def status(self) -> dict:
        return {"errors_logged": len(self.error_log),
                "brain": bool(self.brain and self.brain.online)}
