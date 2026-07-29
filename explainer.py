"""
Graph-augmented retrieval (RAG) explainer.

Retrieval is NOT embedding similarity — it is the concept's own prerequisite
chain, pulled straight out of the curriculum DAG. That is the whole thesis of
the "Adaptive Curriculum Maps" paper: the right context for explaining X is
what the learner already knows on the path to X.

Generation is local qwen3-fast via the Ollama HTTP API. If Ollama is not
running, every function degrades to a canned bilingual explanation and the
app keeps working with zero network calls.

IMPORTANT: never shell out to `ollama run` and capture stdout — always the
HTTP API at localhost:11434. This module never invokes ollama as a subprocess.
"""
import json
import os
import re
import urllib.request
import urllib.error

import graph_engine as ge

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_URL = OLLAMA_HOST.rstrip("/") + "/api/chat"
OLLAMA_TAGS_URL = OLLAMA_HOST.rstrip("/") + "/api/tags"
OLLAMA_MODEL = os.environ.get("TUTOR_MODEL", "qwen3-fast")
EXPLANATIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "explanations.json")

_explanations_cache = None

# qwen3 is a thinking model; even with think=false it can emit a reasoning
# preamble terminated by </think>. Keep only what comes after the last one.
_THINK_RE = re.compile(r"^.*</think>", re.DOTALL)


def _strip_thinking(text):
    return _THINK_RE.sub("", text).strip()


def load_explanations():
    global _explanations_cache
    if _explanations_cache is None:
        with open(EXPLANATIONS_PATH, "r", encoding="utf-8") as f:
            _explanations_cache = json.load(f)
    return _explanations_cache


def canned_explanation(concept_id, lang="en"):
    """Demo-safety-net fallback. Never touches the network."""
    explanations = load_explanations()
    entry = explanations.get(concept_id)
    if not entry:
        return "No explanation available for this concept yet." if lang == "en" \
            else "لا يوجد شرح متاح لهذا المفهوم بعد."
    return entry.get(lang, entry.get("en", ""))


def ollama_available(timeout=1.5):
    """Cheap liveness probe so the UI can show honest status."""
    try:
        req = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in body.get("models", [])]
        has_model = any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names)
        return {"reachable": True, "model": OLLAMA_MODEL, "model_present": has_model}
    except Exception:
        return {"reachable": False, "model": OLLAMA_MODEL, "model_present": False}


SYSTEM_PROMPT = ("You are a precise intro-CS tutor. Reply with the final answer only. "
                 "No preamble, no meta-commentary, no restating the instructions.")

# num_predict covers thinking tokens as well as the answer. qwen3 reasons a lot
# more before answering in Arabic, so Arabic needs a bigger budget or the cap
# lands mid-monologue and the answer comes back empty.
TOKEN_BUDGET = {"en": 1800, "ar": 2800}


def budget_for(lang):
    return TOKEN_BUDGET.get(lang, TOKEN_BUDGET["en"])


def _generate(prompt, timeout=180, num_predict=1800, temperature=0.2):
    """
    One-shot call to the local model. Returns cleaned text or None.

    Uses /api/chat with think=true on purpose. qwen3 is a reasoning model: with
    thinking OFF it still emits a reasoning monologue, just inside the visible
    answer, and a token cap can truncate it before the closing </think> so
    there is nothing to strip. With thinking ON, Ollama returns the monologue
    in a separate `thinking` field and `content` is the clean answer.
    """
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": True,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    content = (body.get("message") or {}).get("content", "") or ""
    text = _strip_thinking(content)
    # If a stray monologue still leaked through (older Ollama builds ignore
    # `think`), refuse it rather than showing a student the model's scratchpad.
    if _looks_like_scratchpad(text):
        return None
    return text or None


_SCRATCHPAD_MARKERS = (
    "we are explaining", "let's craft", "let us craft", "we want to explain",
    "the instruction says", "but note:", "however, note", "let's write:",
    "we must not", "okay, the user", "the user wants",
)


def _looks_like_scratchpad(text):
    low = text.lower()
    return any(m in low for m in _SCRATCHPAD_MARKERS)


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

def build_context(nodes, concept_id, states=None):
    """Retrieval step: the concept node + its full prerequisite chain, ordered
    root-first so the model sees foundations before the target. When quiz
    states are known, the chain is split into 'already solid' vs 'also shaky'
    so the explanation can lean on the former and re-anchor the latter."""
    chain_ids = ge.prereq_chain(nodes, concept_id)
    node = nodes[concept_id]
    states = states or {}

    solid = [c for c in chain_ids if states.get(c, "ok") == "ok"]
    weak = [c for c in chain_ids if states.get(c, "ok") != "ok"]

    def fmt(ids):
        return "\n".join(f"- {nodes[c]['name']} ({c})" for c in ids)

    return {
        "target": node["name"],
        "target_id": concept_id,
        "cluster": node.get("cluster", ""),
        "prereq_chain": chain_ids,
        "solid": solid,
        "weak": weak,
        "prereq_summary": fmt(chain_ids) if chain_ids
            else "(no prerequisites - this is a foundational concept)",
        "solid_summary": fmt(solid) if solid else "(none yet)",
        "weak_summary": fmt(weak) if weak else "(none)",
    }


# --------------------------------------------------------------------------
# Generation: explanation
# --------------------------------------------------------------------------

def build_prompt(context, lang="en"):
    """
    Deliberately terse. A long instruction-heavy prompt makes qwen3 ruminate,
    which burns the token budget on reasoning and starves the answer. Short
    prompt in, short answer out, about 10s on an M1.
    """
    solid = ", ".join(nid.replace("_", " ") for nid in context["solid"]) or "nothing yet"
    weak = ", ".join(nid.replace("_", " ") for nid in context["weak"])
    lines = [
        f"Student already knows: {solid}.",
        f"Teach: {context['target']}.",
    ]
    if weak:
        lines.append(f"Still shaky, do not lean on: {weak}.")
    if lang == "ar":
        lines.append("القواعد: ثلاث جمل قصيرة بالعربية المبسطة. اذكر مفهوماً يعرفه الطالب. "
                     "مثال بايثون قصير جداً. أبقِ الكود والكلمات المفتاحية بالإنجليزية. "
                     "نص عادي بدون قوائم أو عناوين.")
    else:
        lines.append("Rules: exactly 3 short sentences. Name one concept the student already "
                     "knows. One tiny Python example inline. Plain prose, no bullets, no "
                     "headings. English.")
    return "\n".join(lines)


_explain_cache = {}


def _cache_key(context, lang):
    return (context["target_id"], lang, tuple(context["weak"]))


def explain(nodes, concept_id, lang="en", no_llm=False, states=None, timeout=300,
            use_cache=True):
    """
    Returns (text, source) where source is "llm" or "fallback".
    Never raises because of Ollama: any error/timeout degrades to canned text.

    Successful generations are memoised per (concept, language, shaky-set) so
    flipping back and forth between English and Arabic is instant after the
    first pass. `tutor.py warm` uses this to pre-generate before a live demo.
    """
    if no_llm:
        return canned_explanation(concept_id, lang), "fallback"
    context = build_context(nodes, concept_id, states)
    key = _cache_key(context, lang)
    if use_cache and key in _explain_cache:
        return _explain_cache[key], "llm"
    text = _generate(build_prompt(context, lang), timeout=timeout,
                     num_predict=budget_for(lang))
    if text:
        _explain_cache[key] = text
        return text, "llm"
    return canned_explanation(concept_id, lang), "fallback"


# --------------------------------------------------------------------------
# Generation: adaptive practice question (mastery re-check)
# --------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_practice_prompt(context, lang="en"):
    lang_instruction = (
        "Write prompt and choices in simple Arabic, but keep all code, identifiers and keywords in English."
        if lang == "ar" else "Write everything in simple English."
    )
    return f"""One multiple-choice question testing real understanding of {context['target']} in intro Python.
Wrong choices must be plausible misconceptions. {lang_instruction}
Reply with ONLY this JSON, no prose, no fence:
{{"prompt": "...", "choices": ["...", "...", "...", "..."], "answer": 0, "why": "one sentence"}}
"answer" is the 0-based index of the correct choice. Exactly 4 choices."""


def _parse_question(raw):
    if not raw:
        return None
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj.get("prompt"), str):
        return None
    choices = obj.get("choices")
    if not isinstance(choices, list) or len(choices) != 4:
        return None
    if not all(isinstance(c, str) and c.strip() for c in choices):
        return None
    ans = obj.get("answer")
    if not isinstance(ans, int) or not 0 <= ans < 4:
        return None
    return {
        "prompt": obj["prompt"].strip(),
        "choices": [c.strip() for c in choices],
        "answer": ans,
        "why": str(obj.get("why", "")).strip(),
    }


def fallback_question(concept_id, quiz_questions, exclude_ids=()):
    """Offline path: reuse a bank question for this concept."""
    for q in quiz_questions:
        if q["concept"] == concept_id and q["id"] not in exclude_ids:
            return {
                "prompt": q["prompt"],
                "choices": list(q["choices"]),
                "answer": q["answer"],
                "why": "",
                "source_id": q["id"],
            }
    return None


_practice_cache = {}


def prewarm_practice(nodes, concept_id, quiz_questions, lang="en", states=None):
    """Generate a practice question now and stash it for the next request."""
    q, source = practice_question(nodes, concept_id, quiz_questions, lang=lang,
                                  states=states, use_cache=False)
    if q and source == "llm":
        _practice_cache[_cache_key(build_context(nodes, concept_id, states), lang)] = q
    return source


def practice_question(nodes, concept_id, quiz_questions, lang="en", no_llm=False,
                      states=None, timeout=300, use_cache=True):
    """Returns (question_dict_or_None, source)."""
    if not no_llm:
        context = build_context(nodes, concept_id, states)
        if use_cache:
            # Pre-generated questions are one-shot: pop it, so clicking again
            # asks the model for a genuinely new question.
            cached = _practice_cache.pop(_cache_key(context, lang), None)
            if cached:
                return cached, "llm"
        raw = _generate(build_practice_prompt(context, lang), timeout=timeout,
                        num_predict=budget_for(lang))
        q = _parse_question(raw)
        if q:
            return q, "llm"
    return fallback_question(concept_id, quiz_questions), "fallback"
