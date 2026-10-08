"""Real-browser test: a second tank dropped on a well makes it commingled (one line per tank with its share); a line can be removed.
Run: python tests/browser/run_multi_tank_test.py"""
import sys, threading, http.server, functools, shutil, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from network.examples import demo_field_case
from network.features import palette
D = tempfile.mkdtemp()
shutil.copy(ROOT / 'ui/fieldnet_canvas/build/index.html', Path(D) / 'editor.html'); shutil.copy(Path(__file__).parent / 'host.html', Path(D) / 'host.html')
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=D))
PORT = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = True
def check(c, msg):
    global ok; print(('PASS ' if c else 'FAIL ') + msg); ok &= bool(c)
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(viewport={'width': 1300, 'height': 800}); pg = ctx.new_page()
    errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/host.html'); pg.wait_for_function('window.ready')
    n, e = demo_field_case(); t1 = next(x for x in n if x['kind'] == 'reservoir'); w = next(x for x in n if x['kind'] == 'well')
    n.append({'id': 'T2', 'kind': 'reservoir', 'name': 'Tank 2', 'pressure_bar': None, 'x': t1['x'], 'y': t1['y'] + 170, 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 5e6, 'reservoir_pressure_bar': 230}})
    args = {'nodes': n, 'edges': e, 'pressures': {}, 'rates': {}, 'status': 'UNSOLVED', 'status_message': '', 'height': 800, 'palette': palette(), 'labels': {}}
    pg.evaluate('a=>window.renderArgs(a)', args); pg.wait_for_timeout(300)
    fr = pg.frames[1]; fr.evaluate("document.getElementById('fit').click()")
    port = lambda nid, k: fr.evaluate(f"(()=>{{const r=document.querySelector('.node[data-id=\"{nid}\"] .p{k}').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]}})()")
    check(fr.evaluate("document.querySelectorAll('path.drain').length") >= 1 and fr.evaluate("document.querySelectorAll('path.drain.multi').length") == 0, 'single-tank wells show a plain drainage line')
    a = port('T2', 'out'); t = port(w['id'], 'in'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(*t, steps=8)
    check('Already' not in fr.evaluate("document.getElementById('status').textContent"), 'a second tank can be dropped on a well that already has one')
    pg.mouse.up(); pg.wait_for_timeout(150); fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    last = pg.evaluate('window.msgs')[-1]; ww = next(x for x in last['nodes'] if x['id'] == w['id'])['params']
    check({a['tank_id'] for a in ww.get('reservoir_alloc', [])} == {t1['id'], 'T2'}, 'the well now drains both tanks')
    check(abs(sum(a['share'] for a in ww['reservoir_alloc']) - 100) < 0.5, 'shares add up to 100 %')
    check(fr.evaluate("document.querySelectorAll('path.drain.multi').length") == 2 and fr.evaluate("document.querySelectorAll('.drainlab').length") == 2, 'two share-labelled lines are drawn')
    check('2 tanks' in fr.evaluate(f"document.querySelector('.node[data-id=\"{w['id']}\"] .tag').textContent"), 'the well is tagged "2 tanks"')
    fr.evaluate("document.querySelector('path.hit[data-edge$=\">T2\"]').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}))"); pg.wait_for_timeout(100)
    fr.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',bubbles:true}))"); pg.wait_for_timeout(100)
    fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    ww = next(x for x in pg.evaluate('window.msgs')[-1]['nodes'] if x['id'] == w['id'])['params']
    check('reservoir_alloc' not in ww and ww['reservoir_id'] == t1['id'], 'removing one line leaves a normal single-tank well')
    check(not errs, 'no JS errors: ' + '; '.join(errs))
    b.close()
print('ALL PASS' if ok else 'FAILURES'); sys.exit(0 if ok else 1)
