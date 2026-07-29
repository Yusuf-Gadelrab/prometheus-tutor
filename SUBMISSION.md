# Adaptive CS Tutor - Devpost Submission

Copy-paste each section into the matching Devpost field.

---

## Project name

Adaptive CS Tutor

---

## Tagline

A wrong answer finds the root cause, not the symptom, then a local model explains it in your language.

**Alternates:**
- Your prerequisite graph knows why you're stuck. Your LLM never leaves your laptop.
- Diagnose the concept underneath the concept, then teach it, offline, in English or Arabic.

---

## Elevator pitch / short description

Adaptive CS Tutor takes a 12-question intro-CS diagnostic, then walks a hand-authored 31-concept prerequisite graph to find which upstream idea is actually broken instead of just flagging the question you missed. It generates a topologically-sorted remediation path so root causes come before symptoms, retrieves each concept's real prerequisite chain (not embeddings) as context for a local model, and explains it in English or Arabic while keeping code syntax in English. Everything runs on stdlib Python and a local Ollama model, zero cloud, zero API cost, zero student data leaving the machine.

---

## Inspiration

I'm a CS tutor at SJSU (Coding Warriors + the CS Dept) and I run CS programs at Yerba Buena High School, which is ELL-heavy. The same pattern shows up constantly: a student misses a question about recursion, and the actual gap is functions, or scope, or conditionals three concepts back. Reteaching recursion doesn't fix that. You have to find the concept underneath the concept.

That observation is also the subject of two papers I co-authored at Dr. Ethel Tshukudu's CS Education Research Lab at SJSU for SIGCSE TS 2026: "Exploring Bilingual Coding for Inclusive CS Learning" and "Adaptive Curriculum Maps: Graph-Augmented Retrieval-Oriented LLMs for Education" (poster), an IRB-approved mixed-methods study with 60 participants. This hackathon was the excuse to actually build the system the papers describe instead of just writing about it.

---

## What it does

1. **Diagnostic.** The student answers a 12-question quiz. Each question maps to one of 31 intro-CS concepts (variables through inheritance and file I/O) laid out in a hand-authored prerequisite DAG, depth 7.
2. **Propagation.** Any concept behind a wrong answer is marked SHAKY. A breadth-first search runs forward over the dependents graph from every shaky node, and everything reachable downstream is marked AT RISK, because mastery of a concept built on a shaky one is questionable even if the student never got a direct question on it.
3. **Recommended Path.** Every broken concept (shaky or at risk) gets sorted topologically, shallowest prerequisite depth first. If both functions and recursion are shaky, functions is step 1, always, because reteaching recursion before functions is wasted instruction. Each step shows why it's there and how many downstream concepts it unblocks.
4. **Graph-augmented explanation.** Retrieval isn't embedding similarity, it's the concept's actual prerequisite chain pulled out of the DAG, split into "already solid" and "still shaky." That context gets injected into the prompt so the local model attaches the new idea to something the student already owns, and re-anchors anything shaky it has to touch along the way. The UI shows the retrieved chain so the mechanism isn't a black box.
5. **Bilingual, not just translated.** One toggle flips the entire UI English to Arabic with RTL layout, and the model re-explains in Arabic while keeping code identifiers, keywords, and syntax in English. That's the actual finding from the bilingual-coding paper, not a generic translate button bolted on top.
6. **Check my understanding.** The local model generates a fresh multiple-choice question for the concept the student just read about (JSON, strictly validated). Answer it correctly and the server drops the concept from the shaky set and re-propagates the whole graph in one shot, clearing that concept and its downstream cluster together.
7. **Warm-up cache for live demos.** `python3 server.py --warm functions recursion lists --warm-langs en ar` pre-generates explanations and practice questions on a background thread before anyone touches the UI. Explanations are memoised per concept, language, and shaky-set. Practice questions are one-shot: the prewarmed question is popped the first time it's requested, so clicking "check my understanding" again always asks the model for a genuinely new question instead of repeating the warmed one.
8. **Diagnosis survives a refresh.** `GET /api/session` restores the current shaky/mastered state and remediation path on page load, so reloading the browser mid-demo doesn't throw away the quiz result.
9. **Never network-dependent.** `--no-llm` forces canned bilingual explanations, and the app falls back to them automatically if Ollama isn't reachable. It runs the same with Wi-Fi off.

---

## How I built it

I'm solo on this, so it's all "I" below even though Devpost calls the field "How we built it."

The whole thing is stdlib Python: `http.server` for the web app, no pip install, no npm, no build step, no database. `graph_engine.py` is the part that has to be provably correct on its own, it loads `graph.json` (31 nodes, prereq edges), builds a dependents map, computes topo levels for a cycle check and for layout, runs the BFS state propagation, computes the prerequisite chain for retrieval, and produces the sorted remediation path. It has its own unit tests and zero knowledge of Ollama. The project has 89 tests total, run with `python3 -m unittest discover -v`.

`explainer.py` is the RAG layer. `build_context()` pulls the prerequisite chain for a concept and splits it into solid vs. weak based on current quiz state, then `build_prompt()` turns that into a deliberately short instruction for the model, because a long one makes qwen3 ruminate instead of answer. Generation goes straight to the Ollama HTTP API at `localhost:11434` with `qwen3-fast` (Qwen3-30B-A3B MoE) using `/api/chat` with `think: true`, never through `ollama run` as a subprocess, so stdout capture quirks never enter the picture. Successful generations and practice questions are memoised in-process per concept, language, and shaky-set, which is what the `--warm` warm-up path fills before a demo.

`server.py` wires it together behind a small set of JSON endpoints (`/api/graph`, `/api/quiz`, `/api/submit`, `/api/explain`, `/api/practice`, `/api/master`, `/api/reset`, `/api/session`) plus a single inline HTML page: a full-width Curriculum Map panel with a stat row (mastered / shaky / at risk / diagnostic score) above a three-column layout (Recommended Path, Explanation, Diagnostic), DHAHAB black-and-gold styling, and full English/Arabic string tables. `tutor.py` is a parallel CLI (`doctor`, `demo`, `quiz`, `explain`, `warm`) that exercises the same engine so judges can see the whole pipeline in a terminal without a browser, including the offline path.

---

## Challenges I ran into

- **`think: false` does not stop qwen3 from reasoning, it just hides the seam.** My first assumption was that turning thinking off would give a clean answer. Instead the model reasons anyway and dumps the monologue straight into the visible answer, and since `num_predict` caps the whole response, the cap lands mid-monologue, before the closing `</think>` ever shows up. There is nothing to strip because the tag never closes, so the student would see raw scratch-thinking in the explanation box. The actual fix was switching to `/api/chat` with `think: true`, which makes Ollama return the reasoning in a separate `thinking` field and leaves `content` clean on its own. I also kept a scratchpad-marker guard (phrases like "let's craft" or "the user wants") that refuses any answer that still smells like reasoning and falls back to the canned explanation instead of ever showing it.
- **A long, instruction-heavy prompt makes the model ruminate itself out of an answer.** The first prompt template spelled out every rule in detail, and qwen3 would spend the entire token budget reasoning about the instructions and return an empty answer, a dead end for English explanations specifically. Cutting the prompt down to four short lines took English generation from empty responses to about 10 seconds.
- **Arabic reasons dramatically longer than English.** Same model, same concept, roughly 6,600 characters of thinking for an Arabic answer versus about 1,700 for English. A single fixed token budget either truncated Arabic mid-thought or wasted budget on English, so the budget is language-aware: 1,800 tokens for English, 2,800 for Arabic. Arabic still takes 45 to 60 seconds cold on an M1, which is what motivated the warm-up cache.
- **Getting a thinking model to return parseable JSON, reliably, for generated practice questions.** LLM output is not a contract. `_parse_question()` regex-extracts the first `{...}` block, then validates shape by hand: prompt is a string, choices is a list of exactly 4 non-empty strings, answer is an int in range. Anything that fails validation falls through to the question bank instead of showing the student garbage or crashing the request.
- **Deciding retrieval should be the DAG chain, not embeddings.** It would have been faster to throw a vector store at this, but embedding similarity doesn't know that functions is a prerequisite of recursion, it only knows the text is topically related. The graph is the actual pedagogical structure, so it had to be the retrieval mechanism, which meant the 31-node DAG had to be hand-authored carefully enough to be both acyclic and pedagogically honest (recursion legitimately depends on both functions and conditionals, for example, not just one).
- **RTL layout without a framework.** No CSS framework, no JS framework, so the Arabic toggle is a plain `dir="rtl"` flip on `<body>` plus a handful of `[dir="rtl"]` overrides for the specific elements (choice buttons, path numbering) that don't mirror correctly by default.
- **Making the whole thing degrade to zero network on purpose.** Every LLM call point has a fallback path, canned explanation, question bank, and an honest status pill in the UI that tells the student whether they're getting the live model or offline text. A demo cannot fail because Ollama hiccups mid-presentation.

---

## Accomplishments that I'm proud of

- The BFS propagation and topological remediation ordering are small functions, but they're the actual mechanic from the SIGCSE paper turned into working code, not a mockup of it. On the 31-concept, depth-7 DAG, a simulated student who misses functions and lists shows 2 shaky and 17 at risk out of 31 concepts, and one root-cause lesson on functions alone clears 9 concepts at once, exactly the reteach-the-symptom problem the tool exists to fix.
- Retrieval that is structurally grounded in a real curriculum graph instead of a vector database, and the UI shows the retrieved chain so the "why did it explain it this way" question has a visible answer.
- The offline fallback isn't a stub, `--no-llm` produces a fully functional demo with canned bilingual text, a working question bank, and the entire graph mechanic intact. Nothing about the core product depends on the network being up.
- The warm-up cache turns a 12-to-30-second cold generation into a 15-millisecond served response, measured, without faking anything: the served text is the same generation, just pre-computed on a background thread before the demo starts.
- Shipping a bilingual feature that respects the actual finding from my own research (keep code syntax in English, translate the pedagogy) instead of a naive full-page translation.

---

## What I learned

Building the system described in a research paper is a different kind of work than writing the paper. The DAG had to survive an actual cycle check and actual student paths through it, not just look right in a diagram. Getting an LLM to be a reliable component (not just a chat window) meant treating its output as untrusted input everywhere: strip the thinking preamble, validate the JSON shape, always have a non-LLM path ready. And RAG doesn't have to mean embeddings, if you already have structured domain knowledge, the graph itself is often a better retriever than a similarity search over it.

---

## What's next for Adaptive CS Tutor

- Replace binary shaky/ok scoring with item-response-theory weighting so partial understanding and question difficulty both factor into the diagnosis, not just right/wrong.
- Per-student persistence (today `/api/session` survives a browser refresh, but it's still a single in-memory demo session that resets on server restart, not per-student accounts).
- Spanish and Vietnamese in addition to Arabic, to actually match the language population at Yerba Buena High School.
- A teacher dashboard showing the class-wide graph, so an instructor can see at a glance which single concept is breaking the most students across the whole room, not just one student's path.
- Running it as a real cohort through the SJSU CSEd lab under IRB, the same rigor as the original 60-participant study.
- Exporting anonymized graph data back into the research, closing the loop from paper to tool to data.

---

## Built with

python, ollama, qwen3, http.server, svg, rag, knowledge-graph, javascript, html, css

---

## Try it out / run instructions

```bash
git clone <this repo> && cd prometheus-tutor

# web app (tries Ollama automatically, falls back if unreachable)
python3 server.py
# open http://localhost:8123

# force fully offline demo mode
python3 server.py --no-llm

# pre-warm explanations + practice questions before a live demo
python3 server.py --warm functions recursion lists --warm-langs en ar

# CLI, no browser needed
python3 tutor.py doctor              # checks graph + local model
python3 tutor.py demo                # scripted end-to-end run
python3 tutor.py demo --no-llm --lang ar
python3 tutor.py quiz                # take the diagnostic yourself
python3 tutor.py explain recursion --lang ar
python3 tutor.py warm functions recursion lists --langs en ar

# tests (89 passing)
python3 -m unittest discover -v
```

Zero dependencies to install. Optional: Ollama running locally with `qwen3-fast` pulled, for live generation instead of canned fallback text.

---

## Research citations

1. Exploring Bilingual Coding for Inclusive CS Learning. SIGCSE TS 2026. DOI: [10.1145/3770761.3777339](https://doi.org/10.1145/3770761.3777339)
2. Adaptive Curriculum Maps: Graph-Augmented Retrieval-Oriented LLMs for Education (poster). SIGCSE TS 2026.

Both from Dr. Ethel Tshukudu's CS Education Research Lab at San Jose State University. IRB-approved mixed-methods study, 60 participants.
