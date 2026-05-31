# -*- coding: utf-8 -*-
"""
PHANTRON MACROS  -  DEEP application automation (script / CLI driven).

GUI clicking is fragile. The reliable way to make heavy creative/dev apps do
real work is to drive their OWN scripting engine or command-line interface.
This module turns a plain-Hindi instruction into a script/command for the app,
runs it, and returns the output:

    Blender  -> generate a bpy (Blender Python) script, run headless or in GUI
                ( blender --background --python script.py )
    Android  -> gradle build  ( gradlew assembleDebug / bundleRelease )
    FFmpeg   -> generate + run an ffmpeg command (video/audio/teaser edits)
    Python   -> generate + run a standalone Python script

It uses the AI brain (ai_fn) to author the script, then executes via the host's
run_cmd callback (or a built-in subprocess runner). Every generated command is
sanity-checked, and destructive shell patterns are refused.

Decoupled + import-safe:
    MacroEngine(config, ai_fn=fn, run_cmd=fn, on_event=fn)
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

try:
    from phantron_autonomous import is_destructive
except Exception:
    def is_destructive(text):
        t = (text or "").lower()
        return any(p in t for p in ["rm -rf", "del /s", "format ", "diskpart", "mkfs", "shutdown"])


def _strip_fences(text):
    t = str(text or "").strip()
    t = re.sub(r"^```[a-zA-Z0-9_+-]*\n?", "", t)
    t = re.sub(r"\n?```$", "", t)
    return t.strip("\n")


class MacroEngine:
    def __init__(self, config=None, ai_fn=None, run_cmd=None, on_event=None):
        self.config = config or {}
        self.ai_fn = ai_fn
        self.run_cmd = run_cmd or self._default_run
        self.on_event = on_event or (lambda t, d: None)

    # ── infra ───────────────────────────────────────────────────────────
    def _emit(self, etype, **data):
        try:
            self.on_event(etype, data)
        except Exception:
            pass

    def _default_run(self, command, cwd=None, timeout=None):
        timeout = timeout or self.config.get("agent_command_timeout", 900)
        try:
            shell = isinstance(command, str)
            r = subprocess.run(command, shell=shell, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8", errors="replace")
            out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
            return r.returncode == 0, out
        except subprocess.TimeoutExpired:
            return False, "Command timeout (%ss)." % timeout
        except FileNotFoundError as e:
            return False, "Program nahi mila: " + str(e)
        except Exception as e:
            return False, str(e)

    def _projects_dir(self):
        d = self.config.get("projects_dir") or os.path.join(os.path.expanduser("~"), "Phantron", "Projects")
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except Exception:
            d = os.getcwd()
        return d

    def _resolve_app(self, name, default):
        """Find an app's executable: config 'apps' path -> PATH -> default name."""
        spec = (self.config.get("apps") or {}).get(name) or {}
        target = spec.get("target")
        if target and os.path.exists(target):
            return target
        if target and shutil.which(target):
            return target
        if shutil.which(default):
            return default
        return target or default  # let it fail with a clear message if absent

    def _need_ai(self):
        return None if self.ai_fn else "Script likhne ke liye AI brain chahiye P7 (Ollama/Claude)."

    def _write_script(self, content, suffix):
        path = os.path.join(self._projects_dir(),
                            "phantron_%d%s" % (int(time.time()), suffix))
        Path(path).write_text(content, encoding="utf-8")
        return path

    # ════════════════════════════════════════════════════════════════════
    #  BLENDER  (bpy script -> run)
    # ════════════════════════════════════════════════════════════════════
    def blender(self, instruction, background=None):
        err = self._need_ai()
        if err:
            return err
        instruction = (instruction or "").strip()
        if not instruction:
            return "Blender me kya banau P7?"
        system = (
            "You are an expert Blender technical artist. Write a COMPLETE Python "
            "script using the bpy API for the request. Output ONLY raw Python "
            "code (no markdown, no prose). Make it self-contained: clear the "
            "default scene if needed, build the objects, set up a camera and a "
            "light if rendering, and if a render is asked, set "
            "bpy.context.scene.render.filepath and call bpy.ops.render.render(write_still=True)."
        )
        self._emit("working", message="Blender script bana raha hoon: " + instruction[:60])
        code = self.ai_fn("Blender request: " + instruction, system)
        if not code or str(code).startswith("__ERR__"):
            return "AI se Blender script nahi mila (brain offline?) P7."
        code = _strip_fences(code)
        # quick validity gate
        try:
            compile(code, "<bpy>", "exec")
        except SyntaxError as e:
            return "Generated Blender script me syntax error tha P7 (line %s). Dobara try karo." % e.lineno
        script = self._write_script(code, "_blender.py")
        blender_exe = self._resolve_app("blender", "blender")
        bg = self.config.get("blender_background", True) if background is None else background
        cmd = [blender_exe] + (["--background"] if bg else []) + ["--python", script]
        self._emit("running", message="Blender chala raha hoon" + (" (background)" if bg else ""))
        ok, out = self.run_cmd(cmd)
        tail = "\n".join(out.splitlines()[-12:])
        head = "Blender task done P7." if ok else "Blender task me dikkat aayi P7."
        return "%s Script: %s\n%s" % (head, script, tail[:900])

    # ════════════════════════════════════════════════════════════════════
    #  ANDROID  (gradle build)
    # ════════════════════════════════════════════════════════════════════
    def android_build(self, project_dir=None, task="assembleDebug"):
        project_dir = (project_dir or self.config.get("android_project_dir") or "").strip().strip("'\"")
        if not project_dir or not os.path.isdir(project_dir):
            return ("Android project ka folder batao P7 (jisme gradlew ho). "
                    "Jaise: android build D:\\\\MyApp")
        gradlew = os.path.join(project_dir, "gradlew.bat" if os.name == "nt" else "gradlew")
        if not os.path.exists(gradlew):
            return "Us folder me gradlew nahi mila P7: %s" % project_dir
        task = re.sub(r"[^a-zA-Z]", "", task) or "assembleDebug"
        self._emit("running", message="Gradle build: " + task)
        ok, out = self.run_cmd([gradlew, task], cwd=project_dir)
        tail = "\n".join(out.splitlines()[-15:])
        if ok:
            apk = self._find_apk(project_dir)
            return "Android build (%s) ho gaya P7.%s\n%s" % (
                task, ("\nAPK: " + apk) if apk else "", tail[:700])
        return "Android build fail hua P7 (%s):\n%s" % (task, tail[:900])

    def _find_apk(self, project_dir):
        try:
            for root, _, files in os.walk(os.path.join(project_dir, "app", "build", "outputs")):
                for f in files:
                    if f.endswith(".apk"):
                        return os.path.join(root, f)
        except Exception:
            pass
        return ""

    # ════════════════════════════════════════════════════════════════════
    #  FFMPEG  (video / audio / teaser edits)
    # ════════════════════════════════════════════════════════════════════
    def ffmpeg(self, instruction):
        err = self._need_ai()
        if err:
            return err
        instruction = (instruction or "").strip()
        if not instruction:
            return "FFmpeg se kya karna hai P7? (file path + kaam batao)"
        if not shutil.which("ffmpeg") and not (self.config.get("ffmpeg_path")):
            return "ffmpeg install nahi hai P7. https://ffmpeg.org se install karke PATH me daalo."
        system = (
            "You translate a request into a SINGLE ffmpeg command line. Output "
            "ONLY the command, starting with 'ffmpeg', no markdown, no prose, no "
            "explanation. Use the exact file paths given by the user."
        )
        cmd = self.ai_fn("Request: " + instruction, system)
        if not cmd or str(cmd).startswith("__ERR__"):
            return "AI se ffmpeg command nahi mila P7."
        cmd = _strip_fences(cmd).splitlines()[0].strip()
        if not cmd.lower().startswith("ffmpeg"):
            return "Safe nahi laga (ffmpeg se shuru nahi hua), isliye run nahi kiya P7: " + cmd[:120]
        if is_destructive(cmd):
            return "Command me khatarnak pattern mila, run nahi kiya P7."
        ffpath = self.config.get("ffmpeg_path")
        if ffpath:
            cmd = ffpath + cmd[len("ffmpeg"):]
        self._emit("running", message="ffmpeg: " + cmd[:80])
        ok, out = self.run_cmd(cmd)
        tail = "\n".join(out.splitlines()[-10:])
        return ("ffmpeg done P7.\n" if ok else "ffmpeg me dikkat P7.\n") + ("`%s`\n%s" % (cmd, tail[:700]))

    # ════════════════════════════════════════════════════════════════════
    #  PYTHON  (generate + run a standalone script)
    # ════════════════════════════════════════════════════════════════════
    def python_script(self, instruction, run=True):
        err = self._need_ai()
        if err:
            return err
        instruction = (instruction or "").strip()
        if not instruction:
            return "Python script kis cheez ki banau P7?"
        system = ("You are an expert Python developer. Write a COMPLETE, runnable "
                  "Python script for the request. Output ONLY raw Python code, no "
                  "markdown fences, no explanation.")
        code = self.ai_fn("Request: " + instruction, system)
        if not code or str(code).startswith("__ERR__"):
            return "AI se Python script nahi mila P7."
        code = _strip_fences(code)
        try:
            compile(code, "<script>", "exec")
        except SyntaxError as e:
            return "Generated script me syntax error (line %s) P7." % e.lineno
        path = self._write_script(code, "_script.py")
        if not run:
            return "Script bana di P7: " + path
        self._emit("running", message="Python script chala raha hoon")
        ok, out = self.run_cmd(["python", path])
        tail = "\n".join(out.splitlines()[-12:])
        return ("Script chal gaya P7: %s\n%s" % (path, tail[:800])) if ok \
            else ("Script run me error P7: %s\n%s" % (path, tail[:900]))

    # ════════════════════════════════════════════════════════════════════
    #  DISPATCHER
    # ════════════════════════════════════════════════════════════════════
    def run(self, app, instruction=""):
        app = (app or "").lower().strip()
        if app in ("blender",):
            return self.blender(instruction)
        if app in ("android", "android studio", "gradle"):
            return self.android_build(instruction or None)
        if app in ("ffmpeg", "video", "teaser"):
            return self.ffmpeg(instruction)
        if app in ("python", "py", "script"):
            return self.python_script(instruction)
        return ("Deep automation abhi in apps ke liye hai P7: blender, android, "
                "ffmpeg, python. '%s' ke liye 'auto: <kaam>' bolo - autonomous "
                "mode GUI+vision se handle karega." % app)


_ENGINE = None


def engine(config=None, ai_fn=None, run_cmd=None, on_event=None):
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = MacroEngine(config=config, ai_fn=ai_fn, run_cmd=run_cmd, on_event=on_event)
    return _ENGINE


if __name__ == "__main__":
    def fake_ai(p, s):
        if "ffmpeg" in s.lower():
            return "ffmpeg -i input.mp4 -t 15 teaser.mp4"
        return "import bpy\nprint('hello from bpy')"
    eng = MacroEngine(config={}, ai_fn=fake_ai,
                      run_cmd=lambda c, cwd=None, timeout=None: (True, "ran: %s" % (c,)),
                      on_event=lambda t, d: print("  [%s] %s" % (t, d.get("message", ""))))
    print(eng.ffmpeg("input.mp4 ka 15 second ka teaser banao"))
    print("---")
    print(eng.run("python", "ek number guessing game banao"))
