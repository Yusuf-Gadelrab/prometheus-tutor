"""
Concept-graph engine: loads graph.json and computes node states, remediation
paths, and mastery stats from quiz results.

Pure stdlib, no external deps, no Ollama dependency. This is the part that
must be provably correct on its own (see test_graph_engine.py).

States:
  "ok"       - no evidence of a problem
  "shaky"    - student answered a question mapped to this concept incorrectly
  "at_risk"  - not shaky itself, but depends (directly or transitively) on a
               shaky concept, so mastery here is questionable
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.path.join(HERE, "graph.json")
QUIZ_PATH = os.path.join(HERE, "quiz.json")


def load_graph(path=GRAPH_PATH):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    nodes = {n["id"]: n for n in data["nodes"]}
    return nodes


def load_quiz(path=QUIZ_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["questions"]


def build_dependents(nodes):
    """Map concept_id -> list of concept_ids that list it as a prereq
    (i.e. the concepts that DEPEND ON it and would be at risk if it's shaky)."""
    dependents = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for prereq in node.get("prereqs", []):
            if prereq not in dependents:
                raise ValueError(f"Unknown prereq '{prereq}' referenced by '{nid}'")
            dependents[prereq].append(nid)
    return dependents


def topo_levels(nodes):
    """Return dict concept_id -> depth (longest path from a root), used for
    layout and for ordering remediation. Raises on cycles."""
    memo = {}

    def depth(nid, stack=None):
        if nid in memo:
            return memo[nid]
        stack = stack or set()
        if nid in stack:
            raise ValueError(f"Cycle detected in prerequisite graph at '{nid}'")
        stack = stack | {nid}
        prereqs = nodes[nid].get("prereqs", [])
        d = 0 if not prereqs else 1 + max(depth(p, stack) for p in prereqs)
        memo[nid] = d
        return d

    for nid in nodes:
        depth(nid)
    return memo


def compute_states(nodes, dependents, shaky_ids):
    """
    shaky_ids: set/list of concept ids the student got wrong.
    Returns dict concept_id -> "ok" | "shaky" | "at_risk".

    Propagation: BFS forward through the dependents graph starting from every
    shaky node. Anything reached that isn't itself shaky becomes "at_risk".
    """
    shaky_ids = set(shaky_ids)
    for sid in shaky_ids:
        if sid not in nodes:
            raise ValueError(f"Unknown concept id in shaky set: '{sid}'")

    states = {nid: "ok" for nid in nodes}
    for sid in shaky_ids:
        states[sid] = "shaky"

    at_risk = set()
    visited = set()
    frontier = list(shaky_ids)
    while frontier:
        cur = frontier.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for dep in dependents.get(cur, []):
            if dep not in shaky_ids:
                at_risk.add(dep)
            if dep not in visited:
                frontier.append(dep)

    for nid in at_risk:
        states[nid] = "at_risk"

    return states


def prereq_chain(nodes, concept_id):
    """Full ancestor chain (all transitive prereqs) for a concept, ordered
    root-first, deduplicated. Used as RAG retrieval context."""
    if concept_id not in nodes:
        raise ValueError(f"Unknown concept id: '{concept_id}'")

    order = []
    seen = set()

    def visit(nid):
        for p in nodes[nid].get("prereqs", []):
            if p not in seen:
                visit(p)
                seen.add(p)
                order.append(p)

    visit(concept_id)
    return order


def score_quiz(quiz_questions, answers):
    """
    quiz_questions: list of question dicts (from quiz.json) each with
      id, concept, choices, answer (index of correct choice).
    answers: dict question_id -> chosen index.
    Returns (shaky_concept_ids, per_question_results).
    """
    shaky = set()
    results = []
    for q in quiz_questions:
        chosen = answers.get(q["id"])
        correct = chosen == q["answer"]
        if not correct:
            shaky.add(q["concept"])
        results.append({
            "id": q["id"],
            "concept": q["concept"],
            "correct": correct,
            "chosen": chosen,
        })
    return shaky, results


# --------------------------------------------------------------------------
# Adaptive curriculum map: turn a state map into an ordered plan of attack.
# This is the mechanic from "Adaptive Curriculum Maps" (SIGCSE TS 2026).
# --------------------------------------------------------------------------

def learning_path(nodes, states, levels=None):
    """
    Ordered remediation plan over every concept that is not 'ok'.

    Ordering is topological (shallowest prerequisite depth first), so the
    student is always sent to the ROOT CAUSE before its dependents. If both
    'functions' and 'recursion' are shaky, 'functions' is always step 1 —
    re-teaching recursion first would be wasted instruction.

    Returns a list of step dicts:
      {order, concept, name, state, depth, reason, unblocks}
    """
    levels = levels or topo_levels(nodes)
    broken = [nid for nid, st in states.items() if st != "ok"]

    def sort_key(nid):
        # shaky before at_risk at the same depth: fix causes before symptoms
        return (levels[nid], 0 if states[nid] == "shaky" else 1, nodes[nid]["name"])

    broken.sort(key=sort_key)

    broken_set = set(broken)
    path = []
    for i, nid in enumerate(broken, start=1):
        blocked_prereqs = [p for p in nodes[nid].get("prereqs", []) if p in broken_set]
        if states[nid] == "shaky":
            reason = "answered incorrectly in the diagnostic"
        elif blocked_prereqs:
            names = ", ".join(nodes[p]["name"] for p in blocked_prereqs)
            reason = f"builds directly on {names}"
        else:
            reason = "downstream of a shaky prerequisite"
        unblocks = sorted(
            d for d in _descendants(nodes, nid) if d in broken_set
        )
        path.append({
            "order": i,
            "concept": nid,
            "name": nodes[nid]["name"],
            "state": states[nid],
            "depth": levels[nid],
            "reason": reason,
            "unblocks": unblocks,
        })
    return path


def _descendants(nodes, concept_id):
    """All concepts transitively depending on concept_id."""
    dependents = build_dependents(nodes)
    out, frontier = set(), [concept_id]
    while frontier:
        cur = frontier.pop()
        for d in dependents.get(cur, []):
            if d not in out:
                out.add(d)
                frontier.append(d)
    return out


def ready_next(nodes, states):
    """Concepts the student has NOT been shown to struggle with and whose
    prerequisites are all 'ok' — i.e. safe to move forward on right now."""
    out = []
    for nid, node in nodes.items():
        if states.get(nid) != "ok":
            continue
        if all(states.get(p) == "ok" for p in node.get("prereqs", [])):
            out.append(nid)
    return sorted(out)


def mastery_report(nodes, states, results=None):
    """Headline numbers for the UI + CLI."""
    counts = {"ok": 0, "shaky": 0, "at_risk": 0}
    for st in states.values():
        counts[st] = counts.get(st, 0) + 1
    total = len(nodes) or 1
    report = {
        "total_concepts": len(nodes),
        "ok": counts["ok"],
        "shaky": counts["shaky"],
        "at_risk": counts["at_risk"],
        "mastery_pct": round(100.0 * counts["ok"] / total, 1),
        "coverage_pct": None,
    }
    if results is not None:
        answered = [r for r in results if r["chosen"] is not None]
        correct = [r for r in answered if r["correct"]]
        report["questions"] = len(results)
        report["answered"] = len(answered)
        report["correct"] = len(correct)
        report["coverage_pct"] = round(100.0 * len(correct) / (len(results) or 1), 1)
    return report
