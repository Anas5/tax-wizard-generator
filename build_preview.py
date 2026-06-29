"""
Build a single self-contained, offline HTML previewer for the generated wizards.

Reads every wizard in output/wizards/ and embeds them into output/preview.html,
so the file can be opened by double-clicking with no server and no internet
(browsers block file:// fetches, hence embedding rather than loading at runtime).

  python build_preview.py
  # then open output/preview.html
"""

import os
import json
import glob

HERE = os.path.dirname(__file__)
WIZARDS_DIR = os.path.join(HERE, "output", "wizards")
OUT = os.path.join(HERE, "output", "preview.html")
# Mirror to the repo root so GitHub Pages serves it at the site root.
ROOT_INDEX = os.path.join(HERE, "index.html")

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax Wizard Preview</title>
<style>
  :root {
    --bg:#0f1116; --panel:#171a21; --line:#272c37; --text:#e6e8ee;
    --muted:#9aa3b2; --accent:#4f8cff; --chip:#1f2733; --done:#2f9e6b;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.55 system-ui,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:18px 22px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:center; flex-wrap:wrap;
           position:sticky; top:0; background:var(--bg); z-index:5; }
  header h1 { font-size:17px; margin:0; font-weight:650; }
  select, input { background:var(--panel); color:var(--text);
            border:1px solid var(--line); border-radius:8px; padding:8px 10px;
            font-size:14px; }
  select { min-width:330px; }
  .meta { color:var(--muted); font-size:13px; }
  main { max-width:980px; margin:0 auto; padding:22px; }
  .assumptions { background:var(--panel); border:1px solid var(--line);
            border-left:3px solid #c9a227; border-radius:8px; padding:12px 14px;
            margin-bottom:18px; color:var(--muted); font-size:13px; }
  .sec { margin:26px 0 10px; font-size:13px; letter-spacing:.04em;
         text-transform:uppercase; color:var(--accent); font-weight:650; }
  .step { background:var(--panel); border:1px solid var(--line);
          border-radius:10px; padding:14px 16px; margin:10px 0; }
  .step .no { color:var(--muted); font-size:12px; }
  .q { margin:4px 0 10px; }
  .opts { display:flex; gap:8px; flex-wrap:wrap; }
  .opt { background:var(--chip); border:1px solid var(--line); border-radius:20px;
         padding:4px 12px; font-size:13px; color:var(--muted); }
  .ref { margin-top:10px; font-size:12px; color:var(--muted);
         border-top:1px dashed var(--line); padding-top:8px; }
  .term { margin-top:26px; background:var(--panel); border:1px solid var(--line);
          border-left:3px solid var(--done); border-radius:8px; padding:12px 14px; }
  .hidden { display:none; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Tax Wizard Preview</h1>
  <select id="picker"></select>
  <input id="filter" placeholder="Filter steps…" />
  <span class="meta" id="count"></span>
</header>
<main id="view"></main>
<script>
const WIZARDS = __DATA__;
const view = document.getElementById('view');
const picker = document.getElementById('picker');
const filter = document.getElementById('filter');
const count = document.getElementById('count');

WIZARDS.sort((a,b)=>a.title.localeCompare(b.title)).forEach((w,i)=>{
  const o=document.createElement('option'); o.value=i; o.textContent=w.title; picker.appendChild(o);
});

function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function render(i){
  const w=WIZARDS[i]; const f=filter.value.trim().toLowerCase();
  let html='';
  if(w.assumptions&&w.assumptions.length){
    html+='<div class="assumptions"><b>Assumptions</b><br>'+
      w.assumptions.map(esc).join('<br>')+'</div>';
  }
  let lastSec=null, shown=0;
  (w.steps||[]).forEach(s=>{
    if(f && !((s.question||'').toLowerCase().includes(f) ||
             (s.section||'').toLowerCase().includes(f))) return;
    shown++;
    if(s.section!==lastSec){ html+='<div class="sec">'+esc(s.section||'')+'</div>'; lastSec=s.section; }
    html+='<div class="step"><div class="no">'+esc(s.id)+
      (s.item_no?(' · item '+s.item_no):'')+' · '+esc(s.type||'')+'</div>'+
      '<div class="q">'+esc(s.question)+'</div><div class="opts">'+
      (s.options||[]).map(o=>'<span class="opt">'+esc(o)+'</span>').join('')+'</div>'+
      '<div class="ref">Cites: '+esc((s.guidance_ref||'').slice(0,160))+'</div></div>';
  });
  (w.terminal_actions||[]).forEach(t=>{
    html+='<div class="term"><b>'+esc(t.condition||'Complete')+'</b> — file: '+
      esc((t.forms||[]).join(', ')||'-')+'</div>';
  });
  count.textContent=shown+' / '+(w.steps||[]).length+' steps';
  view.innerHTML=html;
}

picker.addEventListener('change',()=>render(+picker.value));
filter.addEventListener('input',()=>render(+picker.value));
render(0);
</script>
</body>
</html>
"""


def main():
    files = sorted(glob.glob(os.path.join(WIZARDS_DIR, "*.json")))
    wizards = [json.load(open(f, encoding="utf-8")) for f in files]
    html = PAGE.replace("__DATA__", json.dumps(wizards))
    for path in (OUT, ROOT_INDEX):
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    print(f"Wrote {OUT} and {ROOT_INDEX} with {len(wizards)} wizards "
          f"({sum(len(w.get('steps', [])) for w in wizards)} steps total).")


if __name__ == "__main__":
    main()
