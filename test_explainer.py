"""
Tests for the RAG layer. No network: the Ollama call is monkeypatched, so
these pass identically whether or not Ollama is running.
Run: python3 test_explainer.py
"""
import json
import unittest

import graph_engine as ge
import explainer


class Patched:
    """Context manager that swaps explainer._generate for a canned reply."""

    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        self.orig = explainer._generate
        self.calls = []

        def fake(prompt, **kw):
            self.calls.append(prompt)
            return self.reply(prompt) if callable(self.reply) else self.reply

        explainer._generate = fake
        return self

    def __exit__(self, *a):
        explainer._generate = self.orig
        return False


class TestThinkStripping(unittest.TestCase):
    def test_strips_reasoning_preamble(self):
        raw = "let me think about this\n</think>\n\nRecursion is a function calling itself."
        self.assertEqual(explainer._strip_thinking(raw),
                         "Recursion is a function calling itself.")

    def test_passthrough_when_no_think_tag(self):
        self.assertEqual(explainer._strip_thinking("  clean answer  "), "clean answer")

    def test_strips_up_to_last_close_tag(self):
        raw = "<think>a</think>mid</think>final answer"
        self.assertEqual(explainer._strip_thinking(raw), "final answer")


class TestScratchpadGuard(unittest.TestCase):
    """A student must never be shown the model's reasoning monologue."""

    def setUp(self):
        self.nodes = ge.load_graph()

    def test_detects_monologue(self):
        leaked = ("We are explaining functions to a student who knows conditionals.\n"
                  "Let's craft: functions group reusable code.")
        self.assertTrue(explainer._looks_like_scratchpad(leaked))

    def test_clean_answer_passes(self):
        self.assertFalse(explainer._looks_like_scratchpad(
            "A function groups code under a name so you can reuse it."))

    def test_leaked_monologue_falls_back_instead_of_being_shown(self):
        orig = explainer._generate
        try:
            explainer._generate = lambda *a, **k: None  # what the guard causes
            text, source = explainer.explain(self.nodes, "functions")
        finally:
            explainer._generate = orig
        self.assertEqual(source, "fallback")
        self.assertNotIn("We are explaining", text)

    def test_uses_chat_endpoint_with_thinking_separated(self):
        self.assertTrue(explainer.OLLAMA_URL.endswith("/api/chat"))
        with open(explainer.__file__, "r", encoding="utf-8") as f:
            self.assertIn('"think": True', f.read())


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        self.dependents = ge.build_dependents(self.nodes)

    def test_context_chain_matches_graph(self):
        ctx = explainer.build_context(self.nodes, "recursion")
        self.assertEqual(ctx["prereq_chain"], ge.prereq_chain(self.nodes, "recursion"))
        self.assertEqual(ctx["target"], "Recursion")

    def test_foundational_concept_has_no_prereqs(self):
        ctx = explainer.build_context(self.nodes, "variables")
        self.assertEqual(ctx["prereq_chain"], [])
        self.assertIn("foundational", ctx["prereq_summary"])

    def test_states_split_chain_into_solid_and_weak(self):
        states = ge.compute_states(self.nodes, self.dependents, ["conditionals"])
        ctx = explainer.build_context(self.nodes, "recursion", states)
        self.assertIn("conditionals", ctx["weak"])
        self.assertIn("variables", ctx["solid"])
        self.assertNotIn("conditionals", ctx["solid"])

    def test_prompt_contains_retrieved_prereqs_not_unrelated_ones(self):
        ctx = explainer.build_context(self.nodes, "recursion")
        prompt = explainer.build_prompt(ctx, "en")
        self.assertIn("functions", prompt)
        self.assertIn("Teach: Recursion", prompt)
        self.assertNotIn("inheritance", prompt)  # not on the path

    def test_prompt_marks_shaky_prereqs_as_off_limits(self):
        states = ge.compute_states(self.nodes, self.dependents, ["conditionals"])
        prompt = explainer.build_prompt(
            explainer.build_context(self.nodes, "recursion", states), "en")
        self.assertIn("Still shaky", prompt)
        self.assertIn("conditionals", prompt.split("Still shaky")[1])

    def test_arabic_prompt_pins_code_to_english(self):
        ctx = explainer.build_context(self.nodes, "loops_for")
        prompt = explainer.build_prompt(ctx, "ar")
        self.assertIn("بالإنجليزية", prompt)

    def test_prompt_stays_short_enough_to_avoid_rumination(self):
        # a bloated prompt makes qwen3 burn its token budget on reasoning
        ctx = explainer.build_context(self.nodes, "recursion_complexity")
        for lang in ("en", "ar"):
            self.assertLess(len(explainer.build_prompt(ctx, lang)), 500)


class TestExplain(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()

    def test_no_llm_uses_canned_text(self):
        with Patched("SHOULD NOT BE CALLED") as p:
            text, source = explainer.explain(self.nodes, "lists", no_llm=True)
        self.assertEqual(source, "fallback")
        self.assertEqual(p.calls, [])
        self.assertEqual(text, explainer.canned_explanation("lists", "en"))

    def test_llm_text_used_when_available(self):
        with Patched("A list holds many values in order."):
            text, source = explainer.explain(self.nodes, "lists")
        self.assertEqual(source, "llm")
        self.assertIn("list", text.lower())

    def test_falls_back_when_model_unreachable(self):
        with Patched(None):
            text, source = explainer.explain(self.nodes, "lists")
        self.assertEqual(source, "fallback")
        self.assertTrue(text)

    def test_arabic_fallback_is_arabic(self):
        text = explainer.canned_explanation("variables", "ar")
        self.assertTrue(any("؀" <= ch <= "ۿ" for ch in text))

    def test_unknown_concept_canned_message(self):
        self.assertIn("No explanation", explainer.canned_explanation("nope", "en"))


class TestExplainCache(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        explainer._explain_cache.clear()

    def tearDown(self):
        explainer._explain_cache.clear()

    def test_second_call_is_served_from_cache(self):
        with Patched("cached answer") as p:
            explainer.explain(self.nodes, "lists")
            text, source = explainer.explain(self.nodes, "lists")
        self.assertEqual(len(p.calls), 1)
        self.assertEqual((text, source), ("cached answer", "llm"))

    def test_cache_is_per_language(self):
        with Patched("answer") as p:
            explainer.explain(self.nodes, "lists", lang="en")
            explainer.explain(self.nodes, "lists", lang="ar")
        self.assertEqual(len(p.calls), 2)

    def test_cache_invalidated_when_shaky_set_changes(self):
        dependents = ge.build_dependents(self.nodes)
        s1 = ge.compute_states(self.nodes, dependents, [])
        s2 = ge.compute_states(self.nodes, dependents, ["data_types"])
        with Patched("answer") as p:
            explainer.explain(self.nodes, "lists", states=s1)
            explainer.explain(self.nodes, "lists", states=s2)
        self.assertEqual(len(p.calls), 2)

    def test_failures_are_not_cached(self):
        with Patched(None) as p:
            explainer.explain(self.nodes, "lists")
            explainer.explain(self.nodes, "lists")
        self.assertEqual(len(p.calls), 2)

    def test_use_cache_false_bypasses(self):
        with Patched("answer") as p:
            explainer.explain(self.nodes, "lists")
            explainer.explain(self.nodes, "lists", use_cache=False)
        self.assertEqual(len(p.calls), 2)


class TestPracticeQuestion(unittest.TestCase):
    def setUp(self):
        self.nodes = ge.load_graph()
        self.quiz = ge.load_quiz()

    def good_json(self):
        return json.dumps({
            "prompt": "What does range(3) produce?",
            "choices": ["0 1 2", "1 2 3", "3", "error"],
            "answer": 0, "why": "range is 0-based and stops before 3",
        })

    def test_parses_clean_json(self):
        q = explainer._parse_question(self.good_json())
        self.assertEqual(q["answer"], 0)
        self.assertEqual(len(q["choices"]), 4)

    def test_parses_json_wrapped_in_prose_or_fence(self):
        raw = "Sure, here you go:\n```json\n" + self.good_json() + "\n```\nHope that helps."
        self.assertIsNotNone(explainer._parse_question(raw))

    def test_rejects_wrong_choice_count(self):
        bad = json.dumps({"prompt": "x", "choices": ["a", "b"], "answer": 0})
        self.assertIsNone(explainer._parse_question(bad))

    def test_rejects_out_of_range_answer(self):
        bad = json.dumps({"prompt": "x", "choices": ["a", "b", "c", "d"], "answer": 7})
        self.assertIsNone(explainer._parse_question(bad))

    def test_rejects_non_json(self):
        self.assertIsNone(explainer._parse_question("I cannot do that."))
        self.assertIsNone(explainer._parse_question(None))

    def test_uses_bank_when_offline(self):
        q, source = explainer.practice_question(self.nodes, "lists", self.quiz, no_llm=True)
        self.assertEqual(source, "fallback")
        self.assertEqual(q["choices"], next(x["choices"] for x in self.quiz
                                            if x["concept"] == "lists"))

    def test_falls_back_when_model_returns_garbage(self):
        with Patched("here is a question but not json"):
            q, source = explainer.practice_question(self.nodes, "lists", self.quiz)
        self.assertEqual(source, "fallback")
        self.assertIsNotNone(q)

    def test_uses_model_question_when_valid(self):
        with Patched(self.good_json()):
            q, source = explainer.practice_question(self.nodes, "loops_for", self.quiz)
        self.assertEqual(source, "llm")
        self.assertEqual(q["prompt"], "What does range(3) produce?")

    def test_prewarmed_question_is_served_once_then_regenerated(self):
        explainer._practice_cache.clear()
        with Patched(self.good_json()) as p:
            explainer.prewarm_practice(self.nodes, "loops_for", self.quiz)
            self.assertEqual(len(p.calls), 1)
            q1, s1 = explainer.practice_question(self.nodes, "loops_for", self.quiz)
            self.assertEqual(len(p.calls), 1)      # served from the prewarm
            q2, s2 = explainer.practice_question(self.nodes, "loops_for", self.quiz)
            self.assertEqual(len(p.calls), 2)      # cache was one-shot
        self.assertEqual((s1, s2), ("llm", "llm"))
        self.assertEqual(q1["prompt"], q2["prompt"])
        explainer._practice_cache.clear()

    def test_no_bank_question_returns_none_offline(self):
        # 'inheritance' has no diagnostic question in the bank
        q, source = explainer.practice_question(self.nodes, "inheritance", self.quiz, no_llm=True)
        self.assertIsNone(q)
        self.assertEqual(source, "fallback")


class TestNeverShellsOutToOllama(unittest.TestCase):
    def test_no_subprocess_import_in_explainer(self):
        with open(explainer.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("ollama run", src.replace("`ollama run`", ""))
        self.assertIn("localhost:11434", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
