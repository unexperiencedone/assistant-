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
from dataclasses import asdict, dataclass, field

from ..events import Event, EventBus
from .plan import Plan, strip_markup

MAX_ACTIONS_SHOWN = 4

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


class GraphTracker:
    def __init__(self, bus: EventBus, plan: Plan) -> None:
        self.bus = bus
        self.plan = plan
        self._lock = threading.RLock()
        self._runs = itertools.count(1)
        self._reset(0)
        for topic in ("route", "agent", "plan", "macro_step", "macro_done"):
            bus.subscribe(topic, self._on_event)

    # -- state --------------------------------------------------------------------
    def _reset(self, run: int) -> None:
        self.run = run
        self.nodes: dict[str, Node] = {}
        self.edges: list[tuple[str, str]] = []
        self.show_plan = False
        self.executing_plan = False

    def _add(self, node: Node, parent: str | None = None) -> Node:
        self.nodes[node.id] = node
        if parent and (parent, node.id) not in self.edges:
            self.edges.append((parent, node.id))
        return node

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "run": self.run,
                "nodes": [asdict(n) for n in self.nodes.values()],
                "edges": [{"id": f"{s}->{t}", "source": s, "target": t} for s, t in self.edges],
            }

    # -- events -------------------------------------------------------------------
    def _on_event(self, event: Event) -> None:
        with self._lock:
            handler = getattr(self, f"_on_{event.topic}")
            if handler(event.data) is False:
                return
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
                anchor.detail = text[:140]
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
            node.detail = d["detail"][:140]
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
                       _clean(text)[:300]))
        for leaf in leaves:
            self.edges.append((leaf, "result"))

    def _on_plan(self, d: dict) -> bool | None:
        if "request" not in self.nodes:
            return False
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
        anchor.actions.append({"tool": tool, "text": text[:120], "status": status})
        anchor.action_count += 1
        del anchor.actions[:-MAX_ACTIONS_SHOWN]

    def _leaf_ids(self) -> list[str]:
        sources = {s for s, _ in self.edges}
        return [nid for nid in self.nodes if nid not in sources and nid != "request"] or ["route"]


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
