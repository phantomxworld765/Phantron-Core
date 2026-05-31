# -*- coding: utf-8 -*-
"""
PHANTRON AUTONOMOUS  -  the continuous perceive -> think -> act loop.

This is the "core-level autonomous agent". Given a GOAL, it repeatedly:

    1. PERCEIVE   capture multimodal state -> on-screen text (vision/OCR),
                  active window, system snapshot, and the result of the last action.
    2. THINK      ask the AI brain for the single next best action as strict JSON.
    3. ACT        execute that action through the host's tool executor
                  (keyboard / mouse / command / open / vision click / ...).
    4. OBSERVE    feed the new perception back and loop until done or max steps.

It is decoupled from phantron_core: the host passes in
    * ai_fn(prompt, system) -> str         (the language model)
    * executor(tool, args)  -> str         (runs a tool, returns a result string)
    * vision                                (a phantron_vision.VisionEngine, optional)
    * on_event(type, data)                 (live progress callback, optional)

Safety: a hard step cap, and a guard that blocks destructive shell commands
unless the config explicitly allows them. Nothing runs silently - every step
is reported through on_event.
"""

import json
import re
import time


# commands we refuse to run autonomously unless explicitly allowed
_DESTRUCTIVE = [
    r"\brm\s+-rf\b", r"\bdel\s+/[sqf]", r"\bformat\b", r"\bdiskpart\b",
    r"\brmdir\s+/s", r"\bremove-item\b.*-recurse", r"\bmkfs\b",
    r"\breg\s+delete\b", r"\bshutdown\b", r"\brestart-computer\b",
    r"\bstop-computer\b", r":\(\)\{.*\};:", r"\bdd\s+if=",
]


def is_destructive(text):
    t = (text or "").lower()
    return any(re.search(p, t) for p in _DESTRUCTIVE)


AGENT_SYSTEM = (
    "Tum PHANTRON ka AUTONOMOUS execution core ho, {user} ke liye. Tum ek GOAL ko "
    "chhote steps me khud poora karte ho, screen dekh kar (vision) decide karte ho.\n"
    "Har turn me SIRF ek JSON object do, aur kuch nahi:\n"
    '{{"think": "<1 line reasoning>", '
    '"action": {{"tool": "<naam>", "args": {{...}}}}, '
    '"say": "<1 line Hindi update, no emoji>", '
    '"done": <true|false>}}\n'
    "Rules:\n"
    "- Ek turn me sirf EK action. Main uska result aur nayi screen wapas dunga, phir agla step do.\n"
    "- Jab GOAL pura ho jaye to action ko null/omit karo aur done=true.\n"
    "- Agar screen padhni hai to pehle 'read_screen' tool use karo.\n"
    "- Button/link par click karna ho to 'click_text' use karo (args: {{\"text\": \"...\"}}).\n"
    "Available tools:\n"
    "- read_screen {{}}            : OCR se screen ka text padho\n"
    "- click_text {{text}}         : screen par dikhne wale text/button ko dhundh kar click karo\n"
    "- open {{target}}             : app ya url kholo\n"
    "- type {{text}} | press {{key}} | hotkey {{keys:[...]}}\n"
    "- mouse {{action,x,y}}        : click/move/scroll_up/scroll_down\n"
    "- command {{cmd}}             : shell command (destructive band hai safe mode me)\n"
    "- search {{query}} | play_song {{song}}\n"
    "- screenshot {{}} | system_info {{}}\n"
    "Hamesha Hindi me 'say'. SIRF valid JSON, aur kuch mat likho."
)


class AutonomousAgent:
    def __init__(self, config=None, ai_fn=None, executor=None, vision=None, on_event=None):
        self.config = config or {}
        self.ai_fn = ai_fn
        self.executor = executor
        self.vision = vision
        self.on_event = on_event or (lambda t, d: None)
        self.history = []

    # ── helpers ─────────────────────────────────────────────────────────
    def _emit(self, etype, **data):
        try:
            self.on_event(etype, data)
        except Exception:
            pass

    def _perceive(self, last_result=None):
        """Build a compact multimodal snapshot of the current state."""
        parts = []
        if self.vision is not None and self.vision.available():
            title = self.vision.active_window_title()
            if title:
                parts.append("Active window: " + title)
            txt = self.vision.read_text()
            if txt and not txt.startswith("__"):
                parts.append("Screen text (OCR):\n" + " ".join(txt.split())[:900])
            elif txt == "__NO_OCR__":
                parts.append("(OCR off - screen ka text nahi padh sakta, blind mode)")
        else:
            parts.append("(Vision off - screen nahi dekh sakta)")
        if last_result is not None:
            parts.append("Last action result:\n" + str(last_result)[:600])
        return "\n\n".join(parts) if parts else "(no perception)"

    def _guard(self, tool, args):
        """Return an error string if this action is unsafe, else None."""
        safe = self.config.get("agent_safe_mode", True)
        allow_shutdown = self.config.get("agent_allow_shutdown", False)
        if tool == "command":
            cmd = args.get("cmd") or args.get("command") or ""
            if is_destructive(cmd) and not (allow_shutdown and not safe):
                return ("BLOCKED (safety): destructive command refuse kiya -> '%s'. "
                        "Config me agent_safe_mode=false aur agent_allow_shutdown=true "
                        "karo tabhi chalega." % cmd[:80])
        return None

    # ── main loop ───────────────────────────────────────────────────────
    def run(self, goal, max_steps=None):
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "summary": "Koi goal nahi mila.", "steps": []}
        if not self.ai_fn:
            return {"ok": False, "summary": "AI brain off hai, autonomous mode nahi chalega.",
                    "steps": []}
        max_steps = int(max_steps or self.config.get("agent_max_steps", 12))
        user = self.config.get("user_name", "P7")
        self._emit("goal", message="Autonomous goal: " + goal)

        steps = []
        last_result = None
        final_say = ""
        for i in range(1, max_steps + 1):
            perception = self._perceive(last_result)
            prompt = (
                "GOAL: " + goal +
                "\n\nABHI KI STATE:\n" + perception +
                ("\n\nAb tak " + str(i - 1) + " step ho chuke." if i > 1 else "") +
                "\n\nAgla EK step JSON me do."
            )
            raw = self.ai_fn(prompt, AGENT_SYSTEM.format(user=user))
            if raw and str(raw).startswith("__ERR__"):
                return {"ok": False, "summary": "AI brain offline ho gaya step %d par." % i,
                        "steps": steps, "say": final_say}

            data = _extract_json(raw)
            if not isinstance(data, dict):
                # not JSON -> treat as a final spoken reply
                final_say = _clean(raw) or final_say
                self._emit("done", message=final_say or "Ho gaya.")
                return {"ok": True, "summary": final_say or "Ho gaya P7.", "steps": steps,
                        "say": final_say}

            think = (data.get("think") or "").strip()
            say = (data.get("say") or "").strip()
            action = data.get("action")
            done = bool(data.get("done", False))
            if say:
                final_say = say
            self._emit("step_think", index=i, message=say or think or "")

            # finished?
            if done and not action:
                self._emit("done", message=final_say or "Goal complete.")
                return {"ok": True, "summary": final_say or "Goal poora ho gaya P7.",
                        "steps": steps, "say": final_say}

            if not isinstance(action, dict) or not action.get("tool"):
                # nothing actionable but not done -> nudge once, then stop
                if done:
                    return {"ok": True, "summary": final_say or "Ho gaya P7.", "steps": steps}
                last_result = "Koi valid action nahi mila, dobara plan karo."
                continue

            tool = str(action.get("tool", "")).lower()
            args = action.get("args") or {}

            # safety guard
            blocked = self._guard(tool, args)
            if blocked:
                self._emit("blocked", index=i, message=blocked)
                steps.append({"index": i, "tool": tool, "args": args, "result": blocked,
                              "blocked": True})
                last_result = blocked
                continue

            # built-in vision tools handled here; everything else -> host executor
            self._emit("step_start", index=i, tool=tool, message=say or tool)
            result = self._do(tool, args)
            steps.append({"index": i, "tool": tool, "args": args, "result": str(result)[:400]})
            self._emit("step_result", index=i, tool=tool, ok=True, message=str(result)[:160])
            last_result = result

            if done:
                self._emit("done", message=final_say or "Ho gaya.")
                return {"ok": True, "summary": (final_say or "Ho gaya P7."), "steps": steps,
                        "say": final_say}
            time.sleep(0.3)  # let the UI / OS settle before next perception

        self._emit("done", message="Step limit (%d) tak pahunch gaye." % max_steps)
        return {"ok": False, "summary": (final_say or "") +
                " (Itne steps me pura nahi hua P7, dobara try karo ya goal chhota karo.)",
                "steps": steps, "say": final_say}

    # ── action execution ────────────────────────────────────────────────
    def _do(self, tool, args):
        # vision-native tools
        if tool in ("read_screen", "read_text", "ocr"):
            if not (self.vision and self.vision.available()):
                return "Vision off hai - screen nahi padh sakta (pip install pyautogui pytesseract)."
            txt = self.vision.read_text()
            if txt == "__NO_OCR__":
                return "OCR off - pip install pytesseract + Tesseract engine chahiye."
            return ("Screen par text: " + " ".join(txt.split())[:600]) if txt and not txt.startswith("__") \
                else "Screen par readable text nahi mila."
        if tool in ("click_text", "click_on", "tap_text"):
            return self._click_text(args.get("text") or args.get("query") or "")
        # delegate to host
        if self.executor:
            try:
                return self.executor(tool, args)
            except Exception as e:
                return "Action error (%s): %s" % (tool, e)
        return "Tool '%s' ko run karne wala executor nahi mila." % tool

    def _click_text(self, query):
        query = (query or "").strip()
        if not query:
            return "Kis text par click karun?"
        if not (self.vision and self.vision.available()):
            return "Click-by-text ke liye vision chahiye (pip install pyautogui pytesseract)."
        hits = self.vision.find_text(query)
        if not hits:
            return "Screen par '%s' nahi mila." % query
        best = hits[0]
        if self.executor:
            self.executor("mouse", {"action": "click", "x": best["cx"], "y": best["cy"]})
            return "'%s' par click kiya (%d,%d)." % (query, best["cx"], best["cy"])
        return "'%s' yahan mila (%d,%d) par click executor nahi mila." % (
            query, best["cx"], best["cy"])


# ── JSON / text helpers ─────────────────────────────────────────────────
def _extract_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _clean(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


if __name__ == "__main__":
    # offline smoke test with a fake AI that drives a 2-step plan
    plan = [
        '{"think":"pehle screen padho","action":{"tool":"read_screen","args":{}},'
        '"say":"Screen padh raha hoon","done":false}',
        '{"think":"kaam ho gaya","action":null,"say":"Goal poora","done":true}',
    ]
    box = {"i": 0}

    def fake_ai(prompt, system):
        out = plan[min(box["i"], len(plan) - 1)]
        box["i"] += 1
        return out

    def fake_exec(tool, args):
        return "executed %s %s" % (tool, args)

    agent = AutonomousAgent(config={"agent_max_steps": 5}, ai_fn=fake_ai,
                            executor=fake_exec, vision=None,
                            on_event=lambda t, d: print("  [%s] %s" % (t, d.get("message", ""))))
    rep = agent.run("demo goal")
    print("RESULT:", rep["ok"], "-", rep["summary"])
