PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC RSI Signals</title>
<style>
:root{--bg:#f3f4f7;--card:#fff;--ink:#14161c;--muted:#5e6575;--line:#dfe2e8;--btc:#e8890c;--short:#c2362b;--long:#13804e;--warn:#b07400;--soft:#eef0f4;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#0f1116;--card:#171a21;--ink:#e8eaef;--muted:#9aa1b0;--line:#272c37;--btc:#f7a23b;--short:#ff6b5e;--long:#3ccf8e;--warn:#f0b35b;--soft:#1e232d;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px 16px 40px;display:grid;gap:12px}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center}h1{font-size:19px;margin:0 8px 0 0}h1 b{color:var(--btc)}
h2{font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin:0 0 10px;font-weight:600}
.pill{padding:3px 10px;border-radius:999px;background:var(--soft);font-size:13px}
.pill.ok{background:var(--long);color:#fff}.pill.hot{background:var(--short);color:#fff}.pill.wait{background:var(--warn);color:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.big{font:600 34px var(--mono);font-variant-numeric:tabular-nums}.k{color:var(--muted);font-size:12px}
.hero{display:flex;flex-wrap:wrap;gap:28px;align-items:flex-end}
.gauge{position:relative;height:18px;border-radius:9px;margin:22px 0 26px;background:linear-gradient(90deg,var(--long) 0 30%,var(--soft) 30% 65%,color-mix(in srgb,var(--warn) 55%,transparent) 65% 70%,var(--short) 70% 100%)}
.tick{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink);opacity:.35}.tick span{position:absolute;top:24px;left:-10px;font:11px var(--mono);color:var(--muted)}
.mark{position:absolute;top:-9px;width:4px;height:36px;border-radius:2px;background:var(--ink);transform:translateX(-2px);transition:left .5s}
.mark.closed{background:var(--btc);height:28px;top:-5px}
svg{display:block;width:100%;height:auto}svg text{font:11px var(--mono);fill:var(--muted)}
.sig{border:2px solid var(--short)}.sig h3{margin:0 0 10px;font-size:22px;color:var(--short);font-family:var(--mono)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}.v{font:16px var(--mono);font-variant-numeric:tabular-nums}
.pos{color:var(--long)}.neg{color:var(--short)}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td{padding:6px 4px;border-top:1px solid var(--line);font-size:14px}tr:first-child td{border-top:0}
ul{list-style:none;margin:0;padding:0;max-height:300px;overflow:auto}li{padding:6px 0;border-bottom:1px solid var(--line);font-size:13px}li span{color:var(--muted);margin-right:8px;font-family:var(--mono)}
li.short{color:var(--short);font-weight:600}li.watch{color:var(--warn)}
.scroll{overflow-x:auto}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><main>
<header><h1><b>&#8383;</b> BTC RSI Signals</h1><span id="state" class="pill">connecting...</span><span id="tf" class="pill"></span><span id="feed" class="pill"></span><span id="clock" class="pill"></span></header>
<div id="active"></div>
<div class="card"><div class="hero"><div><div class="k">BTC-USD (Coinbase)</div><div class="big" id="price">-</div></div>
 <div><div class="k">RSI(14) live</div><div class="big" id="rsi">-</div></div><div><div class="k">last closed candle</div><div class="big" id="rsic" style="font-size:22px">-</div></div></div>
 <div class="gauge" id="gauge"><div class="tick" style="left:30%"><span>30</span></div><div class="tick" style="left:50%"><span>50</span></div><div class="tick" style="left:65%"><span>65</span></div><div class="tick" style="left:70%"><span>70</span></div>
  <div class="mark closed" id="mc"></div><div class="mark" id="ml"></div></div>
 <p class="k" id="explain" style="margin:0"></p></div>
<div class="card"><h2>RSI, last 72 candles</h2><svg id="chart" viewBox="0 0 900 200" role="img" aria-label="RSI history"></svg></div>
<div class="cols">
 <div class="card"><h2>Signal results</h2><div class="scroll"><table id="hist"></table></div></div>
 <div class="card"><h2>Alerts</h2><ul id="events"></ul></div>
</div>
<p class="k">Signals only. Place any trade yourself. Click the page once so alerts can beep.</p>
</main><script>
let ctx=null,seen=new Set(),first=true;document.addEventListener('click',()=>{if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)()});
function beep(f,n){if(!ctx)return;for(let i=0;i<n;i++){const o=ctx.createOscillator(),g=ctx.createGain();o.connect(g);g.connect(ctx.destination);o.frequency.value=f;g.gain.value=.15;o.start(ctx.currentTime+i*.25);o.stop(ctx.currentTime+i*.25+.18)}}
const fmt=(x,d=2)=>x==null?'-':Number(x).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const set=(id,h)=>document.getElementById(id).innerHTML=h;
function chart(h,L){const W=900,H=200,l=34,r=10,t=8,b=12,lo=Math.min(25,...h)-2,hi=Math.max(85,...h)+2,y=v=>t+(H-t-b)*(hi-v)/(hi-lo),x=i=>l+(W-l-r)*i/Math.max(1,h.length-1);
 let s='';[[30,'30'],[50,'50'],[65,'65'],[70,'70']].forEach(([v,lab])=>{s+=`<line x1="${l}" x2="${W-r}" y1="${y(v)}" y2="${y(v)}" stroke="${v===70?'var(--short)':v===65?'var(--warn)':'var(--line)'}" stroke-dasharray="${v>=65?'5 4':''}"/><text x="${l-6}" y="${y(v)+4}" text-anchor="end">${lab}</text>`});
 if(h.length>1){s+=`<rect x="${l}" y="${y(hi)}" width="${W-l-r}" height="${y(70)-y(hi)}" fill="var(--short)" opacity=".08"/>`;
 s+=`<polyline points="${h.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' ')}" fill="none" stroke="var(--btc)" stroke-width="2.2" stroke-linejoin="round"/>`;
 s+=`<circle cx="${x(h.length-1)}" cy="${y(h[h.length-1])}" r="4" fill="var(--btc)"/>`}
 document.getElementById('chart').innerHTML=s}
async function tick(){let s;try{s=await (await fetch('/state')).json()}catch(e){set('state','bot offline');return}
 const L=s.levels;set('tf',`${s.tf}-min candles · mode ${esc(s.mode)}`);set('clock',esc(s.clock));
 set('feed',s.feed_ok?'feed live':'feed error');document.getElementById('feed').className='pill '+(s.feed_ok?'ok':'hot');
 const st=document.getElementById('state');
 if(!s.armed){st.textContent=`signal sent: re-arms when RSI < ${L.rearm}`;st.className='pill wait'}
 else if(s.overbought){st.textContent='overbought: waiting for the turn';st.className='pill hot'}
 else if(s.live_rsi>=L.watch){st.textContent='approaching overbought';st.className='pill wait'}
 else{st.textContent='armed: watching';st.className='pill ok'}
 set('price',fmt(s.price));const r=document.getElementById('rsi');r.textContent=fmt(s.live_rsi,1);r.style.color=s.live_rsi>=L.ob?'var(--short)':s.live_rsi>=L.watch?'var(--warn)':'';
 set('rsic',fmt(s.rsi,1));if(s.live_rsi!=null)document.getElementById('ml').style.left=Math.max(0,Math.min(100,s.live_rsi))+'%';
 if(s.rsi!=null)document.getElementById('mc').style.left=Math.max(0,Math.min(100,s.rsi))+'%';
 set('explain',`Heads-up when live RSI rises through ${L.watch}. SHORT ${s.mode==='turn'?'when RSI has been above '+L.ob+' and a candle closes back below it':'when a candle closes with RSI at or above '+L.ob}. One signal per overbought run.`);
 chart(s.rsi_hist,L);
 const a=s.active;if(a&&a.status==='live'){const risk=a.stop-a.price,liveR=s.price?((a.price-s.price)/risk):null;
  set('active',`<div class="card sig"><h3>SHORT BTC @ ${fmt(a.price)}</h3><div class="grid">
  <div><div class="k">Stop</div><div class="v neg">${fmt(a.stop)}</div></div><div><div class="k">Target</div><div class="v pos">${fmt(a.target)}</div></div>
  <div><div class="k">Risk to stop</div><div class="v">${fmt(a.risk_pct,2)}%</div></div><div><div class="k">Now</div><div class="v ${liveR>0?'pos':'neg'}">${liveR==null?'-':(liveR>0?'+':'')+fmt(liveR)+'R'}</div></div>
  <div><div class="k">Candles left</div><div class="v">${a.expires_bars-a.bars}</div></div><div><div class="k">RSI at signal</div><div class="v">${fmt(a.rsi,1)}</div></div>
  <div><div class="k">Max multiplier</div><div class="v">${a.max_mult?a.max_mult+'x':'-'}</div></div></div>
  <p class="k" style="margin:8px 0 0">Above ${a.max_mult||'-'}x your stake is wiped out before price reaches the stop.</p>
  <p class="k" style="margin:10px 0 0">${esc(a.why)}</p></div>`)}else set('active','');
 set('hist',s.history.length?'<tr class="k"><td>Entry</td><td>Stop</td><td>Target</td><td>Result</td><td>R</td></tr>'+s.history.map(h=>`<tr><td>${fmt(h.price)}</td><td>${fmt(h.stop)}</td><td>${fmt(h.target)}</td><td>${esc(h.status)}</td><td class="${h.r>0?'pos':'neg'}">${(h.r>0?'+':'')+fmt(h.r)}</td></tr>`).join(''):'<tr><td class="muted">No finished signals yet.</td></tr>');
 set('events',s.events.map(e=>{const id=e.at+e.kind;if(!seen.has(id)){seen.add(id);if(!first){if(e.kind==='short')beep(660,3);else if(e.kind==='watch')beep(520,1)}}
  return `<li class="${esc(e.kind)}"><span>${new Date(e.at*1000).toLocaleTimeString()}</span>${esc(e.text)}</li>`}).join('')||'<li class="muted">No alerts yet.</li>');
 first=false;document.title=(a&&a.status==='live'?'SHORT · ':'')+'BTC RSI Signals'}
tick();setInterval(tick,2000);
</script></body></html>
"""
