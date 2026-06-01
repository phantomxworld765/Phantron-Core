# -*- coding: utf-8 -*-
"""
PHANTRON agent - the decision loop that ties the brain to the skills.

Given what P7 says, the agent decides whether to simply talk back or to act
(open an app, run a command, search, automate the mouse, ...). When a real
LLM brain is available it plans using a compact JSON protocol and can chain a
few tool calls autonomously. When no brain is configured it falls back to a
fast rule-based parser so PHANTRON is still useful offline.

JSON protocol the brain must follow each step:

    {"thought": "...", "action": "speak",  "text": "reply to the user"}
    {"thought": "...", "action": "tool",   "tool": "open_app",
     "args": {"target": "chrome"}, "say": "optional line to speak now"}

The loop runs tools, feeds their results back, and stops when the brain
chooses "speak" or after agent.max_steps (a safety bound).
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

from .brain import Brain, ERR
from .skills import Skills, TOOLS


def _persona(cfg) -> str:
    name = cfg.get("assistant_name", "PHANTRON")
    user = cfg.get("user_name", "P7")
    lang = cfg.get("language", "hinglish")
    tone = {
        "hinglish": "Reply in friendly, natural Hinglish (Roman script). Keep it short and warm.",
        "hindi": "Reply in simple Hindi (Devanagari).",
        "english": "Reply in clear, friendly English.",
    }.get(lang, "Reply in friendly Hinglish.")
    return (
        "You are {name}, a loyal personal AI assistant for {user} - styled like "
        "Jarvis/Friday. You are confident, quick and a little witty. {tone} "
        "Call the user {user}. Never mention you are a language model; you are {name}."
    ).format(name=name, user=user, tone=tone)


def _tool_catalogue() -> str:
    lines = []
    for t in TOOLS:
        args = (" args: " + t["args"]) if t["args"] else ""
        lines.append("- %s%s -> %s" % (t["name"], args, t["desc"]))
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply (tolerates code fences)."""
    if not text:
        return None
    text = text.strip()
    # strip ```json ... ``` fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # find the first balanced {...}
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    return json.loads(blob)
                except Exception:
                    return None
    return None


class Agent:
    def __init__(self, cfg, brain: Brain, skills: Skills,
                 on_event: Optional[Callable[[str, str, str], None]] = None,
                 memory=None):
        self.cfg = cfg
        self.brain = brain
        self.skills = skills
        self.on_event = on_event
        self.memory = memory
        self.history: List[Dict[str, str]] = []

    def _emit(self, action: str, detail: str = "", kind: str = "thought"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    def _remember(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        cap = int(self.cfg.get("brain.memory_turns", 12)) * 2
        if len(self.history) > cap:
            self.history = self.history[-cap:]

    # ─────────────────────────────────────────────────────────────────────
    #  Public entry point
    # ─────────────────────────────────────────────────────────────────────
    def handle(self, user_text: str) -> str:
        user_text = (user_text or "").strip()
        if not user_text:
            return ""
        self._remember("user", user_text)

        if self.brain.online and self.cfg.get("agent.autonomous", True):
            reply = self._handle_with_brain(user_text)
        elif self.brain.online:
            reply = self._handle_chat_only(user_text)
        else:
            reply = self._handle_offline(user_text)

        self._remember("assistant", reply)
        return reply

    # ─────────────────────────────────────────────────────────────────────
    #  Brain-driven autonomous loop
    # ─────────────────────────────────────────────────────────────────────
    def _system_prompt(self) -> str:
        mem_block = ""
        if self.memory:
            facts = self.memory.facts_block()
            recap = self.memory.recent_context(6)
            if facts:
                mem_block += "\n\nKnown facts about the user (use them naturally):\n" + facts
            if recap:
                mem_block += "\n\nRecent conversation recap (for continuity):\n" + recap
        return (
            _persona(self.cfg) + mem_block + "\n\n"
            "You can ACT on the user's Windows PC by calling tools. Decide each "
            "step and reply with ONE json object only, no extra prose.\n\n"
            "Available tools:\n" + _tool_catalogue() + "\n\n"
            "Respond with exactly one of:\n"
            '  {"thought":"why","action":"tool","tool":"<name>",'
            '"args":{...},"say":"short line to tell the user now (optional)"}\n'
            '  {"thought":"why","action":"speak","text":"final reply to the user"}\n\n'
            "Rules: use a tool only when the user wants something DONE. For normal "
            "chat, questions or coding help, just use \"speak\". If the user shares "
            "something worth remembering long-term (their name, preferences, setup), "
            "use the remember tool. After a tool runs you will get its result; then "
            "either call another tool or speak. Keep spoken text concise and in the "
            "user's language."
        )

    def _handle_with_brain(self, user_text: str) -> str:
        system = self._system_prompt()
        # working transcript for this turn (separate from long-term history)
        convo: List[Dict[str, str]] = list(self.history)
        max_steps = int(self.cfg.get("agent.max_steps", 6))
        spoken: List[str] = []

        for _ in range(max_steps):
            raw = self.brain.chat(system, convo)
            if raw.startswith(ERR):
                # brain failed mid-loop -> degrade gracefully
                fallback = self._handle_offline(user_text)
                return fallback or ("Dimaag se connect nahi ho pa raha: " + raw[len(ERR):].strip())

            plan = _extract_json(raw)
            if not plan:
                # model just talked plainly; treat it as the answer
                return raw.strip()

            action = (plan.get("action") or "").lower()
            if plan.get("thought"):
                self._emit("Thinking", str(plan["thought"])[:120], "thought")

            if action == "speak":
                text = (plan.get("text") or "").strip()
                if spoken:
                    text = (" ".join(spoken) + (" " + text if text else "")).strip()
                return text or "Ho gaya, P7."

            if action == "tool":
                tool = plan.get("tool", "")
                args = plan.get("args", {}) or {}
                say = (plan.get("say") or "").strip()
                if say:
                    spoken.append(say)
                    self._emit("Saying", say, "speak")

                result = self.skills.execute(tool, args)
                self._emit("Tool", "%s -> %s" % (tool, str(result)[:80]), "tool")

                # feed the model what just happened, ask it to continue
                convo = convo + [
                    {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
                    {"role": "user", "content": "TOOL_RESULT(%s): %s\nContinue: call another tool or speak the final reply." % (tool, result)},
                ]
                continue

            # unknown action shape -> bail with whatever text we have
            return (plan.get("text") or " ".join(spoken) or "Theek hai, P7.").strip()

        # ran out of steps
        return (" ".join(spoken) or "Kaafi steps ho gaye, P7 - ek aur baar try karun?").strip()

    # ─────────────────────────────────────────────────────────────────────
    #  Chat-only (brain online but autonomy disabled)
    # ─────────────────────────────────────────────────────────────────────
    def _handle_chat_only(self, user_text: str) -> str:
        # still honour obvious direct commands locally for speed
        quick = self._rule_based(user_text)
        if quick is not None:
            return quick
        reply = self.brain.chat(_persona(self.cfg), list(self.history))
        if reply.startswith(ERR):
            return self._handle_offline(user_text) or ("Dimaag offline: " + reply[len(ERR):].strip())
        return reply.strip()

    # ─────────────────────────────────────────────────────────────────────
    #  Offline rule-based fallback
    # ─────────────────────────────────────────────────────────────────────
    def _handle_offline(self, user_text: str) -> str:
        out = self._rule_based(user_text)
        if out is not None:
            return out
        return (
            "Abhi mera AI brain connect nahi hai, %s. Main app khol sakta hoon, "
            "gaane chala sakta hoon, system info de sakta hoon. Poori soch-samajh "
            "ke liye config.json me Ollama ya ek API key laga do."
            % self.cfg.get("user_name", "P7")
        )

    def _rule_based(self, text: str) -> Optional[str]:
        """A fast intent parser for the most common direct commands."""
        t = text.lower().strip()

        m = re.search(r"\b(open|khol(?:o|do)?|launch|start)\s+(.+)", t)
        if m:
            return self.skills.open_app(m.group(2).strip(" .!"))

        m = re.search(r"\b(play|chala(?:o|do)?|bajao)\s+(.+)", t)
        if m:
            return self.skills.play_song(m.group(2).strip(" .!"))

        m = re.search(r"\b(search|google|dhundo|khojo)\s+(.+)", t)
        if m:
            return self.skills.web_search(m.group(2).strip(" .!"))

        if re.search(r"\b(time|samay|kitne baje|date|tareekh)\b", t):
            return self.skills.tell_time()

        if re.search(r"\b(system|cpu|ram|battery|memory)\b", t):
            return self.skills.system_info()

        if re.search(r"\b(screenshot|screen shot|capture screen)\b", t):
            return self.skills.screenshot()

        if re.search(r"\b(read screen|screen padho|screen pe kya|what.s on screen)\b", t):
            return self.skills.read_screen()
        if re.search(r"\b(see screen|screen dekho|look at screen|describe screen)\b", t):
            return self.skills.see_screen()

        m = re.search(r"\bremember\s+(?:that\s+)?(.+?)\s+(?:is|=|hai)\s+(.+)", t)
        if m:
            return self.skills.remember(m.group(1).strip(), m.group(2).strip(" .!"))
        m = re.search(r"(?:what'?s my|what is my|recall|yaad hai|mera|meri)\s+(.+)", t)
        if m:
            ans = self.skills.recall(m.group(1).strip(" ?.!"))
            if ans and "kuch yaad nahi" not in ans:
                return ans

        if re.search(r"\bvolume up|awaaz badha", t):
            return self.skills.media("volume_up")
        if re.search(r"\bvolume down|awaaz kam", t):
            return self.skills.media("volume_down")
        if re.search(r"\bmute\b", t):
            return self.skills.media("mute")
        if re.search(r"\b(next song|agla gaana|next track)\b", t):
            return self.skills.media("next")
        if re.search(r"\b(pause|rok do|stop music)\b", t):
            return self.skills.media("play_pause")

        if t in ("hi", "hello", "hey", "hii", "namaste", "yo"):
            return "Hello %s! Main PHANTRON. Bolo kya karna hai?" % self.cfg.get("user_name", "P7")

        # weather
        m = re.search(r"\b(?:weather|mausam|temperature)\s*(?:in|of|ka)?\s*(.*)", t)
        if m and ("weather" in t or "mausam" in t or "temperature" in t):
            return self.skills.weather(m.group(1).strip(" ?.!"))

        # news
        m = re.search(r"\b(?:news|khabar|headlines)\s*(?:about|on|ki)?\s*(.*)", t)
        if m and ("news" in t or "khabar" in t or "headline" in t):
            return self.skills.news(m.group(1).strip(" ?.!"))

        # calculator
        m = re.search(r"\b(?:calculate|calc|kitna(?:\s+hota)?(?:\s+hai)?|solve)\s+(.+)", t)
        if m:
            return self.skills.calc(m.group(1).strip(" ?.!"))
        if re.fullmatch(r"[\d\s+\-*/().%^]+", text.strip()) and re.search(r"[+\-*/]", text):
            return self.skills.calc(text.strip())

        # reminders / timers
        m = re.search(r"\bremind me (?:to |about )?(.+?)\s+(?:in|after)\s+(\d+)\s*(min|minute|minutes|sec|second|seconds|hour|hours|ghante|minat)?", t)
        if m:
            num = int(m.group(2)); unit = (m.group(3) or "min")
            if unit.startswith(("sec", "second")):
                return self.skills.set_reminder(m.group(1).strip(), seconds=num)
            if unit.startswith(("hour", "ghant")):
                return self.skills.set_reminder(m.group(1).strip(), minutes=num * 60)
            return self.skills.set_reminder(m.group(1).strip(), minutes=num)
        m = re.search(r"\b(?:set (?:a )?timer|timer)\s+(?:for\s+)?(\d+)\s*(min|minute|minutes|sec|second|seconds)?", t)
        if m:
            num = int(m.group(1)); unit = (m.group(2) or "min")
            if unit.startswith(("sec", "second")):
                return self.skills.timer(seconds=num)
            return self.skills.timer(minutes=num)

        # notes & todo
        m = re.search(r"\b(?:note|note down|likho|yaad rakhna)\s+(?:that\s+)?(.+)", t)
        if m and "todo" not in t and "to-do" not in t:
            return self.skills.add_note(m.group(1).strip())
        if re.search(r"\b(?:read|show|meri?)\s+notes\b", t):
            return self.skills.read_notes()
        m = re.search(r"\b(?:add (?:to )?todo|todo|to-do)\s+(.+)", t)
        if m:
            return self.skills.add_todo(m.group(1).strip())
        if re.search(r"\b(?:list|show|meri?)\s+(?:todo|to-do|tasks)\b", t):
            return self.skills.list_todo()

        # system control
        if re.search(r"\block (?:pc|computer|screen)\b|computer lock", t):
            return self.skills.lock_pc()
        if re.search(r"\b(?:processes|kon se app chal)\b|running apps", t):
            return self.skills.list_processes()
        m = re.search(r"\bclose\s+(.+)", t)
        if m and "window" not in t:
            return self.skills.close_app(m.group(1).strip(" .!"))

        # folders
        m = re.search(r"\bopen\s+(downloads|documents|desktop|pictures|music|videos|home)\s*(?:folder)?", t)
        if m:
            return self.skills.open_folder(m.group(1))

        return None
