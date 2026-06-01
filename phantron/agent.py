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
                 on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.brain = brain
        self.skills = skills
        self.on_event = on_event
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
        return (
            _persona(self.cfg) + "\n\n"
            "You can ACT on the user's Windows PC by calling tools. Decide each "
            "step and reply with ONE json object only, no extra prose.\n\n"
            "Available tools:\n" + _tool_catalogue() + "\n\n"
            "Respond with exactly one of:\n"
            '  {"thought":"why","action":"tool","tool":"<name>",'
            '"args":{...},"say":"short line to tell the user now (optional)"}\n'
            '  {"thought":"why","action":"speak","text":"final reply to the user"}\n\n'
            "Rules: use a tool only when the user wants something DONE. For normal "
            "chat, questions or coding help, just use \"speak\". After a tool runs "
            "you will get its result; then either call another tool or speak. Keep "
            "spoken text concise and in the user's language."
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

        return None
