# -*- coding: utf-8 -*-
"""
PHANTRON APPS  -  application control framework.

A config-driven registry that knows how to LAUNCH the apps P7 cares about
(WhatsApp, YouTube, VS Code, Blender, Android Studio, Unreal, Maya, Photoshop,
Canva, MS Office, Camera, ...) using the best available strategy on the host:

    * url   -> open a web app in the default browser
    * uri   -> open a Windows app-protocol / store-app alias (e.g. whatsapp:)
    * exe   -> run a command that is on PATH or registered in App Paths
    * path  -> run an absolute executable path (from config overrides)

Users can override or add ANY app by editing the "apps" block in config.json:

    "apps": {
        "unreal":  {"type": "path", "target": "D:\\\\UE_5.4\\\\Engine\\\\Binaries\\\\Win64\\\\UnrealEditor.exe"},
        "mytool":  {"type": "exe",  "target": "mytool"}
    }

This module never imports heavy libs and never crashes - if it cannot launch
something it returns a clear, actionable message.
"""

import os
import platform
import shutil
import subprocess
import webbrowser

IS_WINDOWS = platform.system() == "Windows"


# ── default registry (overridable from config["apps"]) ─────────────────────
DEFAULT_APPS = {
    # web apps
    "youtube":      {"type": "url", "target": "https://www.youtube.com"},
    "whatsapp web": {"type": "url", "target": "https://web.whatsapp.com"},
    "canva":        {"type": "url", "target": "https://www.canva.com"},
    "gmail":        {"type": "url", "target": "https://mail.google.com"},
    "maps":         {"type": "url", "target": "https://maps.google.com"},
    "chatgpt":      {"type": "url", "target": "https://chat.openai.com"},

    # windows store / protocol apps
    "whatsapp":     {"type": "uri", "target": "whatsapp:", "fallback_url": "https://web.whatsapp.com"},
    "camera":       {"type": "uri", "target": "microsoft.windows.camera:"},
    "settings":     {"type": "uri", "target": "ms-settings:"},
    "store":        {"type": "uri", "target": "ms-windows-store:"},

    # browsers
    "chrome":       {"type": "exe", "target": "chrome"},
    "edge":         {"type": "exe", "target": "msedge"},
    "firefox":      {"type": "exe", "target": "firefox"},

    # editors / dev
    "vscode":       {"type": "exe", "target": "code"},
    "vs code":      {"type": "exe", "target": "code"},
    "notepad":      {"type": "exe", "target": "notepad"},
    "cmd":          {"type": "exe", "target": "cmd"},
    "powershell":   {"type": "exe", "target": "powershell"},
    "explorer":     {"type": "exe", "target": "explorer"},
    "calculator":   {"type": "exe", "target": "calc"},
    "android studio": {"type": "exe", "target": "studio64",
                       "hint": "config me 'apps.android studio' ka full path daalo agar na khule"},

    # creative / heavy (usually need a config path override)
    "blender":      {"type": "exe", "target": "blender"},
    "unreal":       {"type": "exe", "target": "UnrealEditor",
                     "hint": "config 'apps.unreal' me UnrealEditor.exe ka full path daalo"},
    "maya":         {"type": "exe", "target": "maya",
                     "hint": "config 'apps.maya' me maya.exe ka full path daalo"},
    "photoshop":    {"type": "exe", "target": "photoshop",
                     "hint": "config 'apps.photoshop' me Photoshop.exe ka full path daalo"},
    "premiere":     {"type": "exe", "target": "premiere",
                     "hint": "config 'apps.premiere' me Premiere.exe ka full path daalo"},

    # ms office
    "word":         {"type": "exe", "target": "winword"},
    "ms word":      {"type": "exe", "target": "winword"},
    "excel":        {"type": "exe", "target": "excel"},
    "ms excel":     {"type": "exe", "target": "excel"},
    "powerpoint":   {"type": "exe", "target": "powerpnt"},
    "ppt":          {"type": "exe", "target": "powerpnt"},
}


# common alias normalisation so "whatts app", "you tube" etc. still match
_ALIASES = {
    "whatts app": "whatsapp", "whats app": "whatsapp", "wp": "whatsapp",
    "you tube": "youtube", "yt": "youtube",
    "vs": "vscode", "visual studio code": "vscode",
    "ms-word": "word", "ms-excel": "excel", "ms-powerpoint": "powerpoint",
    "ue5": "unreal", "ue": "unreal", "unreal engine": "unreal",
}


class AppController:
    def __init__(self, config=None):
        self.config = config or {}
        self.registry = dict(DEFAULT_APPS)
        # merge user overrides; an empty "" target keeps the built-in default
        for name, spec in (self.config.get("apps") or {}).items():
            key = (name or "").lower().strip()
            base = dict(self.registry.get(key, {}))
            clean = {k: v for k, v in (spec or {}).items() if v not in ("", None)}
            base.update(clean)
            if base:
                self.registry[key] = base

    def resolve(self, name):
        """Return (canonical_name, spec) or (name, None) if unknown."""
        key = (name or "").lower().strip()
        key = _ALIASES.get(key, key)
        if key in self.registry:
            return key, self.registry[key]
        # partial match (e.g. "android" -> "android studio")
        for app in self.registry:
            if key and (key in app or app in key):
                return app, self.registry[app]
        return key, None

    def known_apps(self):
        return sorted(self.registry.keys())

    def launch(self, name):
        """Launch an app by friendly name. Returns a human status string."""
        canonical, spec = self.resolve(name)
        if not spec:
            # unknown -> best-effort: try as a raw command / start-menu name
            ok = self._raw_start(name)
            if ok:
                return "'%s' khol diya P7." % name
            return ("'%s' registry me nahi mila P7. config.json ke 'apps' block me "
                    "iska path daal do, ya pura naam batao." % name)

        atype = spec.get("type", "exe")
        target = spec.get("target", "")
        try:
            if atype == "url":
                webbrowser.open(target)
                return "'%s' browser me khol diya P7." % canonical
            if atype == "uri":
                if IS_WINDOWS:
                    os.startfile(target)  # type: ignore[attr-defined]
                    return "'%s' khol diya P7." % canonical
                fb = spec.get("fallback_url")
                if fb:
                    webbrowser.open(fb)
                    return "'%s' (web) khol diya P7." % canonical
                return "'%s' sirf Windows par khulta hai P7." % canonical
            # exe / path
            if self._run_exe(target):
                return "'%s' khol diya P7." % canonical
            fb = spec.get("fallback_url")
            if fb:
                webbrowser.open(fb)
                return "'%s' (web fallback) khol diya P7." % canonical
            hint = spec.get("hint")
            return ("'%s' launch nahi ho saka P7." % canonical) + ((" " + hint) if hint else "")
        except Exception as e:
            return "'%s' kholte waqt error: %s" % (canonical, str(e)[:90])

    # ── launch strategies ──────────────────────────────────────────────
    def _run_exe(self, target):
        if not target:
            return False
        # absolute path?
        if os.path.isabs(target) and os.path.exists(target):
            subprocess.Popen([target])
            return True
        # on PATH?
        if shutil.which(target):
            subprocess.Popen([target] if not IS_WINDOWS else ["cmd", "/c", "start", "", target],
                             shell=False)
            return True
        return self._raw_start(target)

    def _raw_start(self, target):
        try:
            if IS_WINDOWS:
                subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
                return True
            if shutil.which(target):
                subprocess.Popen([target])
                return True
            subprocess.Popen(["xdg-open", target])
            return True
        except Exception:
            return False


_CTRL = None


def controller(config=None):
    global _CTRL
    if _CTRL is None:
        _CTRL = AppController(config)
    return _CTRL


if __name__ == "__main__":
    c = AppController()
    print("Known apps (%d):" % len(c.known_apps()))
    print(", ".join(c.known_apps()))
    print()
    for n in ["youtube", "whatts app", "android", "ue5", "nonexistentapp"]:
        canon, spec = c.resolve(n)
        print("  resolve('%s') -> %s  %s" % (n, canon, spec))
