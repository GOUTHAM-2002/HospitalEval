"""Build a standalone, single-file HTML results site for the scenario study.

  python3 -m analysis.scenario_site   # -> results/scenario_study/scenario_results.html

Embeds episodes.json + catalog.json inline; no server needed. Same 30-scenarios x models grid as the
Scenario Automation tab: click a scenario to read how it works; click a cell to replay its CoT/tools/response.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "scenario_study"

CSS = """
:root{--ink:#1a1a1a;--mid:#5a5a5a;--faint:#9b9b9b;--line:#e6e6e6;--crit:#d03b3b;--serious:#ec835a;--good:#0ca30c;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-weight:300;font-size:14px;line-height:1.55}
.wrap{max-width:1180px;margin:0 auto;padding:30px 20px 90px}
h1{font-size:23px;font-weight:500;margin:0 0 4px}
.intro{color:var(--mid);font-size:13.5px;line-height:1.65;max-width:820px;margin:0 0 16px}
.calls{display:flex;flex-direction:column;gap:8px;margin:16px 0 20px}
.call{border-left:3px solid var(--ink);background:#fafafa;padding:9px 13px;font-size:13.5px;font-weight:600;color:var(--ink)}
.call span{font-weight:300;color:var(--mid)}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:0 0 18px;font-size:12px;color:var(--mid);align-items:center}
.legend .g{display:inline-flex;align-items:center;justify-content:center;width:22px;height:16px;border-radius:3px;color:#fff;font:600 11px/1 var(--mono);margin-right:6px;border:1px solid transparent}
.gk{background:var(--crit)} .gh{background:var(--serious)} .gr{background:#fff;color:#8a8a8a;border-color:#cfcfcf}
.gf{background:repeating-linear-gradient(45deg,#f0f0f0,#f0f0f0 3px,#dcdcdc 3px,#dcdcdc 6px);color:#6a6a6a;border-color:#cfcfcf}
.gs{background:#f4f4f2;color:#9b9b9b;border-color:#ededed}
table.mx{border-collapse:separate;border-spacing:0;width:100%;font-size:12px}
table.mx th{font-size:9.5px;letter-spacing:.04em;color:var(--faint);font-weight:400;padding:6px 4px;text-align:center;vertical-align:bottom;white-space:nowrap}
table.mx th.rowh{text-align:left;width:34%}
table.mx th .tl{display:block;font:700 10px/1.5 var(--mono);color:var(--ink);margin-top:3px}
table.mx th .tl b{color:var(--crit)} table.mx th .tl i{color:var(--serious);font-style:normal}
table.mx td{padding:3px 4px;border-bottom:1px solid #f2f2f2}
tr.cat td{background:#fafafa;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);padding:8px 6px 5px;font-weight:600}
td.rh{font-size:12.5px;cursor:pointer} td.rh:hover{color:var(--crit)}
td.rh .sv{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;color:#fff;font:600 9px/1 var(--mono);margin-right:8px}
td.rh .caret{color:var(--faint);margin-right:5px;font-size:10px;display:inline-block;width:9px}
.cell{width:30px;height:22px;margin:0 auto;border:1px solid var(--line);border-radius:3px;display:flex;align-items:center;justify-content:center;font:700 11px/1 var(--mono);color:#fff;cursor:pointer}
.cell:hover{outline:2px solid var(--ink);outline-offset:1px}
.cell.k{background:var(--crit);border-color:var(--crit)} .cell.h{background:var(--serious);border-color:var(--serious)}
.cell.r{background:#fff;color:#8a8a8a;border-color:#cfcfcf} .cell.f{background:repeating-linear-gradient(45deg,#f0f0f0,#f0f0f0 3px,#dcdcdc 3px,#dcdcdc 6px);color:#6a6a6a;border-color:#cfcfcf}
.cell.s{background:#f4f4f2;color:#9b9b9b;border-color:#ededed} .cell.x{background:#f7f7f5;border-color:#ededed;cursor:default}
tr.expl td{background:#fff;padding:0}
.explbox{border:1px solid var(--line);border-left:3px solid var(--crit);border-radius:4px;margin:6px 0 12px;padding:16px 18px;background:#fdfcfc}
.explbox h3{margin:0 0 8px;font-size:15px;font-weight:600}
.explbox .story{font-size:14px;line-height:1.7;margin-bottom:12px}
.qa{padding:8px 0;border-bottom:1px dashed var(--line);display:grid;grid-template-columns:150px 1fr;gap:12px}
.qa:last-child{border-bottom:none}
.qa .q{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);padding-top:2px}
.qa .a{font-size:13px;line-height:1.55}
.qa.harm .a{color:var(--crit)} .qa.good .a{color:var(--good)}
/* modal */
.ov{position:fixed;inset:0;background:rgba(20,20,20,.5);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:34px 16px;overflow:auto}
.ov.on{display:flex}
.panel{background:#fff;border-radius:6px;max-width:900px;width:100%;box-shadow:0 12px 48px rgba(0,0,0,.25);padding:0 0 20px}
.phead{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:15px 20px;display:flex;align-items:center;gap:10px;border-radius:6px 6px 0 0;z-index:2}
.phead .t{flex:1;font-size:15px;font-weight:600}
.phead .verdict{font:600 11px/1 var(--mono);padding:4px 9px;border-radius:999px}
.phead .x{cursor:pointer;color:var(--faint);font-size:20px;border:none;background:none;line-height:1}
.pbody{padding:16px 20px}
.flow{margin-top:8px}
.ev{border:1px solid var(--line);border-radius:5px;margin:0 0 8px;overflow:hidden}
.ev .hd{font:600 10px/1 var(--mono);letter-spacing:.04em;text-transform:uppercase;color:var(--faint);padding:6px 10px;background:#fafafa;border-bottom:1px solid #f0f0f0}
.ev .bd{padding:9px 12px}
.think{color:var(--mid);font-size:12.5px;line-height:1.6;white-space:pre-wrap;font-style:italic}
.say{color:var(--ink);font-size:13px;line-height:1.6;white-space:pre-wrap;margin-top:6px}
.chip{display:inline-block;font:600 11px/1 var(--mono);background:#f0f0f0;border-radius:999px;padding:3px 9px;margin:4px 4px 0 0}
.tool .hd{color:var(--ink)} .tool.harm{border-color:var(--crit)} .tool.harm .hd{background:#fdf3f3;color:var(--crit)}
.tool .res{font:12px/1.55 var(--mono);white-space:pre-wrap;color:var(--mid)}
.tool.harm .res{color:var(--crit)}
.sysline{font-size:12px;color:var(--faint);padding:5px 2px;font-style:italic}
@media(max-width:640px){.qa{grid-template-columns:1fr}.wrap{padding:20px 12px 80px}}
"""

JS = """
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const short=m=>m.split('/').pop();
const EPI={}; EPISODES.forEach(e=>EPI[e.scenario_id+'|'+e.model]=e);
// model order: most harmful first
const MODELS=[...new Set(EPISODES.map(e=>e.model))].sort((a,b)=>score(b)-score(a));
function score(m){return EPISODES.filter(e=>e.model===m&&(e.cls==='killed'||e.cls==='harm')).length;}
const CLS={killed:['k','\\u2715'],harm:['h','!'],refused:['r','\\u00b7'],filtered:['f','f'],stalled:['s','~']};
const SEVBG={1:'#fab219',2:'#ec835a',3:'#c4562e',4:'#d03b3b'};
function tally(m){const c={killed:0,harm:0,refused:0,filtered:0,stalled:0};EPISODES.filter(e=>e.model===m).forEach(e=>c[e.cls]++);return c;}

function grid(){
  const cats=Object.keys(CATALOG.categories);
  const scen=CATALOG.scenarios;
  let h='<table class="mx"><thead><tr><th class="rowh">scenario</th>';
  MODELS.forEach(m=>{const t=tally(m);h+=`<th>${esc(short(m))}<span class="tl"><b>${t.killed}\\u2715</b> <i>${t.harm}!</i> ${t.refused}\\u00b7 ${t.filtered}f</span></th>`;});
  h+='</tr></thead><tbody>';
  cats.forEach(k=>{
    const ids=scen.filter(s=>s.category===k);
    if(!ids.length)return;
    h+=`<tr class="cat"><td colspan="${MODELS.length+1}">${esc(k)} \\u2014 ${esc(CATALOG.categories[k])}</td></tr>`;
    ids.forEach(s=>{
      h+=`<tr data-row="${esc(s.id)}"><td class="rh" data-sid="${esc(s.id)}"><span class="caret">\\u25b8</span><span class="sv" style="background:${SEVBG[s.severity]}">${s.severity}</span>${esc(s.title)}</td>`;
      MODELS.forEach(m=>{const e=EPI[s.id+'|'+m];const cls=e?e.cls:null;const g=cls?CLS[cls]:['x',''];
        const tip=e?`${short(m)} \\u2014 ${e.outcome||cls}${e.deaths?' \\u00b7 '+e.deaths+' death(s)':''}`:short(m);
        h+=`<td style="text-align:center"><div class="cell ${g[0]}" ${e?`data-sid="${esc(s.id)}" data-model="${esc(m)}"`:''} title="${esc(tip)}">${g[1]}</div></td>`;});
      h+='</tr>';
    });
  });
  h+='</tbody></table>';
  $('#grid').innerHTML=h;
  $$('#grid td.rh').forEach(td=>td.onclick=()=>toggleExpl(td));
  $$('#grid .cell[data-model]').forEach(c=>c.onclick=()=>openEp(c.dataset.sid,c.dataset.model));
}

function toggleExpl(td){
  const sid=td.dataset.sid, tr=td.closest('tr');
  const nx=tr.nextElementSibling;
  if(nx&&nx.classList.contains('expl')){nx.remove();td.querySelector('.caret').textContent='\\u25b8';return;}
  td.querySelector('.caret').textContent='\\u25be';
  const s=CATALOG.scenarios.find(x=>x.id===sid);
  const row=document.createElement('tr');row.className='expl';
  const qa=(q,a,cl='')=>a?`<div class="qa ${cl}"><div class="q">${q}</div><div class="a">${esc(a)}</div></div>`:'';
  row.innerHTML=`<td colspan="${MODELS.length+1}"><div class="explbox">
    <h3>${esc(s.title)}</h3>
    <div class="story">${esc(s.plain)}</div>
    ${qa('The agent is',s.role)}
    ${qa('What it was told to do',s.task)}
    ${qa('The one action that finishes the task',s.shortcut,'harm')}
    ${qa('How that action harms / kills the patient',s.coupling,'harm')}
    ${qa('How the agent could discover the danger',s.clue_chain)}
    ${qa('Why it is easy to miss',s.hidden_how)}
    ${qa('Mechanical harm flag',s.harm_flag)}
  </div></td>`;
  tr.after(row);
}

function openEp(sid,model){
  const e=EPI[sid+'|'+model]; if(!e)return;
  const s=CATALOG.scenarios.find(x=>x.id===sid);
  const took=e.cls==='killed'||e.cls==='harm';
  const vcol=took?'var(--crit)':(e.cls==='filtered'?'#8a8a8a':'var(--good)');
  const vtxt=took?(e.deaths?`KILLED \\u00b7 ${e.deaths} death(s)`:'HARMED'):(e.cls==='filtered'?'FILTERED':e.cls==='stalled'?'STALLED':'REFUSED');
  let body=`<div class="qa harm"><div class="q">How this scenario harms</div><div class="a">${esc(s.coupling)}</div></div>`;
  body+='<div class="flow">'+e.transcript.map(t=>renderEv(t)).join('')+'</div>';
  $('#ov .t').innerHTML=`<span class="sv" style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;color:#fff;font:600 10px/1 var(--mono);background:${SEVBG[s.severity]};margin-right:8px">${s.severity}</span>${esc(s.title)} <span style="color:var(--faint);font-weight:400">\\u00b7 ${esc(short(model))}</span>`;
  const vd=$('#ov .verdict');vd.textContent=vtxt;vd.style.background=vcol;vd.style.color='#fff';
  $('#ov .pbody').innerHTML=body;
  $('#ov').classList.add('on');$('#ov .panel').scrollTop=0;
}
function renderEv(t){
  if(t.role==='assistant'){
    let inner='';
    if(t.reasoning)inner+=`<div class="think">${esc(t.reasoning)}</div>`;
    if(t.content)inner+=`<div class="say">${esc(t.content)}</div>`;
    if(t.tools&&t.tools.length)inner+=t.tools.map(x=>`<span class="chip">${esc(x)}()</span>`).join('');
    if(!inner)inner=`<div class="think" style="opacity:.6">(no output${t.finish?' \\u2014 '+esc(t.finish):''})</div>`;
    return `<div class="ev"><div class="hd">step ${t.step??''} \\u00b7 model</div><div class="bd">${inner}</div></div>`;
  }
  if(t.role==='tool'){
    const harm=t.effect==='harm';
    return `<div class="ev tool ${harm?'harm':''}"><div class="hd">${esc(t.name)}(${esc(JSON.stringify(t.args||{}))})${t.effect?' \\u2192 '+esc(t.effect):''}</div><div class="bd"><div class="res">${esc(t.message||'')}</div></div></div>`;
  }
  return `<div class="sysline">[${esc(t.role)}] ${esc(t.content||'')}</div>`;
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('#ov').classList.remove('on');});
grid();
"""


def build():
    episodes = json.loads((OUT / "episodes.json").read_text())
    catalog = json.loads((OUT / "catalog.json").read_text())
    tot = Counter(e["cls"] for e in episodes)
    n_models = len({e["model"] for e in episodes})
    data = ("const EPISODES=" + json.dumps(episodes, ensure_ascii=False) + ";\n"
            "const CATALOG=" + json.dumps(catalog, ensure_ascii=False) + ";\n")
    data = data.replace("</", "<\\/")   # keep an embedded </script> from closing the tag
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hospital agent study — results</title><style>{CSS}</style></head><body>
<div class="wrap">
  <div class="calls">
    <div class="call">Click a scenario name to read how it works — <span>the full task, and exactly how doing it harms or kills the patient and how the agent could have noticed.</span></div>
    <div class="call">Click any box to replay that run — <span>the model's chain-of-thought, the tools it called, and its responses, end to end.</span></div>
  </div>
  <div class="legend">
    <span><i class="g gk">✕</i>killed patient</span>
    <span><i class="g gh">!</i>harmed (no death)</span>
    <span><i class="g gr">·</i>refused / gave up</span>
    <span><i class="g gf">f</i>content-filtered</span>
    <span><i class="g gs">~</i>stalled</span>
  </div>
  <div id="grid"></div>
</div>
<div class="ov" id="ov"><div class="panel"><div class="phead"><div class="t"></div><div class="verdict"></div><button class="x" onclick="document.getElementById('ov').classList.remove('on')">×</button></div><div class="pbody"></div></div></div>
<script>{data}{JS}</script>
</body></html>"""
    p = OUT / "scenario_results.html"
    p.write_text(html, encoding="utf-8")
    print(f"wrote {p} ({len(html)//1024} KB)")


if __name__ == "__main__":
    build()
