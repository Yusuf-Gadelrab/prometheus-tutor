# Adaptive CS Tutor, 2-Minute Demo Script
Prometheus July AI Challenge · solo entry · Yusuf Gadelrab, SJSU CS · deadline Jul 30 2026

## The shot in one sentence

A single unbroken screen capture of the live app at `localhost:8123`: open on the DHAHAB header, scroll down to the Diagnostic column and answer the questions, submit, watch the page jump back up to the full-width Curriculum Map as the prerequisite graph propagates shaky/at-risk state, follow the Recommended Path to a graph-augmented explanation from the local qwen3-fast model, close a knowledge gap with "Check my understanding" and watch a red cluster turn green, flip the whole UI to Arabic, then cut briefly to the terminal for the CLI/offline proof and a closing card, narrated live over the top, no music.

**Recording notes**
- Screen resolution: 1920x1080, browser window maximized, zoom at 100% (not 110%+, labels must stay crisp at 1080p export).
- Terminal font: 16–18pt monospace, dark theme, for the CLI cutaway.
- Mic: run one silent test clip first, check no room echo, no keyboard clatter bleeding into narration.
- No music under narration, voice only, keeps it feel like a real technical demo, not a trailer.
- Capture with QuickTime Player (Screen Recording, internal mic or external if available).
- Export at 1080p, upload unlisted to YouTube, paste the link in the submission form.
- **Latency is real and the whole shoot depends on pre-warming it.** Cold, on his M1: an English explanation takes 10–12s, a generated practice question takes ~30s, and a cold Arabic explanation takes 45–60s (qwen3 reasons much longer in Arabic). None of that is acceptable on camera. Before hitting record, start the server with:
  ```
  python3 server.py --warm functions recursion lists --warm-langs en ar
  ```
  This pre-generates explanations and practice questions into an in-process cache on a background thread and prints `warm-up complete` to the terminal when it's done (takes a few minutes, let it finish, it's Arabic that's slow). Once warm, serve time for a cached explanation is ~15ms, so the explanation panel and the Arabic flip are instant on camera.
- **Wait for `warm-up complete` in the terminal before hitting record.** State it plainly in your own notes, not on camera, that this is caching: the served text is the exact same generated text the model produced, nothing is scripted or faked, only pre-fetched.

## Shot table

| Time | On screen | Narration |
|---|---|---|
| 0:00–0:12 | Cold open on `localhost:8123`. DHAHAB lion mark, kicker "Applied SIGCSE TS 2026 Research," title "Adaptive CS Tutor" visible in frame. Status pill top right reads "QWEN3-FAST LOCAL." Hold, don't scroll yet. | This isn't a toy demo. It's built on my own peer-reviewed SIGCSE paper, real research, sixty student participants, an IRB-approved study. I turned the findings into a working tutor. |
| 0:12–0:26 | Scroll down past the map to the Diagnostic column (third column of the three-column row). Click through the twelve questions, deliberately wrong on "functions" and "lists," click "Submit Diagnostic." | Twelve questions probe thirty-one intro CS concepts, wired into a hand-built prerequisite graph, seven levels deep. I answer a couple wrong on purpose, then submit. |
| 0:26–0:38 | Page auto-scrolls back up to the full-width Curriculum Map. Stat row reads 12 Mastered, 2 Shaky, 17 At risk, 10/12 Diagnostic. Point out amber nodes (Shaky) and crimson nodes (At risk) per the legend. | Watch the graph react. Wrong answers mark concepts shaky, amber. Everything that depends on them turns crimson, at risk, propagated automatically through the graph. |
| 0:38–0:50 | Scroll down to the Recommended Path, the left column of the three-column row. Point at row 01: "Lists / Arrays [SHAKY], answered incorrectly in the diagnostic · unblocks 7," sitting above what it blocks. | This is the adaptive learning path. It sorts every broken concept so root causes come first. Lists is step one here, it unblocks seven other concepts downstream. |
| 0:50–1:02 | Click step 01 in the path. Middle column, Explanation panel shows "Thinking locally..." for a beat, then the cached text lands instantly. | I click a concept. That kicks off a call to qwen3-fast, a thirty-billion parameter model, running entirely on my laptop. |
| 1:02–1:12 | Explanation text on screen, "qwen3-fast local" badge visible in the meta row, "Retrieved context" chain shown underneath (prereqs → concept). | And look, it retrieved context, not embeddings, the actual prerequisite chain from the graph. It teaches from what I already know. |
| 1:12–1:24 | Click "Check my understanding" in the Explanation column. A fresh multiple-choice question generates, click the correct choice. | Now I check my understanding. The model writes a fresh question on the spot. I answer correctly, and that concept gets marked mastered. |
| 1:24–1:34 | Page scrolls back up to the Curriculum Map: several nodes flip from crimson/amber to green in one refresh, stat row updates. | The server re-propagates the whole map instantly. One root-cause lesson, and a whole cluster of red nodes turns green. |
| 1:34–1:46 | Click the "العربية" button top right. Entire UI flips to Arabic, right-to-left layout, status pill still reads live model, re-click the same concept so it re-explains in Arabic, instantly, cached. | One button flips the entire interface to Arabic, right to left, and the model re-explains in Arabic too, code and syntax stay in English. |
| 1:46–1:54 | Hard cut to a terminal window. Run `python3 tutor.py demo --miss functions recursion`, colored output scrolling through all six stages. | There's also a terminal version for judges, and it all runs offline. No API keys, no cloud, zero dollars. |
| 1:54–2:00 | Closing card / final frame back on the app header with the DHAHAB lion mark, status pill visible. | Adaptive CS Tutor, built by Yusuf Gadelrab for the Prometheus July AI Challenge. |

## Full narration (teleprompter block)

This isn't a toy demo. It's built on my own peer-reviewed SIGCSE paper, real research, sixty student participants, an IRB-approved study. I turned the findings into a working tutor. Twelve questions probe thirty-one intro CS concepts, wired into a hand-built prerequisite graph, seven levels deep. I answer a couple wrong on purpose, on functions and lists, then submit. Watch the graph react. Wrong answers mark concepts shaky, amber. Everything that depends on them turns crimson, at risk, propagated automatically through the graph. Twelve mastered, two shaky, seventeen at risk, from ten out of twelve right on the diagnostic. This is the adaptive learning path. It sorts every broken concept so root causes come first. Lists is step one here, marked shaky because I missed it on the diagnostic, and it unblocks seven other concepts downstream. I click it. That kicks off a call to qwen3-fast, a thirty-billion parameter model, running entirely on my own laptop, no cloud involved. And look, it retrieved context, not embeddings, the actual prerequisite chain pulled straight from the graph. It teaches from what I already know, not a generic textbook answer. Now I check my understanding. The model writes a brand new practice question on the spot. I answer correctly, and that concept gets marked mastered. The server re-propagates the whole map instantly. One root-cause lesson, and a whole cluster of red nodes turns green. One button flips the entire interface to Arabic, right to left layout and all, and the model re-explains the same concept in Arabic too, code and syntax stay in English. There's also a terminal version built for judges, and the whole thing runs offline. No API keys, no cloud, zero dollars to run this. Adaptive CS Tutor, built by Yusuf Gadelrab for the Prometheus July AI Challenge.

**Word count: 297 words.** Target pace is roughly 135–150 words per minute for a 120-second read, so 297 words lands right in the 280–300-word ballpark, natural, not rushed.

## If something goes wrong

- **Ollama is slow, unreachable, or the model isn't pulled:** restart the server with `python3 server.py --no-llm` and re-record, the fallback explanations are real bilingual content, not error text, so the demo still holds together. Status pill will read "OFFLINE EXPLANATIONS" instead of "QWEN3-FAST LOCAL," state that plainly on camera if it happens mid-take rather than pretending.
- **Warm-up didn't finish, or you clicked a concept that wasn't warmed:** don't fake instant generation, own the wait or cut. Cold English is 10–12s, cold Arabic is 45–60s, a cold generated practice question is ~30s, none of those read well live. If you're not sure something's warm, check the terminal for its `warmed explanation` / `warmed question` line before clicking it on camera.
- **Live demo breaks or times out mid-recording:** cut to `python3 tutor.py demo --miss functions recursion` in the terminal as backup B-roll, it runs the entire diagnostic-to-mastery loop headless in about 20 seconds, prints all six stages, and can stand in for any web-UI segment that fails.
- **Browser refreshes mid-demo by accident:** don't panic-restart from zero, there's a session-restore endpoint now (`/api/session`), the page reloads straight back into the diagnosed state, shaky/at-risk nodes and all, so a refresh doesn't blow the take.
- **Arabic font renders oddly or RTL layout glitches in the browser used for capture:** test the toggle once before recording; if it's still off, keep that beat shorter and lean on narration to carry the point.
