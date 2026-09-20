"""Live "thought process" graph for the current request, as nodes + edges.

    request ─▶ route ─▶ agent ─────────────────────▶ result      (direct task)
                  └──▶ step 1 ─▶ step 2 ─┬▶ step 4 ─▶ result      (plan: a DAG)
                                └▶ step 3 ┘

Actions the agent takes (tool calls, local lookups) are listed inside the node
they belong to, newest last, instead of becoming nodes of their own: the graph
keeps the shape of the plan, and each box shows what is happening in it.

Built purely from event-bus traffic (route / agent / plan topics) and
re-published on the "graph" topic as JSON-ready dicts, so any UI can render it.

    python -m assistant.planning.graph     # prints a sample run's graph JSON
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from ..events import Event, EventBus
from .plan import Plan, strip_markup

MAX_ACTIONS_SHOWN = 4      # how many are drawn inside a node
MAX_ACTIONS_KEPT = 300     # how many are kept per node (the inspector shows these)
MAX_TEXT_KEPT = 8000       # per action/detail, so one huge tool result can't bloat memory
DISPLAY_TEXT = 300         # what goes into the live snapshot; full text comes from /api/run
MAX_HISTORY = 20           # previous requests kept for the history rail

ROUTE_LABELS = {
    "task": "Send to {backend}",
    "plan": "Plan with {backend} (no changes)",
    "execute_plan": "Execute plan with {backend}",
}


@dataclass
class Node:
    id: str
    kind: str  # request | route | agent | step | result
    label: str
    status: str = "pending"  # pending | running | done | failed
    detail: str = ""
    actions: list[dict] = field(default_factory=list)  # newest last: {"tool", "text", "status"}
    action_count: int = 0


@dataclass
class Lane:
    """One track of work. An automation and the agent run on separate threads, so they
    get separate graphs: otherwise whichever starts last takes over the canvas and the
    other's events land in the wrong picture."""
    run: int = 0
    started: float = field(default_factory=time.time)
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    show_plan: bool = False
    executing_plan: bool = False


MAX_LANES = 4  # concurrent tasks kept on the canvas; older finished ones go to history


class GraphTracker:
    def __init__(self, bus: EventBus, plan: Plan) -> None:
        self.bus = bus
        self.plan = plan
        self._lock = threading.RLock()
        self._runs = itertools.count(1)
        self.history: deque[dict] = deque(maxlen=MAX_HISTORY)  # finished graphs, newest last
        # One lane per task: the agent's tasks (t1, t2...), automations ("macro") and
        # local commands ("main") all get their own graph so they never overwrite each other.
        self.lanes: dict[str, Lane] = {"main": Lane()}
        self._current = "main"    # lane the event being handled belongs to
        self.active = "main"      # lane the canvas follows by default
        for topic in ("route", "agent", "plan", "macro_step", "macro_done"):
            bus.subscribe(topic, self._on_event)

    # -- the lane currently being written to ----------------------------------------
    @property
    def lane(self) -> Lane:
        if self._current not in self.lanes:
            self.lanes[self._current] = Lane()
            self._evict_lanes()
        return self.lanes[self._current]

    def _evict_lanes(self) -> None:
        """Keep the canvas to a handful of lanes; finished ones are still in history."""
        while len(self.lanes) > MAX_LANES:
            oldest = min((k for k in self.lanes if k != self._current),
                         key=lambda k: self.lanes[k].started, default=None)
            if oldest is None:
                return
            lane = self.lanes.pop(oldest)
            if any(n.kind in ("agent", "step", "result") for n in lane.nodes.values()):
                self.history.append(self._full_dict(lane))

    @property
    def run(self) -> int:
        return self.lane.run

    @property
    def started(self) -> float:
        return self.lane.started

    @property
    def nodes(self) -> dict[str, Node]:
        return self.lane.nodes

    @property
    def edges(self) -> list[tuple[str, str]]:
        return self.lane.edges

    @edges.setter
    def edges(self, value: list[tuple[str, str]]) -> None:
        self.lane.edges = value

    @property
    def show_plan(self) -> bool:
        return self.lane.show_plan

    @show_plan.setter
    def show_plan(self, value: bool) -> None:
        self.lane.show_plan = value

    @property
    def executing_plan(self) -> bool:
        return self.lane.executing_plan

    @executing_plan.setter
    def executing_plan(self, value: bool) -> None:
        self.lane.executing_plan = value

    # -- state --------------------------------------------------------------------
    def _reset(self, run: int) -> None:
        # Keep the finished graph so the previous request can still be looked at, unless
        # nothing happened in it: a local command that found no match publishes a route and
        # then falls through to the agent, and that stub shouldn't become a history entry.
        if self._did_something():
            self.history.append(self._full_dict())
        self.lanes[self._current] = Lane(run=run)

    def _did_something(self) -> bool:
        nodes = self.lane.nodes
        if not nodes:
            return False
        return any(n.kind in ("agent", "step", "result") or n.actions for n in nodes.values())

    def _add(self, node: Node, parent: str | None = None) -> Node:
        self.nodes[node.id] = node
        if parent and (parent, node.id) not in self.edges:
            self.edges.append((parent, node.id))
        return node

    def _full_dict(self, lane: Lane | None = None) -> dict:
        """Everything, untruncated: what /api/run and the inspector panel serve."""
        lane = lane or self.lane
        return {
            "run": lane.run,
            "started": lane.started,
            "label": next((n.label for n in lane.nodes.values() if n.kind == "request"), ""),
            "nodes": [asdict(n) for n in lane.nodes.values()],
            "edges": [{"id": f"{s}->{t}", "source": s, "target": t} for s, t in lane.edges],
        }

    @staticmethod
    def _display(full: dict) -> dict:
        """Shorten text for the wire; the full version stays available from /api/run."""
        for node in full["nodes"]:
            node["detail"] = node["detail"][:DISPLAY_TEXT]
            actions = node["actions"]
            node["actions_kept"] = len(actions)
            node["actions"] = [{**a, "text": a["text"][:DISPLAY_TEXT]} for a in actions[-MAX_ACTIONS_SHOWN:]]
        return full

    def to_dict(self) -> dict:
        """The live snapshot: the active lane, plus every lane and the request history."""
        with self._lock:
            lanes = {name: self._display(self._full_dict(lane)) for name, lane in self.lanes.items()}
            active = self.active
            history = [
                {"run": g["run"], "label": g["label"], "started": g["started"],
                 "status": next((n["status"] for n in g["nodes"] if n["kind"] == "result"), "done")}
                for g in list(self.history)[::-1]
            ]
        snapshot = dict(lanes.get(active) or next(iter(lanes.values())))
        snapshot["lane"] = active
        snapshot["lanes"] = {name: {**graph, "title": _lane_title(name, graph)} for name, graph in lanes.items()
                             if graph["nodes"]}
        snapshot["history"] = history
        return snapshot

    def run_detail(self, run: int | None = None) -> dict | None:
        """One run in full, for the inspector. None = the lane the canvas is following."""
        with self._lock:
            if run is None:
                return self._full_dict(self.lanes[self.active])
            for lane in self.lanes.values():
                if lane.run == run:
                    return self._full_dict(lane)
            return next((g for g in self.history if g["run"] == run), None)

    # -- events -------------------------------------------------------------------
    def _lane_for(self, event: Event) -> str:
        """Which task's graph this event belongs to."""
        if event.topic in ("macro_step", "macro_done"):
            return "macro"
        if event.topic == "route" and event.data.get("route") == "macro":
            return "macro"
        task = event.data.get("task")
        if task:
            return str(task)
        if event.topic == "plan":
            # Plan edits belong with whichever task is executing the plan, else the lane
            # the canvas is on (a plan being drafted or rearranged).
            return self.active if self.active in self.lanes else "main"
        return self.active if event.topic != "route" and self.active in self.lanes else "main"

    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._current = self._lane_for(event)
            handler = getattr(self, f"_on_{event.topic}")
            if handler(event.data) is False:
                return
            self.active = self._current  # the canvas follows whatever just moved
            snapshot = self.to_dict()
        self.bus.publish("graph", **snapshot)

    def _on_route(self, d: dict) -> None:
        self._reset(next(self._runs))
        self._add(Node("request", "request", d["utterance"], "done"))
        if d["route"] == "local":
            self._add(Node("route", "route", f"Local command: {d['name'].replace('_', ' ')}", "done",
                           "answered on this computer, no model call"), "request")
            return
        if d["route"] == "macro":
            self._add(Node("route", "route", f"Automation: {d['name']}", "done",
                           "scripted steps, no model call"), "request")
            previous = "route"
            for i, label in enumerate(d.get("steps", []), 1):
                self._add(Node(f"step{i}", "step", f"Step {i}: {label}"), previous)
                previous = f"step{i}"
            return
        label = ROUTE_LABELS.get(d["name"], "Send to {backend}").format(backend=d.get("backend", "agent"))
        self._add(Node("route", "route", label, "done"), "request")
        self.executing_plan = d["name"] == "execute_plan"
        self.show_plan = self.executing_plan or d["name"] == "plan"
        if self.executing_plan:
            self._sync_steps()

    def _on_agent(self, d: dict) -> bool | None:
        kind = d["kind"]
        if kind == "local":  # results of a local command (found files, duplicates...)
            if "route" not in self.nodes:
                return False
            self._add_action("route", d.get("tool", "local"), d.get("text", ""), "done")
            return None
        if kind == "start":
            if "request" not in self.nodes:  # started without a route event
                self._on_route({"utterance": d.get("text", ""), "route": "agent", "name": "task", "backend": d["backend"]})
            if not self.executing_plan:
                self._add(Node("agent", "agent", d["backend"], "running", "thinking"), "route")
        elif kind == "tool":
            self._add_action(self._anchor(), d.get("tool", "tool"), d.get("text", ""), "running")
        elif kind == "text":
            text = _clean(d.get("text", ""))
            anchor = self.nodes.get(self._anchor())
            if anchor and text:
                anchor.detail = text[:MAX_TEXT_KEPT]
        elif kind in ("result", "error"):
            self._finish(kind == "result", d.get("text", ""))
        else:
            return False
        return None

    def _on_macro_step(self, d: dict) -> bool | None:
        node = self.nodes.get(f"step{d['index'] + 1}")
        if not node:
            return False
        node.status = d["status"]
        if d.get("detail"):
            node.detail = d["detail"][:MAX_TEXT_KEPT]
        return None

    def _on_macro_done(self, d: dict) -> bool | None:
        if "request" not in self.nodes:
            return False
        self._finish(d["ok"], d.get("text", ""), untouched="done" if d["ok"] else "pending")
        return None

    def _finish(self, ok: bool, text: str, untouched: str | None = None) -> None:
        for node in self.nodes.values():
            if node.status == "running":
                node.status = "done" if ok else "failed"
            elif untouched and node.status == "pending":
                node.status = untouched  # an automation that stopped early leaves later steps pending
            for action in node.actions:
                if action["status"] == "running":
                    action["status"] = "done" if ok else "failed"
        leaves = self._leaf_ids()
        self._add(Node("result", "result", "Done" if ok else "Problem", "done" if ok else "failed",
                       _clean(text)[:MAX_TEXT_KEPT]))
        for leaf in leaves:
            self.edges.append((leaf, "result"))
        # The finished request, in full, for the work history (assistant/history).
        self.bus.publish("run_finished", lane=self._current, graph=self._full_dict())

    def _on_plan(self, d: dict) -> bool | None:
        if "request" not in self.nodes:
            # A plan with no request behind it: restored from the last session, or edited
            # before anything ran. Draw it on its own so it can be seen and rearranged.
            if not d.get("steps"):
                return False
            self._reset(next(self._runs))
            self._add(Node("request", "request", d.get("title") or "Plan", "done"))
            self._add(Node("route", "route", "Current plan", "done",
                           "not running yet: drag steps to reorder, or say go ahead"), "request")
            self.show_plan = True
        if not self.show_plan and not self.executing_plan:
            self.show_plan = bool(d.get("steps"))
        if not self.show_plan:
            return False
        self._sync_steps()
        return None

    # -- helpers ------------------------------------------------------------------
    def _sync_steps(self) -> None:
        steps = self.plan.to_dict()["steps"]
        stale = {nid for nid in self.nodes if nid.startswith("step") and int(nid[4:]) > len(steps)}
        for nid in stale:
            self.nodes.pop(nid)
        self.edges = [(s, t) for s, t in self.edges if s not in stale and not t.startswith("step")]
        root = "route" if self.executing_plan else ("agent" if "agent" in self.nodes else "route")
        for i, step in enumerate(steps, 1):
            nid = f"step{i}"
            node = self.nodes.get(nid) or self._add(Node(nid, "step", ""))
            node.label, node.status = f"Step {i}: {step['text']}", step["status"]
            if node.status in ("done", "failed"):
                for action in node.actions:
                    if action["status"] == "running":
                        action["status"] = node.status
            for dep in step["after"] or []:
                self.edges.append((f"step{dep}", nid))
            if not step["after"]:
                self.edges.append((root, nid))

    def _anchor(self) -> str:
        """The node new actions belong to: the running plan step, else the agent node."""
        if self.show_plan or self.executing_plan:
            running = [nid for nid, n in self.nodes.items() if n.kind == "step" and n.status == "running"]
            if running:
                return running[-1]
        if "agent" in self.nodes:
            return "agent"
        return "route"

    def _add_action(self, anchor_id: str, tool: str, text: str, status: str) -> None:
        anchor = self.nodes.get(anchor_id)
        if not anchor:
            return
        for action in anchor.actions:
            if action["status"] == "running":
                action["status"] = "done"
        anchor.actions.append({"tool": tool, "text": text[:MAX_TEXT_KEPT], "status": status})
        anchor.action_count += 1
        # Keep a long tail: the node shows the last few, the inspector shows these.
        del anchor.actions[:-MAX_ACTIONS_KEPT]

    def _leaf_ids(self) -> list[str]:
        sources = {s for s, _ in self.edges}
        return [nid for nid in self.nodes if nid not in sources and nid != "request"] or ["route"]


def _lane_title(name: str, graph: dict) -> str:
    if name == "macro":
        return "Automation"
    if name == "main":
        return "Nova"
    running = any(n["status"] == "running" for n in graph["nodes"])
    return f"Task {name.lstrip('t')}{'' if running else ' (done)'}"


def _clean(text: str) -> str:
    """Agent text without plan blocks, step markers or line breaks."""
    return " ".join(strip_markup(text).split())


def _demo() -> None:
    """Simulate a planned run and print the graph after each phase."""
    bus = EventBus()
    plan = Plan()
    tracker = GraphTracker(bus, plan)

    bus.publish("route", utterance="let's plan a notes app", route="agent", name="plan", backend="Claude Code")
    bus.publish("agent", kind="start", backend="Claude Code", text="plan: notes app")
    plan.replace_from_block("[[PLAN: Notes app]]\n1. Create project\n2. Build API (after 1)\n3. Build UI (after 1)\n4. Tests (after 2, 3)\n[[/PLAN]]")
    bus.publish("plan", **plan.to_dict())
    bus.publish("agent", kind="result", backend="Claude Code", text="Here's a four step plan.")
    print("# after planning\n" + json.dumps(tracker.to_dict(), indent=2))

    bus.publish("route", utterance="go ahead", route="agent", name="execute_plan", backend="Claude Code")
    plan.begin_execution()
    bus.publish("plan", **plan.to_dict())
    bus.publish("agent", kind="start", backend="Claude Code", text="Notes app")
    bus.publish("agent", kind="tool", backend="Claude Code", tool="Write", text="package.json")
    plan.apply_markers("[[STEP 1 DONE]] [[STEP 2 START]]")
    bus.publish("plan", **plan.to_dict())
    bus.publish("agent", kind="tool", backend="Claude Code", tool="Edit", text="api.py")
    print("# mid-execution\n" + json.dumps(tracker.to_dict(), indent=2))


if __name__ == "__main__":
    _demo()
