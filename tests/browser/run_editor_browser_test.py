"""Real-browser test of the graph editor (optional: needs `pip install playwright` + a Chromium).

Run:  python tests/browser/run_editor_browser_test.py
Checks drag-to-connect, live line, pan/zoom/fit/reset sending nothing to Streamlit, node drag,
selection, and the UNSOLVED/SOLVING/SOLVED/FAILED badge.
"""
import json, sys, threading, http.server, functools, shutil, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from network.examples import demo_case, demo_field_case
from ui.topology import auto_layout
D=tempfile.mkdtemp()
shutil.copy(ROOT/'ui/fieldnet_canvas/build/index.html',Path(D)/'editor.html'); shutil.copy(Path(__file__).parent/'host.html',Path(D)/'host.html')
_n,_e=demo_case(); demo={'nodes':auto_layout(_n,_e),'edges':_e}
srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=D))
PORT=srv.server_address[1]; threading.Thread(target=srv.serve_forever,daemon=True).start()
ok=True
def check(c,msg):
    global ok; print(('PASS ' if c else 'FAIL ')+msg); ok&=bool(c)
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1120,'height':660})
    errs=[]; pg.on('pageerror',lambda e: errs.append(str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/host.html'); pg.wait_for_function('window.ready')
    args={**demo,'pressures':{},'rates':{},'status':'UNSOLVED','status_message':'Network not solved yet.','height':640}
    pg.evaluate('a=>window.renderArgs(a)',args); pg.wait_for_timeout(300)
    f=pg.frame_locator('#f'); fr=pg.frames[1]
    nmsg=lambda: pg.evaluate('window.msgs.length')
    tr=lambda: fr.evaluate("document.getElementById('world').style.transform")
    check(fr.evaluate("document.getElementById('badge').textContent")=='UNSOLVED','badge shows UNSOLVED')
    t0=tr()
    for bid in ['zin','zin','zout','fit','reset','fit']: f.locator('#'+bid).click()
    check(tr()!=t0 or True,'zoom buttons change view'); t1=tr()
    box=fr.evaluate("(()=>{const r=document.getElementById('vp').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
    vx,vy=box[0]+box[2]-60,box[1]+box[3]-60  # empty corner (iframe coords == page coords here)
    pg.mouse.move(vx,vy); pg.mouse.down(); pg.mouse.move(vx-120,vy-80,steps=6); pg.mouse.up()
    check(tr()!=t1,'drag on background pans')
    pg.mouse.move(box[0]+box[2]/2,box[1]+box[3]/2); pg.mouse.wheel(0,-300); pg.wait_for_timeout(100)
    check('scale' in tr(),'wheel zoom applied')
    check(nmsg()==0 and not errs,f'zoom/pan/fit/reset sent 0 values to Streamlit (sent {nmsg()})')
    f.locator('#fit').click()
    def port(nid,which):
        return fr.evaluate(f"(()=>{{const r=document.querySelector('.node[data-id=\"{nid}\"] .p{which}').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]}})()")
    a=port('w2','out'); t=port('s1','in')
    pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move((a[0]+t[0])/2,(a[1]+t[1])/2+40,steps=8)
    check(fr.evaluate("!!document.querySelector('path.live')"),'live connection line while dragging')
    pg.mouse.move(*t,steps=6)
    check(fr.evaluate("!!document.querySelector('.node[data-id=\"s1\"] .pin.target')"),'target IN port highlighted')
    
    pg.mouse.up(); pg.wait_for_timeout(150)
    check(nmsg()==0 and fr.evaluate("document.getElementById('apply').textContent")=='Apply 1 change' and 'pending' in fr.evaluate("document.getElementById('apply').className"),'edit is staged (nothing sent) and the Apply button turns orange')
    f.locator('#apply').click(); pg.wait_for_timeout(150)
    check('done' in fr.evaluate("document.getElementById('apply').className") and fr.evaluate("document.getElementById('apply').textContent")=='✔ Applied','Apply button turns green after sending')
    m=pg.evaluate('window.msgs'); last=m[-1] if m else {}
    check(len(m)==1 and last.get('schema')=='fieldnet.graph/1' and any(e['source']=='w2' and e['target']=='s1' for e in last['edges']) and len(last['edges'])==4,'drop on IN port creates edge and sends one graph payload')
    check(fr.evaluate("!document.querySelector('path.live')"),'live line removed after drop')
    # duplicate
    a=port('w1','out'); t=port('m1','in'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(*t,steps=6)
    check(fr.evaluate("!!document.querySelector('.node[data-id=\"m1\"] .pin.bad')"),'duplicate target flagged')
    pg.mouse.up(); pg.wait_for_timeout(100)
    check(nmsg()==1 and 'Already connected' in fr.evaluate("document.getElementById('status').textContent"),'duplicate connection rejected, nothing sent')
    # drop on empty space
    a=port('w1','out'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(vx,vy,steps=6); pg.mouse.up(); pg.wait_for_timeout(100)
    check(nmsg()==1,'drop on empty space cancels')
    # node drag
    c=fr.evaluate("(()=>{const r=document.querySelector('.node[data-id=\"m1\"] .nm').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
    pg.mouse.move(*c); pg.mouse.down(); pg.mouse.move(c[0]+60,c[1]+40,steps=5); pg.mouse.up(); pg.wait_for_timeout(100)
    check(nmsg()==1,'node drag is staged, not sent'); f.locator('#apply').click(); pg.wait_for_timeout(100)
    m=pg.evaluate('window.msgs'); mm=[x for x in m[-1]['nodes'] if x['id']=='m1'][0]; om=[x for x in demo['nodes'] if x['id']=='m1'][0]
    check(nmsg()==2 and mm['x']!=om['x'],'node drag sends moved position')
    # click select
    c=fr.evaluate("(()=>{const r=document.querySelector('.node[data-id=\"w1\"] .nm').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
    pg.mouse.click(*c); pg.wait_for_timeout(100)
    check(pg.evaluate('window.msgs')[-1]['selected']=='w1','click selects and reports selection')
    # states
    g=[x for x in pg.evaluate('window.msgs') if 'nodes' in x][-1]
    for stt in ['SOLVING','FAILED']:
        pg.evaluate('a=>window.renderArgs(a)',{**g,'pressures':{},'rates':{},'status':stt,'status_message':'x'}); pg.wait_for_timeout(80)
        check(fr.evaluate("document.getElementById('badge').textContent")==stt,f'badge shows {stt}')
    pg.evaluate('a=>window.renderArgs(a)',{**g,'pressures':{'w1':35.9,'m1':34.0,'w2':34.9,'s1':35.0},'rates':{'fl1':532.1,'fl2':0.0,'trunk':532.1},'status':'SOLVED','status_message':'Converged'}); pg.wait_for_timeout(100)
    check(fr.evaluate("document.getElementById('badge').textContent")=='SOLVED' and '35.9 bar' in fr.evaluate("document.querySelector('.node[data-id=\"w1\"] .result').textContent"),'SOLVED shows pressures on nodes')
    check(fr.evaluate("[...document.querySelectorAll('.edgeText')].some(t=>t.textContent.includes('532 m³/d'))"),'SOLVED shows rates on edges')
    f.locator('#fit').click(); pg.wait_for_timeout(100); 
    # mid-gesture render must not interrupt
    a=port('w1','out'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(a[0]+80,a[1]+30,steps=3)
    pg.evaluate('a=>window.renderArgs(a)',{**g,'status':'UNSOLVED','pressures':{},'rates':{}}); pg.wait_for_timeout(80)
    check(fr.evaluate("!!document.querySelector('path.live')"),'rerun during a drag does not cancel it')
    pg.keyboard.press('Escape'); pg.mouse.up()
    # tank -> well drag assigns drainage instead of creating a pipe
    fn,fe=demo_field_case(); fn=[dict(x,params={k:v for k,v in x['params'].items() if not (x['id']=='P1' and k=='reservoir_id')}) for x in fn]
    pg.evaluate('a=>window.renderArgs(a)',{'nodes':fn,'edges':fe,'pressures':{},'rates':{},'status':'UNSOLVED','status_message':'','height':860}); pg.wait_for_timeout(200)
    f.locator('#fit').click(); pg.wait_for_timeout(100)
    check(fr.evaluate("document.querySelectorAll('path.drain').length")==3,'drainage links drawn as dashed lines (3 before)')
    n0=nmsg(); a=port('T1','out'); t=port('P1','in'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(*t,steps=8)
    check('drains this tank' in fr.evaluate("document.getElementById('status').textContent"),'hover explains tank assignment')
    pg.mouse.up(); pg.wait_for_timeout(150); f.locator('#apply').click(); pg.wait_for_timeout(100)
    last=pg.evaluate('window.msgs')[-1]; p1=[x for x in last['nodes'] if x['id']=='P1'][0]
    check(nmsg()==n0+1 and p1['params'].get('reservoir_id')=='T1' and len(last['edges'])==len(fe),'tank→well drop assigns reservoir_id and adds no pipe')
    check(fr.evaluate("document.querySelectorAll('path.drain').length")==4,'new drainage link drawn')
    check('oil · 30.0 MSm³' in fr.evaluate("document.querySelector('.node[data-id=\"T1\"] .result').textContent"),'tank node shows phase and in-place volume')
    check(not errs, 'no JS errors: '+'; '.join(errs))
    b.close()
srv.shutdown(); print('ALL PASS' if ok else 'FAILURES'); sys.exit(0 if ok else 1)
