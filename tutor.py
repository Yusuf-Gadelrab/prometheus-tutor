#!/usr/bin/env python3
"""
Adaptive CS Tutor - command line interface.

Same engine as the web app, no browser required. Useful for judges, for CI,
and as the offline-proof demo (works with Wi-Fi off).

    python3 tutor.py doctor                     # is the local model reachable?
    python3 tutor.py demo                       # scripted end-to-end run
    python3 tutor.py demo --no-llm --lang ar    # offline, Arabic
    python3 tutor.py quiz                       # take the diagnostic yourself
    python3 tutor.py explain recursion --lang ar
"""
import argparse
import sys

import graph_engine as ge
import explainer

GOLD = "\033[38;5;179m"
BONE = "\033[38;5;253m"
DIM = "\033[38;5;244m"
AMBER = "\033[38;5;178m"
RED = "\033[38;5;131m"
GREEN = "\033[38;5;108m"
OFF = "\033[0m"

STATE_COLOR = {"ok": GREEN, "shaky": AMBER, "at_risk": RED}


def rule(width=68):
    print(f"{DIM}{'-' * width}{OFF}")


def header(text):
    print()
    print(f"{GOLD}{text.upper()}{OFF}")
    rule()


def banner():
    print()
    print(f"{GOLD}  ADAPTIVE CS TUTOR{OFF}  {DIM}DHAHAB . applied CS-education research{OFF}")
    print(f"{DIM}  prerequisite-graph diagnostics + local qwen3-fast, $0 stack{OFF}")


def print_report(report):
    print(f"  {BONE}{report['ok']}{OFF} mastered   "
          f"{AMBER}{report['shaky']}{OFF} shaky   "
          f"{RED}{report['at_risk']}{OFF} at risk   "
          f"{DIM}of {report['total_concepts']} concepts{OFF}")
    if report.get("questions"):
        print(f"  {DIM}diagnostic: {report['correct']}/{report['questions']} correct"
              f"  ({report['coverage_pct']}%){OFF}")


def print_states(nodes, states):
    broken = [(nid, st) for nid, st in states.items() if st != "ok"]
    if not broken:
        print(f"  {GREEN}every concept clear{OFF}")
        return
    for nid, st in sorted(broken, key=lambda x: (x[1], x[0])):
        c = STATE_COLOR[st]
        print(f"  {c}{st:<8}{OFF} {nodes[nid]['name']}")


def print_path(path):
    if not path:
        print(f"  {GREEN}no remediation needed{OFF}")
        return
    for step in path:
        c = STATE_COLOR[step["state"]]
        unblocks = f"{DIM} (unblocks {len(step['unblocks'])}){OFF}" if step["unblocks"] else ""
        print(f"  {GOLD}{step['order']:02d}{OFF}  {BONE}{step['name']}{OFF} "
              f"{c}[{step['state']}]{OFF}{unblocks}")
        print(f"      {DIM}{step['reason']}{OFF}")


def cmd_doctor(args):
    banner()
    header("environment")
    nodes = ge.load_graph()
    quiz = ge.load_quiz()
    print(f"  graph.json      {BONE}{len(nodes)} concepts{OFF}")
    print(f"  quiz.json       {BONE}{len(quiz)} questions{OFF}")
    levels = ge.topo_levels(nodes)
    print(f"  DAG check       {GREEN}acyclic, depth {max(levels.values())}{OFF}")
    probe = explainer.ollama_available()
    if probe["reachable"] and probe["model_present"]:
        print(f"  ollama          {GREEN}{probe['model']} ready at {explainer.OLLAMA_HOST}{OFF}")
    elif probe["reachable"]:
        print(f"  ollama          {AMBER}reachable but '{probe['model']}' not pulled"
              f" - canned explanations will be used{OFF}")
    else:
        print(f"  ollama          {AMBER}not reachable - canned explanations will be used{OFF}")
    print()
    return 0


def cmd_explain(args):
    nodes = ge.load_graph()
    if args.concept not in nodes:
        print(f"unknown concept '{args.concept}'. Try one of:", file=sys.stderr)
        print("  " + ", ".join(sorted(nodes)), file=sys.stderr)
        return 1
    chain = ge.prereq_chain(nodes, args.concept)
    banner()
    header(f"explaining: {nodes[args.concept]['name']}")
    print(f"  {DIM}retrieved context: "
          f"{' -> '.join(chain) if chain else 'foundational, no prerequisites'}{OFF}")
    print()
    text, source = explainer.explain(nodes, args.concept, lang=args.lang, no_llm=args.no_llm)
    tag = "qwen3-fast (local)" if source == "llm" else "canned fallback (offline)"
    print(f"  {BONE}{text}{OFF}")
    print()
    print(f"  {GOLD}source: {tag}{OFF}")
    print()
    return 0


def _simulated_answers(quiz, wrong_concepts):
    """Answer every question correctly except those mapped to wrong_concepts."""
    answers = {}
    for q in quiz:
        if q["concept"] in wrong_concepts:
            answers[q["id"]] = (q["answer"] + 1) % len(q["choices"])
        else:
            answers[q["id"]] = q["answer"]
    return answers


def cmd_demo(args):
    nodes = ge.load_graph()
    quiz = ge.load_quiz()
    dependents = ge.build_dependents(nodes)
    levels = ge.topo_levels(nodes)

    wrong = set(args.miss)
    banner()
    header("1. diagnostic")
    print(f"  simulated student misses: {AMBER}{', '.join(sorted(wrong))}{OFF}")
    answers = _simulated_answers(quiz, wrong)
    shaky, results = ge.score_quiz(quiz, answers)
    states = ge.compute_states(nodes, dependents, shaky)
    report = ge.mastery_report(nodes, states, results)
    print()
    print_report(report)

    header("2. graph propagation")
    print(f"  {DIM}wrong answers mark concepts shaky; everything downstream is at risk{OFF}")
    print()
    print_states(nodes, states)

    header("3. adaptive learning path")
    print(f"  {DIM}topological order - root causes before the symptoms they cause{OFF}")
    print()
    path = ge.learning_path(nodes, states, levels)
    print_path(path)

    if not path:
        return 0

    target = path[0]["concept"]
    header(f"4. graph-augmented explanation: {nodes[target]['name']}")
    chain = ge.prereq_chain(nodes, target)
    print(f"  {DIM}retrieval = prerequisite chain, not embeddings:{OFF}")
    print(f"  {DIM}{' -> '.join(chain) if chain else 'foundational'}"
          f"{' -> ' if chain else ''}{OFF}{GOLD}{target}{OFF}")
    print()
    text, source = explainer.explain(nodes, target, lang=args.lang,
                                     no_llm=args.no_llm, states=states)
    print(f"  {BONE}{text}{OFF}")
    print()
    print(f"  {GOLD}source: {'qwen3-fast (local)' if source == 'llm' else 'canned fallback (offline)'}{OFF}")

    header("5. mastery re-check")
    q, qsource = explainer.practice_question(nodes, target, quiz, lang=args.lang,
                                             no_llm=args.no_llm, states=states)
    if q:
        print(f"  {BONE}{q['prompt']}{OFF}")
        for i, c in enumerate(q["choices"]):
            mark = f"{GREEN} <- correct{OFF}" if i == q["answer"] else ""
            print(f"    {DIM}{chr(97 + i)}){OFF} {c}{mark}")
        print()
        print(f"  {GOLD}source: {'generated by qwen3-fast' if qsource == 'llm' else 'question bank (offline)'}{OFF}")
    else:
        print(f"  {DIM}no offline practice question for this concept{OFF}")

    header("6. student proves it - map re-propagates")
    shaky2 = set(shaky) - {target}
    states2 = ge.compute_states(nodes, dependents, shaky2)
    report2 = ge.mastery_report(nodes, states2, results)
    print(f"  {DIM}after mastering {target}:{OFF}")
    print()
    print_report(report2)
    fixed = report2["ok"] - report["ok"]
    print()
    print(f"  {GREEN}one root-cause lesson cleared {fixed} concept(s) on the map{OFF}")
    print()
    return 0


def cmd_warm(args):
    """Pre-generate explanations so a live demo has no first-token wait.

    A 30B model on a laptop takes 10-45s for a cold answer (Arabic is the slow
    one). The server memoises per concept+language, so warming the concepts you
    plan to show makes them instant on camera. Nothing is faked: the same
    generated text is what gets served.
    """
    nodes = ge.load_graph()
    banner()
    header("warming cache")
    for concept in args.concepts:
        if concept not in nodes:
            print(f"  {RED}unknown concept {concept}{OFF}")
            continue
        for lang in args.langs:
            import time
            t0 = time.time()
            _, source = explainer.explain(nodes, concept, lang=lang)
            mark = f"{GREEN}ok{OFF}" if source == "llm" else f"{AMBER}fallback{OFF}"
            print(f"  {BONE}{concept:<24}{OFF} {DIM}{lang}{OFF}  {mark}  "
                  f"{DIM}{time.time() - t0:.1f}s{OFF}")
    print()
    print(f"  {DIM}note: the cache lives in the process, so warm inside the same"
          f" server you demo with (or call /api/explain once per concept){OFF}")
    print()
    return 0


def cmd_quiz(args):
    nodes = ge.load_graph()
    quiz = ge.load_quiz()
    dependents = ge.build_dependents(nodes)
    banner()
    header("diagnostic")
    answers = {}
    for i, q in enumerate(quiz, 1):
        print(f"\n  {GOLD}{i:02d}{OFF}  {BONE}{q['prompt']}{OFF}")
        for j, c in enumerate(q["choices"]):
            print(f"      {DIM}{chr(97 + j)}){OFF} {c}")
        while True:
            raw = input(f"  {GOLD}>{OFF} ").strip().lower()
            if raw and raw[0].isalpha() and 0 <= ord(raw[0]) - 97 < len(q["choices"]):
                answers[q["id"]] = ord(raw[0]) - 97
                break
            print(f"  {DIM}enter a letter{OFF}")
    shaky, results = ge.score_quiz(quiz, answers)
    states = ge.compute_states(nodes, dependents, shaky)
    header("result")
    print_report(ge.mastery_report(nodes, states, results))
    header("your path")
    print_path(ge.learning_path(nodes, states))
    print()
    print(f"  {DIM}explain any step:  python3 tutor.py explain <concept>{OFF}")
    print()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Adaptive CS Tutor CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check graph + local model")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("explain", help="explain one concept")
    e.add_argument("concept")
    e.add_argument("--lang", choices=["en", "ar"], default="en")
    e.add_argument("--no-llm", action="store_true")
    e.set_defaults(func=cmd_explain)

    dm = sub.add_parser("demo", help="scripted end-to-end run")
    dm.add_argument("--miss", nargs="+", default=["functions", "lists"],
                    help="concept ids the simulated student gets wrong")
    dm.add_argument("--lang", choices=["en", "ar"], default="en")
    dm.add_argument("--no-llm", action="store_true")
    dm.set_defaults(func=cmd_demo)

    w = sub.add_parser("warm", help="pre-generate explanations before a live demo")
    w.add_argument("concepts", nargs="*", default=["functions", "recursion", "lists"])
    w.add_argument("--langs", nargs="+", choices=["en", "ar"], default=["en", "ar"])
    w.set_defaults(func=cmd_warm)

    qz = sub.add_parser("quiz", help="take the diagnostic interactively")
    qz.set_defaults(func=cmd_quiz)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
