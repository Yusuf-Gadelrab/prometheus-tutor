"""
End-to-end tests against a real running server on a random port.

Forces offline mode (server.FORCE_NO_LLM = True) so the suite is fast,
deterministic, and passes with Ollama stopped.
Run: python3 test_server.py
"""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import server as srv


def request(method, path, body=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, resp.headers.get("Content-Type", ""), raw
    except urllib.error.HTTPError as e:
        out = (e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8"))
        e.close()
        return out


def get_json(path):
    status, _, raw = request("GET", path)
    return status, json.loads(raw)


def post_json(path, body):
    status, _, raw = request("POST", path, body)
    return status, json.loads(raw)


PORT = None
HTTPD = None


def setUpModule():
    global PORT, HTTPD
    srv.FORCE_NO_LLM = True
    HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    PORT = HTTPD.server_address[1]
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def tearDownModule():
    HTTPD.shutdown()
    HTTPD.server_close()


class TestStaticPage(unittest.TestCase):
    def test_index_renders_with_lion_and_brand_colors(self):
        status, ctype, html = request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Adaptive", html)
        self.assertIn("#d4af37", html)          # DHAHAB gold
        self.assertIn("#0a0a0a", html)          # ink
        self.assertNotIn("{{LION}}", html)      # placeholder was substituted
        self.assertIn("<svg", html)             # lion mark inlined
        self.assertIn("العربية", html)          # bilingual toggle present

    def test_index_supports_deep_link_boot_params(self):
        status, ctype, html = request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("URLSearchParams(location.search)", html)
        self.assertIn("boot.get('lang')==='ar'", html)
        self.assertIn("boot.get('concept')", html)
        # a deep-linked concept must be validated against the graph before use
        self.assertIn("graphData.nodes.some(n=>n.id===deep)", html)

    def test_unknown_route_404(self):
        status, body = get_json("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


class TestReadEndpoints(unittest.TestCase):
    def test_graph_payload_shape(self):
        status, g = get_json("/api/graph")
        self.assertEqual(status, 200)
        self.assertEqual(len(g["nodes"]), len(srv.NODES))
        self.assertTrue(all({"id", "name", "x", "y", "prereqs"} <= set(n) for n in g["nodes"]))
        ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            self.assertIn(e["from"], ids)
            self.assertIn(e["to"], ids)

    def test_quiz_never_leaks_answer_key(self):
        status, q = get_json("/api/quiz")
        self.assertEqual(status, 200)
        self.assertEqual(len(q), len(srv.QUIZ))
        for item in q:
            self.assertNotIn("answer", item)

    def test_status_reports_offline_when_forced(self):
        status, s = get_json("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(s["no_llm_forced"])
        self.assertFalse(s["ollama"]["reachable"])
        self.assertEqual(s["concepts"], len(srv.NODES))


class TestDiagnosticFlow(unittest.TestCase):
    def setUp(self):
        post_json("/api/reset", {})

    def all_correct(self):
        return {q["id"]: q["answer"] for q in srv.QUIZ}

    def test_perfect_score_leaves_map_clean(self):
        status, d = post_json("/api/submit", {"answers": self.all_correct()})
        self.assertEqual(status, 200)
        self.assertEqual(d["shaky"], [])
        self.assertEqual(d["path"], [])
        self.assertEqual(d["report"]["mastery_pct"], 100.0)

    def test_one_wrong_answer_propagates_and_builds_path(self):
        answers = self.all_correct()
        target = next(q for q in srv.QUIZ if q["concept"] == "lists")
        answers[target["id"]] = (target["answer"] + 1) % len(target["choices"])
        status, d = post_json("/api/submit", {"answers": answers})
        self.assertEqual(status, 200)
        self.assertEqual(d["shaky"], ["lists"])
        self.assertEqual(d["states"]["lists"], "shaky")
        self.assertEqual(d["states"]["dicts"], "at_risk")
        self.assertEqual(d["states"]["variables"], "ok")
        self.assertEqual(d["path"][0]["concept"], "lists")
        self.assertGreater(len(d["path"]), 1)

    def test_mastering_a_concept_clears_its_cluster(self):
        answers = self.all_correct()
        target = next(q for q in srv.QUIZ if q["concept"] == "lists")
        answers[target["id"]] = (target["answer"] + 1) % len(target["choices"])
        _, before = post_json("/api/submit", {"answers": answers})
        _, after = post_json("/api/master", {"concept": "lists"})
        self.assertEqual(after["shaky"], [])
        self.assertEqual(after["path"], [])
        self.assertGreater(after["report"]["ok"], before["report"]["ok"])
        self.assertIn("lists", after["mastered"])

    def test_session_endpoint_restores_diagnosis_after_reload(self):
        answers = self.all_correct()
        target = next(q for q in srv.QUIZ if q["concept"] == "lists")
        answers[target["id"]] = (target["answer"] + 1) % len(target["choices"])
        post_json("/api/submit", {"answers": answers})
        status, s = get_json("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(s["shaky"], ["lists"])
        self.assertEqual(s["path"][0]["concept"], "lists")

    def test_session_is_clean_before_any_diagnostic(self):
        _, s = get_json("/api/session")
        self.assertEqual(s["shaky"], [])
        self.assertEqual(s["path"], [])

    def test_reset_restores_clean_state(self):
        post_json("/api/submit", {"answers": {}})
        _, d = post_json("/api/reset", {})
        self.assertEqual(d["shaky"], [])
        self.assertTrue(all(s == "ok" for s in d["states"].values()))

    def test_master_unknown_concept_404(self):
        status, _ = post_json("/api/master", {"concept": "not_real"})
        self.assertEqual(status, 404)

    def test_bad_json_body_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/submit", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()


class TestExplainEndpoint(unittest.TestCase):
    def test_explain_returns_canned_text_and_retrieval_chain(self):
        status, d = get_json("/api/explain?concept=recursion&lang=en")
        self.assertEqual(status, 200)
        self.assertEqual(d["source"], "fallback")
        self.assertTrue(d["text"])
        self.assertIn("functions", d["retrieved"])
        self.assertEqual(d["name"], "Recursion")

    def test_explain_arabic(self):
        _, d = get_json("/api/explain?concept=variables&lang=ar")
        self.assertTrue(any("؀" <= ch <= "ۿ" for ch in d["text"]))

    def test_explain_unknown_concept_404(self):
        status, _ = get_json("/api/explain?concept=nope")
        self.assertEqual(status, 404)

    def test_practice_offline_uses_bank(self):
        status, d = get_json("/api/practice?concept=lists&lang=en")
        self.assertEqual(status, 200)
        self.assertEqual(d["source"], "fallback")
        self.assertEqual(len(d["question"]["choices"]), 4)

    def test_practice_without_bank_question_503(self):
        status, _ = get_json("/api/practice?concept=inheritance&lang=en")
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)
