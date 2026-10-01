"""
chart_trades.py -- draw one stock's 30-second session with the engine's own
entries and exits marked, so the logic can be SEEN rather than argued about.

Usage: python3 chart_trades.py SYMBOL [DAY] [out.html]
Writes a self-contained HTML file: candles, EMA8/MA12/VWAP, SuperTrend band,
MACD and RSI panels, and every entry/exit the signal engine took.
"""
import json, sys
from pathlib import Path
import entry_lab as E, sri_stack as S, signal_sim as SS

HERE = Path(__file__).resolve().parent
SYM = (sys.argv[1] if len(sys.argv) > 1 else "TEJASNET").upper()
DAY = sys.argv[2] if len(sys.argv) > 2 else "20260904"
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else f"/tmp/chart_{SYM}_{DAY}.html")

tape = E.load_tape(DAY)
if SYM not in tape:
    print(f"{SYM}: no tape for {DAY} -- run build_tape first"); sys.exit(1)
bars = [b for b in tape[SYM] if "09:15:00" <= b["hhmm"] <= "15:30:00"]
st = S.compute(bars)

# ONLY this symbol, so the chart shows what the logic does with the stock
# itself rather than what survived competition for slots.
_orig = E.load_tape
E.load_tape = lambda d, _f=tape, _s=SYM: {_s: _f[_s]}
closed, _ = SS.run(DAY, log=None)
E.load_tape = _orig
trades = [t for t in closed if t["sym"] == SYM]

data = {
  "sym": SYM, "day": DAY,
  "bars": [{"t": b["hhmm"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]} for b in bars],
  "ema8": [r["ema8"] for r in st], "ma12": [r["ma12"] for r in st],
  "vwap": [r["vwap"] for r in st], "stdir": [r["st_dir"] for r in st],
  "stline": [r["st_line"] for r in st],
  "macd": [r["macd"] for r in st], "macdsig": [r["macd_sig"] for r in st],
  "rsi": [r["rsi"] for r in st],
  "trades": [{"in_t": t["in_t"], "in": t["in"], "out_t": t["out_t"], "out": t["out"],
              "why": t["why"], "net": round(t["net"]), "qty": t["qty"]} for t in trades],
}

HTML = """<title>%(SYM)s %(DAY)s</title>
<style>
 :root{--bg:#fbfbfa;--fg:#1a1a18;--grid:#e7e5e0;--up:#0f9d58;--dn:#d93025;--mut:#6b6862;
       --ema:#e8710a;--ma:#1a73e8;--vwap:#8430ce;--panel:#fff}
 :root:not([data-theme=light]) @media (prefers-color-scheme:dark){}
 @media (prefers-color-scheme:dark){:root:not([data-theme=light]){
   --bg:#141413;--fg:#f0efea;--grid:#2c2b28;--panel:#1c1b19;--mut:#8a877f}}
 :root[data-theme=dark]{--bg:#141413;--fg:#f0efea;--grid:#2c2b28;--panel:#1c1b19;--mut:#8a877f}
 body{background:var(--bg);color:var(--fg);font:13px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:16px}
 h1{font-size:17px;margin:0 0 2px} .sub{color:var(--mut);margin-bottom:12px}
 .wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--grid);border-radius:8px;padding:10px}
 canvas{display:block}
 table{border-collapse:collapse;margin-top:14px;font-size:12.5px;width:100%%;max-width:760px}
 th,td{text-align:right;padding:5px 9px;border-bottom:1px solid var(--grid)}
 th:first-child,td:first-child{text-align:left}
 th{color:var(--mut);font-weight:600}
 .pos{color:var(--up)} .neg{color:var(--dn)}
 .key{display:flex;gap:14px;flex-wrap:wrap;color:var(--mut);margin:8px 0 0;font-size:12px}
 .key i{display:inline-block;width:16px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
</style>
<h1>%(SYM)s &middot; 30-second &middot; %(DAY)s</h1>
<div class="sub">Engine entries and exits, forward-only. Green triangle = buy, red = exit.</div>
<div class="wrap"><canvas id="c"></canvas></div>
<div class="key">
 <span><i style="background:var(--ema)"></i>EMA 8</span>
 <span><i style="background:var(--ma)"></i>MA 12</span>
 <span><i style="background:var(--vwap)"></i>VWAP</span>
 <span>shaded band = Heiken Ashi SuperTrend (10,3)</span>
</div>
<div id="tbl"></div>
<script>
const D=%(DATA)s;
const n=D.bars.length, CW=Math.max(1100,n*3.2), PH=380, MH=90, RH=80, GAP=26, L=58, R=14;
const H=PH+GAP+MH+GAP+RH+28;
const cv=document.getElementById('c'); const dpr=devicePixelRatio||1;
cv.width=CW*dpr; cv.height=H*dpr; cv.style.width=CW+'px'; cv.style.height=H+'px';
const x=cv.getContext('2d'); x.scale(dpr,dpr);
const css=k=>getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const lo=Math.min(...D.bars.map(b=>b.l)), hi=Math.max(...D.bars.map(b=>b.h));
const pad=(hi-lo)*0.08, LO=lo-pad, HI=hi+pad;
const px=i=>L+(i+0.5)*((CW-L-R)/n), py=p=>PH-((p-LO)/(HI-LO))*(PH-10)-5;
const bw=Math.max(1.6,(CW-L-R)/n*0.62);
x.fillStyle=css('--panel'); x.fillRect(0,0,CW,H);
// grid + price axis
x.strokeStyle=css('--grid'); x.fillStyle=css('--mut'); x.lineWidth=1; x.font='11px system-ui';
for(let k=0;k<=5;k++){const p=LO+(HI-LO)*k/5, y=py(p);
  x.beginPath();x.moveTo(L,y);x.lineTo(CW-R,y);x.stroke();
  x.fillText(p.toFixed(1),6,y+3);}
// supertrend band
x.globalAlpha=.13;
for(let i=1;i<n;i++){ if(D.stline[i]==null||D.stdir[i]==null) continue;
  x.fillStyle=D.stdir[i]===1?css('--up'):css('--dn');
  const y0=py(D.stline[i]), y1=D.stdir[i]===1?py(LO):py(HI);
  x.fillRect(px(i)-bw/2,Math.min(y0,y1),bw,Math.abs(y1-y0)); }
x.globalAlpha=1;
// candles
for(let i=0;i<n;i++){const b=D.bars[i], up=b.c>=b.o;
  x.strokeStyle=x.fillStyle=up?css('--up'):css('--dn');
  x.beginPath();x.moveTo(px(i),py(b.h));x.lineTo(px(i),py(b.l));x.stroke();
  const y=py(Math.max(b.o,b.c)),h2=Math.max(1,Math.abs(py(b.o)-py(b.c)));
  x.fillRect(px(i)-bw/2,y,bw,h2);}
// lines
const line=(arr,col,w)=>{x.strokeStyle=col;x.lineWidth=w;x.beginPath();let s=false;
  for(let i=0;i<n;i++){if(arr[i]==null){s=false;continue;}
    const X=px(i),Y=py(arr[i]); s?x.lineTo(X,Y):x.moveTo(X,Y); s=true;} x.stroke();x.lineWidth=1;};
line(D.vwap,css('--vwap'),1.6); line(D.ma12,css('--ma'),1.4); line(D.ema8,css('--ema'),1.6);
// trades
const at=t=>D.bars.findIndex(b=>b.t>=t);
x.font='bold 11px system-ui';
D.trades.forEach(t=>{const i=at(t.in_t), j=at(t.out_t); if(i<0)return;
  const yi=py(t.in), yj=j<0?yi:py(t.out);
  x.strokeStyle=css('--mut'); x.setLineDash([4,3]); x.beginPath();
  x.moveTo(px(i),yi); x.lineTo(px(j<0?n-1:j),yj); x.stroke(); x.setLineDash([]);
  x.fillStyle=css('--up'); x.beginPath();
  x.moveTo(px(i),yi+13);x.lineTo(px(i)-6,yi+24);x.lineTo(px(i)+6,yi+24);x.closePath();x.fill();
  if(j>=0){x.fillStyle=css('--dn'); x.beginPath();
    x.moveTo(px(j),yj-13);x.lineTo(px(j)-6,yj-24);x.lineTo(px(j)+6,yj-24);x.closePath();x.fill();
    x.fillStyle=t.net>=0?css('--up'):css('--dn');
    x.fillText((t.net>=0?'+':'')+t.net, px(j)+8, yj-16);}});
// MACD
const my=PH+GAP, mv=Math.max(...D.macd.filter(v=>v!=null).map(Math.abs),0.01);
const myy=v=>my+MH/2-(v/mv)*(MH/2-6);
x.strokeStyle=css('--grid');x.beginPath();x.moveTo(L,myy(0));x.lineTo(CW-R,myy(0));x.stroke();
for(let i=0;i<n;i++){if(D.macd[i]==null||D.macdsig[i]==null)continue;
  const h2=D.macd[i]-D.macdsig[i];
  x.fillStyle=h2>=0?css('--up'):css('--dn'); x.globalAlpha=.5;
  x.fillRect(px(i)-bw/2,Math.min(myy(0),myy(h2)),bw,Math.abs(myy(h2)-myy(0)));x.globalAlpha=1;}
const line2=(arr,col,f)=>{x.strokeStyle=col;x.beginPath();let s=false;
  for(let i=0;i<n;i++){if(arr[i]==null){s=false;continue;}const X=px(i),Y=f(arr[i]);
    s?x.lineTo(X,Y):x.moveTo(X,Y);s=true;}x.stroke();};
line2(D.macd,css('--ma'),myy); line2(D.macdsig,css('--dn'),myy);
x.fillStyle=css('--mut');x.font='11px system-ui';x.fillText('MACD 12-26-9',6,my+12);
// RSI
const ry=my+MH+GAP, ryy=v=>ry+RH-(v/100)*RH;
x.strokeStyle=css('--grid');
[30,50,70].forEach(v=>{x.beginPath();x.moveTo(L,ryy(v));x.lineTo(CW-R,ryy(v));x.stroke();
  x.fillStyle=css('--mut');x.fillText(v,6,ryy(v)+3);});
line2(D.rsi,css('--vwap'),ryy);
x.fillStyle=css('--mut');x.fillText('RSI 14',6,ry+12);
// time axis
x.fillStyle=css('--mut');
for(let i=0;i<n;i+=Math.ceil(n/12)) x.fillText(D.bars[i].t.slice(0,5),px(i)-14,H-8);
// table
let net=0, rows=D.trades.map(t=>{net+=t.net;
  return `<tr><td>${t.in_t}</td><td>${t.in.toFixed(2)}</td><td>${t.out_t}</td><td>${t.out.toFixed(2)}</td>
  <td>${((t.out/t.in-1)*100).toFixed(2)}%%</td><td>${t.qty}</td>
  <td class="${t.net>=0?'pos':'neg'}">${t.net>=0?'+':''}${t.net.toLocaleString('en-IN')}</td><td>${t.why}</td></tr>`}).join('');
document.getElementById('tbl').innerHTML = D.trades.length
 ? `<table><thead><tr><th>entry</th><th>price</th><th>exit</th><th>price</th><th>move</th><th>qty</th><th>P&L</th><th>why</th></tr></thead>
    <tbody>${rows}</tbody><tfoot><tr><th colspan="6">net</th>
    <th class="${net>=0?'pos':'neg'}">${net>=0?'+':''}${net.toLocaleString('en-IN')}</th><th></th></tr></tfoot></table>`
 : '<p style="color:var(--mut)">No trades taken in this stock on this day.</p>';
</script>"""

OUT.write_text(HTML % {"SYM": SYM, "DAY": DAY, "DATA": json.dumps(data)}, encoding="utf-8")
print(f"{SYM} {DAY}: {len(bars)} bars, {len(trades)} trades -> {OUT}")
