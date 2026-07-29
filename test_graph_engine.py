"""
Standalone tests for graph_engine.py — no Ollama, no server, no network.
Run: python3 test_graph_engine.py
"""
import unittest
import graph_engine as ge


class TestGraphLoad(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        self.dependents = ge.build_dependents(self.nodes)

    def test_graph_loads_and_has_enough_nodes(self):
        self.assertGreaterEqual(len(self.nodes), 28)

    def test_no_missing_prereq_ids(self):
        # build_dependents raises if a prereq references an unknown id;
        # calling it in setUp already validates this, so just re-assert shape.
        for nid, deps in self.dependents.items():
            self.assertIsInstance(deps, list)

    def test_no_cycles(self):
        # topo_levels raises ValueError on cycles
        levels = ge.topo_levels(self.nodes)
        self.assertEqual(len(levels), len(self.nodes))

    def test_root_nodes_have_no_prereqs(self):
        self.assertEqual(self.nodes["variables"]["prereqs"], [])


class TestPropagation(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        self.dependents = ge.build_dependents(self.nodes)

    def test_no_shaky_all_ok(self):
        states = ge.compute_states(self.nodes, self.dependents, [])
        self.assertTrue(all(s == "ok" for s in states.values()))

    def test_root_shaky_marks_self_shaky(self):
        states = ge.compute_states(self.nodes, self.dependents, ["variables"])
        self.assertEqual(states["variables"], "shaky")

    def test_root_shaky_propagates_to_descendants(self):
        states = ge.compute_states(self.nodes, self.dependents, ["variables"])
        # data_types depends directly on variables
        self.assertEqual(states["data_types"], "at_risk")
        # lists depends on data_types (transitively on variables)
        self.assertEqual(states["lists"], "at_risk")
        # deep transitive descendant: sorting depends on lists
        self.assertEqual(states["sorting"], "at_risk")

    def test_unrelated_branch_stays_ok(self):
        # marking 'recursion' shaky should not affect 'lists' (no path)
        states = ge.compute_states(self.nodes, self.dependents, ["recursion"])
        self.assertEqual(states["lists"], "ok")

    def test_leaf_shaky_has_no_descendants_at_risk(self):
        # file_io is a leaf (nothing depends on it)
        states = ge.compute_states(self.nodes, self.dependents, ["file_io"])
        at_risk_count = sum(1 for s in states.values() if s == "at_risk")
        self.assertEqual(at_risk_count, 0)

    def test_multiple_shaky_union_of_descendants(self):
        states = ge.compute_states(self.nodes, self.dependents, ["loops_for", "loops_while"])
        self.assertEqual(states["nested_loops"], "at_risk")
        self.assertEqual(states["loops_for"], "shaky")
        self.assertEqual(states["loops_while"], "shaky")

    def test_shaky_wins_over_at_risk_if_both_apply(self):
        # functions depends on conditionals; mark both shaky directly
        states = ge.compute_states(self.nodes, self.dependents, ["conditionals", "functions"])
        self.assertEqual(states["functions"], "shaky")
        self.assertEqual(states["conditionals"], "shaky")

    def test_unknown_shaky_id_raises(self):
        with self.assertRaises(ValueError):
            ge.compute_states(self.nodes, self.dependents, ["not_a_real_concept"])


class TestPrereqChain(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()

    def test_root_has_empty_chain(self):
        self.assertEqual(ge.prereq_chain(self.nodes, "variables"), [])

    def test_chain_includes_all_ancestors_in_order(self):
        chain = ge.prereq_chain(self.nodes, "recursion")
        # recursion depends on functions + conditionals, which trace back to variables
        self.assertIn("functions", chain)
        self.assertIn("conditionals", chain)
        self.assertIn("boolean_logic", chain)
        self.assertIn("operators", chain)
        self.assertIn("variables", chain)
        # ancestors must come before the things that depend on them
        self.assertLess(chain.index("variables"), chain.index("operators"))
        self.assertLess(chain.index("operators"), chain.index("boolean_logic"))
        self.assertLess(chain.index("boolean_logic"), chain.index("conditionals"))
        self.assertLess(chain.index("conditionals"), chain.index("functions"))

    def test_unknown_concept_raises(self):
        with self.assertRaises(ValueError):
            ge.prereq_chain(self.nodes, "nope")


class TestQuizScoring(unittest.TestCase):
    def setUp(self):
        self.quiz = ge.load_quiz()

    def test_all_correct_no_shaky(self):
        answers = {q["id"]: q["answer"] for q in self.quiz}
        shaky, results = ge.score_quiz(self.quiz, answers)
        self.assertEqual(shaky, set())
        self.assertTrue(all(r["correct"] for r in results))

    def test_all_wrong_marks_every_concept_shaky(self):
        answers = {q["id"]: (q["answer"] + 1) % len(q["choices"]) for q in self.quiz}
        shaky, results = ge.score_quiz(self.quiz, answers)
        expected = {q["concept"] for q in self.quiz}
        self.assertEqual(shaky, expected)
        self.assertTrue(all(not r["correct"] for r in results))

    def test_missing_answer_counts_as_wrong(self):
        shaky, results = ge.score_quiz(self.quiz, {})
        self.assertEqual(len(shaky), len({q["concept"] for q in self.quiz}))

    def test_quiz_has_12_questions_across_multiple_clusters(self):
        self.assertEqual(len(self.quiz), 12)
        nodes = ge.load_graph()
        clusters = {nodes[q["concept"]]["cluster"] for q in self.quiz}
        self.assertGreaterEqual(len(clusters), 4)


class TestLearningPath(unittest.TestCase):
    """The adaptive-curriculum-map ordering: root causes before symptoms."""

    def setUp(self):
        self.nodes = ge.load_graph()
        self.dependents = ge.build_dependents(self.nodes)
        self.levels = ge.topo_levels(self.nodes)

    def path_for(self, shaky):
        states = ge.compute_states(self.nodes, self.dependents, shaky)
        return states, ge.learning_path(self.nodes, states, self.levels)

    def test_clean_student_gets_empty_path(self):
        _, path = self.path_for([])
        self.assertEqual(path, [])

    def test_path_covers_exactly_the_broken_concepts(self):
        states, path = self.path_for(["functions", "lists"])
        broken = {nid for nid, st in states.items() if st != "ok"}
        self.assertEqual({s["concept"] for s in path}, broken)

    def test_root_cause_comes_before_dependent(self):
        # both shaky; functions is a prerequisite of recursion
        _, path = self.path_for(["functions", "recursion"])
        order = [s["concept"] for s in path]
        self.assertLess(order.index("functions"), order.index("recursion"))

    def test_deep_root_cause_wins_over_shallow_symptom(self):
        _, path = self.path_for(["variables"])
        self.assertEqual(path[0]["concept"], "variables")
        self.assertEqual(path[0]["state"], "shaky")

    def test_order_is_contiguous_and_one_based(self):
        _, path = self.path_for(["conditionals"])
        self.assertEqual([s["order"] for s in path], list(range(1, len(path) + 1)))

    def test_shaky_before_at_risk_at_same_depth(self):
        # loops_for and loops_while are both depth-3 siblings
        _, path = self.path_for(["loops_for"])
        same_depth = [s for s in path if s["depth"] == path[0]["depth"]]
        self.assertEqual(same_depth[0]["state"], "shaky")

    def test_unblocks_counts_only_broken_descendants(self):
        _, path = self.path_for(["functions"])
        step = next(s for s in path if s["concept"] == "functions")
        self.assertIn("recursion", step["unblocks"])
        self.assertNotIn("lists", step["unblocks"])

    def test_every_step_has_a_human_reason(self):
        _, path = self.path_for(["functions", "lists"])
        for s in path:
            self.assertTrue(s["reason"])
            self.assertIn(s["state"], ("shaky", "at_risk"))

    def test_mastering_root_cause_clears_downstream(self):
        states_before, _ = self.path_for(["lists"])
        states_after, path_after = self.path_for([])
        before_ok = sum(1 for s in states_before.values() if s == "ok")
        after_ok = sum(1 for s in states_after.values() if s == "ok")
        self.assertGreater(after_ok, before_ok)
        self.assertEqual(path_after, [])


class TestReadyNextAndReport(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        self.dependents = ge.build_dependents(self.nodes)
        self.quiz = ge.load_quiz()

    def test_ready_next_excludes_broken_and_blocked(self):
        states = ge.compute_states(self.nodes, self.dependents, ["lists"])
        ready = ge.ready_next(self.nodes, states)
        self.assertNotIn("lists", ready)
        self.assertNotIn("dicts", ready)      # at risk, blocked by lists
        self.assertIn("variables", ready)

    def test_ready_next_is_everything_when_clean(self):
        states = ge.compute_states(self.nodes, self.dependents, [])
        self.assertEqual(len(ge.ready_next(self.nodes, states)), len(self.nodes))

    def test_report_counts_sum_to_total(self):
        states = ge.compute_states(self.nodes, self.dependents, ["functions"])
        r = ge.mastery_report(self.nodes, states)
        self.assertEqual(r["ok"] + r["shaky"] + r["at_risk"], r["total_concepts"])

    def test_report_perfect_run(self):
        answers = {q["id"]: q["answer"] for q in self.quiz}
        shaky, results = ge.score_quiz(self.quiz, answers)
        states = ge.compute_states(self.nodes, self.dependents, shaky)
        r = ge.mastery_report(self.nodes, states, results)
        self.assertEqual(r["mastery_pct"], 100.0)
        self.assertEqual(r["correct"], len(self.quiz))
        self.assertEqual(r["coverage_pct"], 100.0)


class TestContentIntegrity(unittest.TestCase):
    """Content bugs are the ones that embarrass you on stage."""

    def setUp(self):
        self.nodes = ge.load_graph()
        self.quiz = ge.load_quiz()

    def test_every_quiz_concept_exists_in_graph(self):
        for q in self.quiz:
            self.assertIn(q["concept"], self.nodes)

    def test_every_quiz_answer_index_is_valid(self):
        for q in self.quiz:
            self.assertIsInstance(q["answer"], int)
            self.assertTrue(0 <= q["answer"] < len(q["choices"]))
            self.assertGreaterEqual(len(q["choices"]), 2)

    def test_quiz_ids_unique(self):
        ids = [q["id"] for q in self.quiz]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_concept_has_bilingual_canned_explanation(self):
        import explainer
        expl = explainer.load_explanations()
        for nid in self.nodes:
            self.assertIn(nid, expl, f"missing canned explanation for {nid}")
            self.assertTrue(expl[nid].get("en", "").strip())
            self.assertTrue(expl[nid].get("ar", "").strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
