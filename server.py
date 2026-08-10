#!/usr/bin/env python3
"""
Adaptive CS Tutor - single-page stdlib HTTP server.

Zero external deps (stdlib only). Ollama is optional: pass --no-llm to force
canned explanations (also what happens automatically if Ollama is unreachable
at localhost:11434).

Run:
    python3 server.py            # tries Ollama, falls back automatically
    python3 server.py --no-llm   # force offline mode (demo-safe)
    python3 server.py --port 8123
"""
import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import graph_engine as ge
import explainer

HERE = os.path.dirname(os.path.abspath(__file__))

NODES = ge.load_graph()
DEPENDENTS = ge.build_dependents(NODES)
LEVELS = ge.topo_levels(NODES)
QUIZ = ge.load_quiz()

FORCE_NO_LLM = False

# Single-user demo session. Keeps the diagnostic result so remediation
# ("I get it now") can re-propagate the graph without a re-quiz.
SESSION = {"shaky": set(), "results": [], "mastered": set()}
SESSION_LOCK = threading.Lock()

with open(os.path.join(HERE, "assets", "lion-mark.svg"), "r", encoding="utf-8") as _f:
    LION_SVG = _f.read()


def layout_nodes():
    """Deterministic layout: x by prerequisite depth, y by order within depth."""
    by_level = {}
    for nid, lvl in LEVELS.items():
        by_level.setdefault(lvl, []).append(nid)
    for lvl in by_level:
        by_level[lvl].sort(key=lambda nid: (NODES[nid]["cluster"], nid))

    positions = {}
    x_gap, y_gap, x0, y0 = 190, 74, 100, 44
    tallest = max(len(ids) for ids in by_level.values())
    for lvl, ids in by_level.items():
        pad = (tallest - len(ids)) / 2.0
        for i, nid in enumerate(ids):
            positions[nid] = {"x": x0 + lvl * x_gap,
                              "y": y0 + (pad + i) * y_gap}
    return positions


POSITIONS = layout_nodes()


def graph_payload():
    edges = [{"from": p, "to": nid}
             for nid, node in NODES.items() for p in node["prereqs"]]
    nodes_out = [{
        "id": nid, "name": node["name"], "cluster": node["cluster"],
        "x": POSITIONS[nid]["x"], "y": POSITIONS[nid]["y"],
        "prereqs": node["prereqs"],
    } for nid, node in NODES.items()]
    width = max(p["x"] for p in POSITIONS.values()) + 190
    height = max(p["y"] for p in POSITIONS.values()) + 50
    return {"nodes": nodes_out, "edges": edges, "width": width, "height": height}


def quiz_payload():
    # never leak the answer index to the client before scoring
    return [{"id": q["id"], "concept": q["concept"], "prompt": q["prompt"],
             "choices": q["choices"]} for q in QUIZ]


def diagnosis_payload(shaky, results):
    states = ge.compute_states(NODES, DEPENDENTS, shaky)
    return {
        "shaky": sorted(shaky),
        "results": results,
        "states": states,
        "path": ge.learning_path(NODES, states, LEVELS),
        "report": ge.mastery_report(NODES, states, results or None),
        "ready_next": ge.ready_next(NODES, states)[:6],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep the demo terminal clean

    def _send(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path == "/":
            self._send(INDEX_HTML.replace("{{LION}}", LION_SVG).encode("utf-8"),
                       "text/html; charset=utf-8")
        elif path == "/api/graph":
            self._json(graph_payload())
        elif path == "/api/quiz":
            self._json(quiz_payload())
        elif path == "/api/session":
            with SESSION_LOCK:
                payload = diagnosis_payload(SESSION["shaky"], SESSION["results"])
                payload["mastered"] = sorted(SESSION["mastered"])
            self._json(payload)
        elif path == "/api/status":
            probe = {"reachable": False, "model": explainer.OLLAMA_MODEL, "model_present": False} \
                if FORCE_NO_LLM else explainer.ollama_available()
            self._json({"ok": True, "concepts": len(NODES), "questions": len(QUIZ),
                        "no_llm_forced": FORCE_NO_LLM, "ollama": probe})
        elif path == "/api/explain":
            concept = (qs.get("concept") or [""])[0]
            lang = (qs.get("lang") or ["en"])[0]
            no_llm = FORCE_NO_LLM or (qs.get("no_llm") or ["0"])[0] == "1"
            if concept not in NODES:
                self._json({"error": "unknown concept"}, 404)
                return
            with SESSION_LOCK:
                states = ge.compute_states(NODES, DEPENDENTS, SESSION["shaky"])
            text, source = explainer.explain(NODES, concept, lang=lang,
                                             no_llm=no_llm, states=states)
            self._json({"concept": concept, "name": NODES[concept]["name"], "lang": lang,
                        "text": text, "source": source,
                        "retrieved": ge.prereq_chain(NODES, concept)})
        elif path == "/api/practice":
            concept = (qs.get("concept") or [""])[0]
            lang = (qs.get("lang") or ["en"])[0]
            no_llm = FORCE_NO_LLM or (qs.get("no_llm") or ["0"])[0] == "1"
            if concept not in NODES:
                self._json({"error": "unknown concept"}, 404)
                return
            with SESSION_LOCK:
                states = ge.compute_states(NODES, DEPENDENTS, SESSION["shaky"])
            q, source = explainer.practice_question(NODES, concept, QUIZ, lang=lang,
                                                    no_llm=no_llm, states=states)
            if not q:
                self._json({"error": "no practice question available offline",
                            "concept": concept, "source": source}, 503)
                return
            self._json({"concept": concept, "question": q, "source": source})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()
        if body is None:
            self._json({"error": "bad json"}, 400)
            return

        if path == "/api/submit":
            answers = body.get("answers", {})
            shaky, results = ge.score_quiz(QUIZ, answers)
            with SESSION_LOCK:
                SESSION["shaky"] = set(shaky)
                SESSION["results"] = results
                SESSION["mastered"] = set()
                payload = diagnosis_payload(SESSION["shaky"], results)
            self._json(payload)

        elif path == "/api/master":
            # student proved a concept after remediation: drop it from shaky
            # and re-propagate the whole map.
            concept = body.get("concept")
            if concept not in NODES:
                self._json({"error": "unknown concept"}, 404)
                return
            with SESSION_LOCK:
                SESSION["shaky"].discard(concept)
                SESSION["mastered"].add(concept)
                payload = diagnosis_payload(SESSION["shaky"], SESSION["results"])
                payload["mastered"] = sorted(SESSION["mastered"])
            self._json(payload)

        elif path == "/api/reset":
            with SESSION_LOCK:
                SESSION["shaky"] = set()
                SESSION["results"] = []
                SESSION["mastered"] = set()
            self._json(diagnosis_payload(set(), []))
        else:
            self._json({"error": "not found"}, 404)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Adaptive CS Tutor - DHAHAB</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --ink:#0a0a0a; --panel:#101010; --panel2:#141312; --line:#241f16;
    --gold:#d4af37; --gold-dim:#8c7328; --bone:#f5f1e8; --mute:#8d8579;
    --ok:#3f6b4f; --shaky:#d98324; --risk:#9e2f2f;
    color-scheme: dark;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--ink);color:var(--bone)}
  body{font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.6}
  .serif{font-family:"Didot","Bodoni MT",ui-serif,Georgia,"Times New Roman",serif}
  header{
    display:flex;align-items:center;gap:22px;padding:26px 40px;
    border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,#0d0c0a,#0a0a0a);
  }
  header .lion{width:46px;height:46px;flex:0 0 auto}
  header .lion svg{width:100%;height:100%;display:block}
  h1{font-size:25px;margin:0;letter-spacing:.045em;color:var(--bone);font-weight:400}
  h1 em{font-style:normal;color:var(--gold)}
  .kicker{font-size:10.5px;letter-spacing:.34em;text-transform:uppercase;color:var(--gold-dim);margin-bottom:5px}
  .sub{color:var(--mute);font-size:12.5px;margin-top:3px;max-width:62ch}
  .spacer{flex:1}
  .ctrls{display:flex;align-items:center;gap:12px}
  button{font-family:inherit}
  .btn{
    background:transparent;border:1px solid var(--gold-dim);color:var(--gold);
    padding:9px 18px;border-radius:2px;cursor:pointer;font-size:11px;
    letter-spacing:.2em;text-transform:uppercase;transition:.18s;
  }
  .btn:hover{background:var(--gold);color:#0a0a0a;border-color:var(--gold)}
  .btn.solid{background:var(--gold);color:#0a0a0a;border-color:var(--gold)}
  .btn.solid:hover{background:#e8c65a}
  .btn:disabled{opacity:.35;cursor:default}
  .btn.ghost{border-color:var(--line);color:var(--mute)}
  .pill{font-size:10px;letter-spacing:.18em;text-transform:uppercase;padding:5px 11px;
        border:1px solid var(--line);border-radius:999px;color:var(--mute);white-space:nowrap}
  .pill.live{color:var(--gold);border-color:var(--gold-dim)}
  .mapwrap{padding:30px 40px 0;max-width:1780px;margin:0 auto}
  main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr) minmax(0,1fr);
       gap:26px;padding:26px 40px 60px;align-items:start;max-width:1780px;margin:0 auto}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:24px 26px;margin-bottom:26px}
  .panel h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.3em;color:var(--gold-dim);
            margin:0 0 18px;font-weight:500}
  .rule{height:1px;background:linear-gradient(90deg,var(--gold-dim),transparent);margin:0 0 18px}
  .stats{display:flex;gap:34px;margin-bottom:20px;flex-wrap:wrap}
  .stat .n{font-size:30px;color:var(--gold);letter-spacing:.02em}
  .stat .l{font-size:9.5px;letter-spacing:.22em;text-transform:uppercase;color:var(--mute)}
  .legend{display:flex;gap:20px;font-size:11px;color:var(--mute);margin-bottom:16px;flex-wrap:wrap}
  .legend span{display:inline-flex;align-items:center;gap:7px;letter-spacing:.08em}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
  #graphWrap{overflow:auto;max-height:600px;border:1px solid #191510;background:#0c0b09;padding:6px}
  svg.map{display:block}
  .node-circle{stroke:#0a0a0a;stroke-width:2;cursor:pointer;transition:r .2s}
  .node-circle:hover{stroke:var(--gold);stroke-width:2.5}
  .node-circle.sel{stroke:var(--gold);stroke-width:3}
  .node-label{font-size:10px;fill:#a9a196;pointer-events:none;font-family:inherit;letter-spacing:.03em}
  .node-label.hot{fill:var(--bone)}
  .edge{stroke:#2a241a;stroke-width:1.2}
  .edge.hot{stroke:var(--gold-dim);stroke-width:1.8}
  .q{padding:14px 0;border-bottom:1px solid #1a1610}
  .q:last-child{border-bottom:none}
  .q-num{font-size:9.5px;letter-spacing:.24em;color:var(--gold-dim);text-transform:uppercase}
  .q-prompt{font-size:13.5px;margin:5px 0 10px;color:var(--bone)}
  .choice{display:block;width:100%;text-align:left;background:transparent;border:1px solid #221d15;
    color:#cdc6ba;padding:9px 13px;margin-bottom:6px;border-radius:2px;cursor:pointer;font-size:12.5px;
    transition:.15s}
  .choice:hover{border-color:var(--gold-dim);color:var(--bone)}
  .choice.selected{border-color:var(--gold);color:var(--gold);background:rgba(212,175,55,.06)}
  .choice.right{border-color:var(--ok);color:#8fd0a4}
  .choice.wrong{border-color:var(--risk);color:#e08e8e}
  code,.mono{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:.92em;color:var(--gold)}
  ol.path{list-style:none;margin:0;padding:0;counter-reset:step}
  ol.path li{display:flex;gap:16px;padding:13px 0;border-bottom:1px solid #1a1610;cursor:pointer}
  ol.path li:last-child{border-bottom:none}
  ol.path li:hover .pname{color:var(--gold)}
  .pnum{font-size:19px;color:var(--gold-dim);min-width:30px;text-align:right}
  .pname{font-size:14px;color:var(--bone);transition:.15s}
  .pwhy{font-size:11.5px;color:var(--mute);margin-top:2px}
  .tag{font-size:9px;letter-spacing:.18em;text-transform:uppercase;padding:2px 8px;border-radius:999px;margin-left:9px}
  .tag.shaky{background:rgba(217,131,36,.13);color:var(--shaky)}
  .tag.at_risk{background:rgba(158,47,47,.15);color:#cf6a6a}
  #explainBox{font-size:14px;line-height:1.75;white-space:pre-wrap;color:#ddd6c9}
  .meta{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
  .meta .cname{font-size:16px;color:var(--gold);letter-spacing:.04em}
  .badge{font-size:9px;letter-spacing:.16em;text-transform:uppercase;padding:3px 9px;border-radius:999px}
  .badge-llm{background:rgba(212,175,55,.12);color:var(--gold)}
  .badge-fallback{background:#1a1610;color:var(--mute)}
  .retrieved{font-size:10.5px;color:var(--mute);margin-top:14px;letter-spacing:.04em;
             border-top:1px solid #1a1610;padding-top:12px}
  .retrieved b{color:var(--gold-dim);font-weight:500;letter-spacing:.18em;text-transform:uppercase;font-size:9.5px}
  .empty{color:var(--mute);font-size:13px;font-style:italic}
  .verdict{margin-top:12px;font-size:13px}
  .verdict.good{color:#8fd0a4}
  .verdict.bad{color:#e08e8e}
  footer{padding:26px 40px 46px;border-top:1px solid var(--line);color:var(--mute);font-size:11px;
         letter-spacing:.06em;max-width:1680px;margin:0 auto}
  footer a{color:var(--gold-dim);text-decoration:none}
  [dir="rtl"] .choice{text-align:right}
  [dir="rtl"] .pnum{text-align:left}
  @media (max-width:1400px){ main{grid-template-columns:minmax(0,1fr) minmax(0,1fr)} }
  @media (max-width:980px){ main{grid-template-columns:1fr;padding:22px} .mapwrap{padding:22px 22px 0} header{padding:22px} }
</style>
</head>
<body>
<header>
  <div class="lion">{{LION}}</div>
  <div>
    <div class="kicker" id="kicker">DHAHAB &middot; Applied SIGCSE TS 2026 Research</div>
    <h1 class="serif" id="title">Adaptive <em>CS</em> Tutor</h1>
    <div class="sub" id="subtitle">A prerequisite graph finds the root cause of a student's confusion, then a local model explains it from what they already know - in English or Arabic.</div>
  </div>
  <div class="spacer"></div>
  <div class="ctrls">
    <span class="pill" id="statusPill">checking model...</span>
    <button class="btn" id="langToggle" onclick="toggleLang()">العربية</button>
  </div>
</header>

<section class="mapwrap">
  <div class="panel">
    <h2 id="mapHeading">Curriculum Map</h2>
    <div class="rule"></div>
    <div class="stats" id="stats"></div>
    <div class="legend">
      <span><i class="dot" style="background:var(--ok)"></i><span id="legendOk">Mastered</span></span>
      <span><i class="dot" style="background:var(--shaky)"></i><span id="legendShaky">Shaky</span></span>
      <span><i class="dot" style="background:var(--risk)"></i><span id="legendRisk">At risk</span></span>
    </div>
    <div id="graphWrap"></div>
  </div>
</section>

<main>
  <div class="panel">
    <h2 id="pathHeading">Recommended Path</h2>
    <div class="rule"></div>
    <div id="pathBox"><div class="empty" id="pathEmpty">Take the diagnostic to generate a remediation path.</div></div>
  </div>

  <div class="panel">
    <h2 id="explainHeading">Explanation</h2>
    <div class="rule"></div>
    <div class="meta" id="explainMeta"></div>
    <div id="explainBox" class="empty">Select any concept on the map, or a step in the path.</div>
    <div id="retrieved"></div>
    <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn" id="practiceBtn" onclick="loadPractice()" disabled>Check my understanding</button>
    </div>
    <div id="practiceBox"></div>
  </div>

  <div class="panel">
    <h2 id="quizHeading">Diagnostic</h2>
    <div class="rule"></div>
    <div id="quizPanel"></div>
    <div style="margin-top:18px;display:flex;gap:10px">
      <button class="btn solid" id="submitBtn" onclick="submitQuiz()">Submit Diagnostic</button>
      <button class="btn ghost" onclick="resetAll()" id="resetBtn">Reset</button>
    </div>
  </div>
</main>

<footer>
  <span id="foot1">Built on peer-reviewed SIGCSE TS 2026 research:</span>
  <a href="https://doi.org/10.1145/3770761.3777339">10.1145/3770761.3777339</a> &middot;
  <span id="foot2">100% local, $0 stack - stdlib Python + Ollama qwen3-fast.</span>
</footer>

<script>
let lang="en", graphData=null, quizData=null, states={}, answers={},
    selected=null, currentQuestion=null, llmLive=false;

const STR={
 en:{kicker:"DHAHAB · Applied SIGCSE TS 2026 Research",title:"Adaptive <em>CS</em> Tutor",
   subtitle:"A prerequisite graph finds the root cause of a student's confusion, then a local model explains it from what they already know - in English or Arabic.",
   mapHeading:"Curriculum Map",pathHeading:"Recommended Path",explainHeading:"Explanation",quizHeading:"Diagnostic",
   legendOk:"Mastered",legendShaky:"Shaky",legendRisk:"At risk",submit:"Submit Diagnostic",reset:"Reset",
   practice:"Check my understanding",toggle:"العربية",
   selectHint:"Select any concept on the map, or a step in the path.",
   pathEmpty:"Take the diagnostic to generate a remediation path.",
   loading:"Thinking locally...",
   sMastered:"Mastered",sShaky:"Shaky",sRisk:"At risk",sScore:"Diagnostic",
   correct:"Correct - concept marked mastered. The map re-propagated.",
   wrong:"Not quite. Re-read the explanation above, then try again.",
   step:"Step",offline:"offline explanations",live:"qwen3-fast local"},
 ar:{kicker:"ذهب · بحث SIGCSE TS 2026 التطبيقي",title:"المدرّس <em>التكيّفي</em> لعلوم الحاسب",
   subtitle:"خريطة المتطلبات تحدد السبب الجذري لارتباك الطالب، ثم يشرحه نموذج محلي انطلاقاً مما يعرفه الطالب مسبقاً - بالعربية أو الإنجليزية.",
   mapHeading:"خريطة المنهج",pathHeading:"المسار الموصى به",explainHeading:"الشرح",quizHeading:"الاختبار التشخيصي",
   legendOk:"متقن",legendShaky:"غير مستقر",legendRisk:"معرّض للخطر",submit:"إرسال الاختبار",reset:"إعادة",
   practice:"تحقق من فهمي",toggle:"English",
   selectHint:"اختر أي مفهوم من الخريطة أو خطوة من المسار.",
   pathEmpty:"أكمل الاختبار التشخيصي لتوليد مسار المعالجة.",
   loading:"جارٍ التفكير محلياً...",
   sMastered:"متقن",sShaky:"غير مستقر",sRisk:"معرّض للخطر",sScore:"التشخيص",
   correct:"صحيح - تم وضع علامة إتقان على المفهوم وأُعيد حساب الخريطة.",
   wrong:"ليس تماماً. أعد قراءة الشرح أعلاه ثم حاول مرة أخرى.",
   step:"خطوة",offline:"شروح دون اتصال",live:"qwen3-fast محلي"}
};
const S=()=>STR[lang];

function applyLang(){
  const s=S();
  document.getElementById('kicker').textContent=s.kicker;
  document.getElementById('title').innerHTML=s.title;
  document.getElementById('subtitle').textContent=s.subtitle;
  document.getElementById('mapHeading').textContent=s.mapHeading;
  document.getElementById('pathHeading').textContent=s.pathHeading;
  document.getElementById('explainHeading').textContent=s.explainHeading;
  document.getElementById('quizHeading').textContent=s.quizHeading;
  document.getElementById('legendOk').textContent=s.legendOk;
  document.getElementById('legendShaky').textContent=s.legendShaky;
  document.getElementById('legendRisk').textContent=s.legendRisk;
  document.getElementById('submitBtn').textContent=s.submit;
  document.getElementById('resetBtn').textContent=s.reset;
  document.getElementById('practiceBtn').textContent=s.practice;
  document.getElementById('langToggle').textContent=s.toggle;
  const pe=document.getElementById('pathEmpty'); if(pe) pe.textContent=s.pathEmpty;
  document.body.dir = (lang==='ar')?'rtl':'ltr';
  setStatusPill();
  renderStats();
  if(lastDiagnosis) renderPath(lastDiagnosis.path);
}

function toggleLang(){
  lang=(lang==='en')?'ar':'en';
  applyLang();
  if(selected) explainNode(selected);
}

function setStatusPill(){
  const p=document.getElementById('statusPill');
  p.textContent = llmLive ? S().live : S().offline;
  p.className = 'pill'+(llmLive?' live':'');
}

let lastDiagnosis=null;

async function loadAll(){
  const [g,q,st,sess]=await Promise.all([
    fetch('/api/graph').then(r=>r.json()),
    fetch('/api/quiz').then(r=>r.json()),
    fetch('/api/status').then(r=>r.json()),
    fetch('/api/session').then(r=>r.json())
  ]);
  graphData=g; quizData=q;
  llmLive = st.ollama.reachable && st.ollama.model_present && !st.no_llm_forced;
  g.nodes.forEach(n=>states[n.id]='ok');
  const boot=new URLSearchParams(location.search);
  if(boot.get('lang')==='ar') lang='ar';
  renderQuiz();
  if(sess.shaky && sess.shaky.length){ applyDiagnosis(sess); } else { renderGraph(); }
  applyLang();
  const deep=boot.get('concept');
  if(deep && graphData.nodes.some(n=>n.id===deep)) explainNode(deep);
}

function renderStats(){
  const box=document.getElementById('stats');
  if(!lastDiagnosis){ box.innerHTML=''; return; }
  const r=lastDiagnosis.report, s=S();
  const items=[[r.ok,s.sMastered],[r.shaky,s.sShaky],[r.at_risk,s.sRisk]];
  if(r.correct!==undefined) items.push([r.correct+'/'+r.questions,s.sScore]);
  box.innerHTML=items.map(([n,l])=>
    `<div class="stat"><div class="n serif">${n}</div><div class="l">${l}</div></div>`).join('');
}

function renderGraph(){
  const w=graphData.width,h=graphData.height;
  const pos={}; graphData.nodes.forEach(n=>pos[n.id]=n);
  const chain = selected ? new Set([selected, ...(chainOf(selected))]) : new Set();
  let svg=`<svg class="map" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`;
  graphData.edges.forEach(e=>{
    const a=pos[e.from],b=pos[e.to];
    const hot = chain.has(e.from)&&chain.has(e.to) ? ' hot':'';
    svg+=`<line class="edge${hot}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  });
  graphData.nodes.forEach(n=>{
    const st=states[n.id]||'ok';
    const fill= st==='shaky'?'var(--shaky)': st==='at_risk'?'var(--risk)':'var(--ok)';
    const sel = n.id===selected?' sel':'';
    const r = st==='ok'?7:9;
    svg+=`<circle class="node-circle${sel}" fill="${fill}" cx="${n.x}" cy="${n.y}" r="${r}" onclick="explainNode('${n.id}')"><title>${n.name}</title></circle>`;
    svg+=`<text class="node-label${chain.has(n.id)?' hot':''}" x="${n.x+13}" y="${n.y+4}">${n.name}</text>`;
  });
  svg+='</svg>';
  document.getElementById('graphWrap').innerHTML=svg;
}

function chainOf(id){
  const byId={}; graphData.nodes.forEach(n=>byId[n.id]=n);
  const out=new Set(), stack=[id];
  while(stack.length){ const c=stack.pop();
    (byId[c].prereqs||[]).forEach(p=>{ if(!out.has(p)){out.add(p);stack.push(p);} }); }
  return out;
}

function renderQuiz(){
  document.getElementById('quizPanel').innerHTML=quizData.map((q,i)=>`
    <div class="q" data-qid="${q.id}">
      <div class="q-num">${String(i+1).padStart(2,'0')}</div>
      <div class="q-prompt">${q.prompt}</div>
      ${q.choices.map((c,j)=>`<button class="choice" onclick="selectChoice('${q.id}',${j},this)">${c}</button>`).join('')}
    </div>`).join('');
}

function selectChoice(qid,idx,el){
  answers[qid]=idx;
  el.parentElement.querySelectorAll('.choice').forEach(b=>b.classList.remove('selected'));
  el.classList.add('selected');
}

async function submitQuiz(){
  const res=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({answers})});
  applyDiagnosis(await res.json());
  document.getElementById('graphWrap').scrollIntoView({behavior:'smooth',block:'center'});
}

async function resetAll(){
  answers={}; selected=null; currentQuestion=null;
  document.getElementById('practiceBox').innerHTML='';
  document.getElementById('practiceBtn').disabled=true;
  document.getElementById('explainMeta').innerHTML='';
  const box=document.getElementById('explainBox');
  box.className='empty'; box.textContent=S().selectHint;
  document.getElementById('retrieved').innerHTML='';
  const res=await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await res.json();
  lastDiagnosis=null; states=d.states;
  renderQuiz(); renderGraph(); renderStats();
  document.getElementById('pathBox').innerHTML=`<div class="empty" id="pathEmpty">${S().pathEmpty}</div>`;
}

function applyDiagnosis(d){
  lastDiagnosis=d; states=d.states;
  renderGraph(); renderStats(); renderPath(d.path);
}

function renderPath(path){
  const box=document.getElementById('pathBox');
  if(!path||!path.length){
    box.innerHTML=`<div class="empty">${lang==='ar'?'لا توجد فجوات. كل المفاهيم متقنة.':'No gaps detected - every concept is clear.'}</div>`;
    return;
  }
  box.innerHTML='<ol class="path">'+path.map(s=>`
    <li onclick="explainNode('${s.concept}')">
      <div class="pnum serif">${String(s.order).padStart(2,'0')}</div>
      <div>
        <div class="pname">${s.name}<span class="tag ${s.state}">${s.state==='shaky'?S().sShaky:S().sRisk}</span></div>
        <div class="pwhy">${s.reason}${s.unblocks.length?` &middot; unblocks ${s.unblocks.length}`:''}</div>
      </div>
    </li>`).join('')+'</ol>';
}

async function explainNode(id){
  selected=id; currentQuestion=null;
  document.getElementById('practiceBox').innerHTML='';
  document.getElementById('practiceBtn').disabled=true;
  renderGraph();
  const box=document.getElementById('explainBox'), meta=document.getElementById('explainMeta');
  box.className=''; box.textContent=S().loading;
  meta.innerHTML=''; document.getElementById('retrieved').innerHTML='';
  const res=await fetch(`/api/explain?concept=${encodeURIComponent(id)}&lang=${lang}`);
  const d=await res.json();
  box.textContent=d.text;
  const cls=d.source==='llm'?'badge-llm':'badge-fallback';
  const txt=d.source==='llm'?S().live:S().offline;
  meta.innerHTML=`<span class="cname serif">${d.name}</span><span class="badge ${cls}">${txt}</span>`;
  document.getElementById('retrieved').innerHTML = d.retrieved.length
    ? `<div class="retrieved"><b>${lang==='ar'?'السياق المسترجع':'Retrieved context'}</b><br>${d.retrieved.join(' → ')} → <span style="color:var(--gold)">${d.concept}</span></div>`
    : `<div class="retrieved"><b>${lang==='ar'?'السياق المسترجع':'Retrieved context'}</b><br>${lang==='ar'?'مفهوم أساسي بلا متطلبات':'foundational - no prerequisites'}</div>`;
  document.getElementById('practiceBtn').disabled=false;
}

async function loadPractice(){
  const pb=document.getElementById('practiceBox');
  pb.innerHTML=`<div class="empty" style="margin-top:16px">${S().loading}</div>`;
  const res=await fetch(`/api/practice?concept=${encodeURIComponent(selected)}&lang=${lang}`);
  if(!res.ok){ pb.innerHTML=`<div class="empty" style="margin-top:16px">${lang==='ar'?'لا يوجد سؤال متاح دون اتصال.':'No offline practice question for this concept.'}</div>`; return; }
  const d=await res.json();
  currentQuestion=d.question;
  pb.innerHTML=`<div class="q" style="margin-top:16px;border-top:1px solid #1a1610;padding-top:16px">
    <div class="q-prompt">${d.question.prompt}</div>
    ${d.question.choices.map((c,i)=>`<button class="choice" id="pc${i}" onclick="answerPractice(${i})">${c}</button>`).join('')}
    <div id="verdict"></div></div>`;
}

async function answerPractice(i){
  const q=currentQuestion; if(!q) return;
  document.querySelectorAll('#practiceBox .choice').forEach(b=>b.disabled=true);
  document.getElementById('pc'+q.answer).classList.add('right');
  const v=document.getElementById('verdict');
  if(i===q.answer){
    v.className='verdict good'; v.textContent=S().correct+(q.why?' '+q.why:'');
    const res=await fetch('/api/master',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({concept:selected})});
    applyDiagnosis(await res.json());
  } else {
    document.getElementById('pc'+i).classList.add('wrong');
    v.className='verdict bad'; v.textContent=S().wrong;
  }
}

loadAll();
</script>
</body>
</html>
"""


def _warm(concepts, langs):
    """Fill explainer's cache so the first click in a demo is instant. Same
    generated text is served later, nothing is pre-scripted."""
    for concept in concepts:
        for lang in langs:
            _, source = explainer.explain(NODES, concept, lang=lang)
            print(f"  warmed explanation  {concept} [{lang}] -> {source}", flush=True)
    for concept in concepts:
        for lang in langs:
            source = explainer.prewarm_practice(NODES, concept, QUIZ, lang=lang)
            print(f"  warmed question     {concept} [{lang}] -> {source}", flush=True)
    print("  warm-up complete", flush=True)


def main():
    global FORCE_NO_LLM
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--no-llm", action="store_true",
                        help="Force canned explanations, never call Ollama")
    parser.add_argument("--warm", nargs="*", metavar="CONCEPT",
                        help="Pre-generate explanations in the background so a live "
                             "demo has no first-token wait (default: functions recursion lists)")
    parser.add_argument("--warm-langs", nargs="+", choices=["en", "ar"], default=["en", "ar"])
    args = parser.parse_args()
    FORCE_NO_LLM = args.no_llm

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = "OFFLINE (canned explanations)" if FORCE_NO_LLM else "LOCAL LLM + fallback"
    probe = explainer.ollama_available() if not FORCE_NO_LLM else None
    print(f"  Adaptive CS Tutor  ->  http://localhost:{args.port}   [{mode}]")
    print(f"  {len(NODES)} concepts, {len(QUIZ)} diagnostic questions")
    if probe:
        state = "reachable" if probe["reachable"] else "not reachable (will use canned text)"
        print(f"  Ollama {explainer.OLLAMA_MODEL}: {state}")

    if args.warm is not None and not FORCE_NO_LLM:
        concepts = args.warm or ["functions", "recursion", "lists"]
        concepts = [c for c in concepts if c in NODES]
        print(f"  warming {len(concepts)} concept(s) x {len(args.warm_langs)} language(s) "
              f"in the background; Arabic takes ~60s on an M1")
        threading.Thread(target=_warm, args=(concepts, args.warm_langs), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    main()
