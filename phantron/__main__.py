# -*- coding: utf-8 -*-
"""
PHANTRON entry point.

    python -m phantron            # start the web UI + assistant
    python -m phantron --cli      # text-only chat in the terminal
    python -m phantron --voice    # also start the voice (wake-word) loop
    python -m phantron --status   # print brain/voice status and exit
"""

from __future__ import annotations

import sys

from .assistant import Assistant
from .config import load_config


BANNER = r"""
   ____  _   _    _    _   _ _____ ____   ___  _   _
  |  _ \| | | |  / \  | \ | |_   _|  _ \ / _ \| \ | |
  | |_) | |_| | / _ \ |  \| | | | | |_) | | | |  \| |
  |  __/|  _  |/ ___ \| |\  | | | |  _ <| |_| | |\  |
  |_|   |_| |_/_/   \_\_| \_| |_| |_| \_\\___/|_| \_|
            v2  -  P7's personal AI
"""


def _print_status(assistant: Assistant):
    st = assistant.status()
    print(BANNER)
    print("  Assistant : %s  (v%s)" % (st["assistant_name"], st["version"]))
    print("  Brain     : %s / %s" % (st["brain"]["provider"], st["brain"]["model"]))
    print("  Detail    : %s" % st["brain"]["detail"])
    print("  Voice     : tts=%s  stt=%s" % (st["voice"]["tts"], st["voice"]["stt"]))
    print("  Autonomous: %s" % st["autonomous"])
    print()


def run_cli(assistant: Assistant, with_voice: bool):
    _print_status(assistant)
    print("  " + assistant.greeting())
    if with_voice:
        if assistant.start_voice_loop():
            print("  [voice] wake-word loop started.")
        else:
            print("  [voice] mic/STT not available - text only.")
    print("  (type 'exit' to quit)\n")
    while True:
        try:
            text = input("P7 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye P7!")
            break
        if text.lower() in ("exit", "quit", "bye"):
            print("  Bye P7!")
            break
        if not text:
            continue
        reply = assistant.chat(text, speak=with_voice)
        print("  PHANTRON > " + reply + "\n")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    assistant = Assistant(cfg)

    if "--status" in argv:
        _print_status(assistant)
        return 0

    if "--cli" in argv:
        run_cli(assistant, with_voice=("--voice" in argv))
        return 0

    # default: web UI (and voice loop if asked)
    if "--voice" in argv:
        if assistant.start_voice_loop():
            print("  [voice] wake-word loop started.")
        else:
            print("  [voice] mic/STT not available - continuing without voice.")

    from .server import serve
    serve(assistant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
