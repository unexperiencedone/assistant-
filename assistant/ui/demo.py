"""Scripted demo run for the live canvas. No microphone, no Claude usage.

    python main.py demo            # opens the canvas and plays the demo once
    python main.py demo --loop     # keep replaying
    python main.py demo --port 8790

It publishes the same event-bus traffic a real session produces, so the canvas
shows exactly what it would show live:

  1. a request routed to a local command (instant, no model)
  2. planning mode: Claude looks around, then drafts a branching plan
  3. "go ahead": steps light up as actions happen, one step fails and is retried
"""

from __future__ import annotations

import argparse
import time
import webbrowser

from ..events import EventBus
from ..planning import Plan
from ..planning.graph import GraphTracker
from ..state import AppState
from .server import DashboardServer, port_free

BACKEND = "Claude Code"

PLAN_BLOCK = """[[PLAN: 5-day exam study schedule]]
1. Collect the syllabus and notes for all five subjects
2. Estimate study hours per topic from the syllabus (after 1)
3. Schedule days 1 and 2 for Theory of Computation (after 2)
4. Schedule days 3 to 5 for the other four subjects (after 2)
5. Add a short evening review of the previous day (after 3, 4)
6. Save the schedule to study_plan.md (after 5)
[[/PLAN]]
Here's a six step plan. Say go ahead when you want me to run it."""


class DemoRun:
    def __init__(self, bus: EventBus, plan: Plan, speed: float) -> None:
        self.bus = bus
        self.plan = plan
        self.speed = speed

    def pause(self, seconds: float) -> None:
        time.sleep(seconds / self.speed)

    def you(self, text: str) -> None:
        self.bus.publish("transcript", role="user", text=text)
        self.bus.status("transcribing")
        self.pause(0.8)

    def nova(self, text: str) -> None:
        self.bus.publish("transcript", role="assistant", text=text)
        self.bus.status("speaking")
        self.pause(2.0)
        self.bus.status("listening")

    def agent(self, kind: str, text: str = "", tool: str = "") -> None:
        self.bus.publish("agent", kind=kind, backend=BACKEND, tool=tool, text=text)

    def plan_update(self, markers: str = "", action: str = "") -> None:
        changed = self.plan.apply_markers(markers) if markers else False
        if action:
            changed = self.plan.infer_from_action(action) or changed
        if changed:
            self.bus.publish("plan", **self.plan.to_dict())

    # -- the script -------------------------------------------------------------------
    def play(self) -> None:
        self.plan.clear()
        self.bus.publish("plan", **self.plan.to_dict())
        self.bus.status("listening")
        self.pause(1.5)

        # 1. Local command: resolved on this computer, no model involved.
        self.you("find my theory of computation notes")
        self.bus.publish("route", utterance="find my theory of computation notes", route="local", name="find_item")
        self.agent("local", r"1. file: C:\Users\you\OneDrive\Documents\Theory of Computation notes.pdf", "find")
        self.agent("local", r"2. folder: C:\Users\you\OneDrive\Documents\TOC", "find")
        self.nova("Top match is Theory of Computation notes in Documents. Say open 1 to open it.")
        self.pause(3)

        # 2. Planning mode: nothing changes on disk, the plan appears as a graph.
        request = "let's plan a 5 day study schedule for my exams"
        self.you(request)
        self.bus.publish("route", utterance=request, route="agent", name="plan", backend=BACKEND)
        self.agent("start", "plan: 5 day study schedule")
        self.bus.status("thinking")
        self.pause(1.5)
        self.agent("tool", r'python "C:\Assisstant\sysindex.py" find syllabus --kind file', "PowerShell")
        self.pause(1.5)
        self.agent("tool", "Theory of Computation notes.pdf", "Read")
        self.pause(1.5)
        self.agent("text", PLAN_BLOCK)
        self.plan.replace_from_block(PLAN_BLOCK)
        self.bus.publish("plan", **self.plan.to_dict())
        self.pause(0.5)
        self.agent("result", PLAN_BLOCK)
        self.nova("Here's a six step plan. Days for Theory of Computation and the other subjects branch in parallel. Say go ahead when you want me to run it.")
        self.pause(4)

        # 3. Execution: steps light up from actions and [[STEP n]] markers.
        self.you("go ahead")
        self.bus.publish("route", utterance=self.plan.title, route="agent", name="execute_plan", backend=BACKEND)
        self.plan.begin_execution()
        self.bus.publish("plan", **self.plan.to_dict())
        self.agent("start", self.plan.title)
        self.bus.status("listening")
        # (markers printed before the action, action tool, action detail, markers printed after)
        steps = [
            ("[[STEP 1 START]]", "Glob", "syllabus", ""),
            ("", "Read", "notes and syllabus for five subjects", ""),
            ("[[STEP 1 DONE]] [[STEP 2 START]]", "Write", "hours_per_topic.md", ""),
            ("[[STEP 2 DONE]] [[STEP 3 START]]", "Edit", "Theory of Computation block, days 1 and 2", ""),
            ("[[STEP 3 DONE]] [[STEP 4 START]]", "Edit", "days 3 to 5 for the other four subjects", ""),
            ("", "PowerShell", "check total hours per day", "[[STEP 4 FAILED]]"),
            ("[[STEP 4 START]]", "Edit", "rebalance days 3 to 5 so no day is over 8 hours", ""),
            ("[[STEP 4 DONE]] [[STEP 5 START]]", "Edit", "add evening review of the previous day", ""),
            ("[[STEP 5 DONE]] [[STEP 6 START]]", "Write", "study_plan.md", ""),
        ]
        for before, tool, detail, after in steps:
            self.plan_update(markers=before)
            self.agent("tool", detail, tool)
            self.pause(2.2)
            if after:
                self.plan_update(markers=after)
                self.agent("text", "Day 4 came out at 11 hours, too long. Rebalancing.")
                self.pause(2.5)
        self.plan_update(markers="[[STEP 6 DONE]]")
        summary = "I saved a five day schedule to study_plan.md, with Theory of Computation on days one and two and a short review every evening."
        self.plan.finish(True)
        self.bus.publish("plan", **self.plan.to_dict())
        self.agent("result", summary)
        self.nova(summary)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--loop", action="store_true", help="replay until Ctrl+C")
    parser.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if not port_free(args.port):
        print(f"Port {args.port} is in use; pick another with --port.")
        return 1
    bus = EventBus()
    plan = Plan()
    GraphTracker(bus, plan)
    state = AppState(bus, "Nova (demo)", BACKEND, "demo workspace, nothing is changed")
    server = DashboardServer(state, args.port, on_text=lambda text: bus.log(f"(demo ignores input: {text})"))
    server.start()
    print(f"Canvas demo: {server.url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(server.url)
    time.sleep(2.5)  # give the page time to connect before the first event

    run = DemoRun(bus, plan, max(0.1, args.speed))
    try:
        while True:
            run.play()
            if not args.loop:
                print("Demo finished. The canvas stays up; press Ctrl+C to exit.")
                while True:
                    time.sleep(1)
            run.pause(6)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
