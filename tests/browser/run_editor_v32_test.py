"""Real-browser test of the v32 editor features: grouped palette from Python, joint + inline equipment nodes,
tank<->tank communication links, node result labels and SVG export.   Run: python tests/browser/run_editor_v32_test.py"""
import sys, threading, http.server, functools, shutil, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from network.examples import demo_field_case
from network.features import palette
from network.equipment import convert_edge_equipment_to_nodes
D = tempfile.mkdtemp()
shutil.copy(ROOT / 'ui/fieldnet_canvas/build/index.html', Path(D) / 'editor.html'); shutil.copy(Path(__file__).parent / 'host.html', Path(D) / 'host.html')
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=D))
PORT = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = True
def check(c, msg):
    global ok; print(('PASS ' if c else 'FAIL ') + msg); ok &= bool(c)
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(accept_downloads=True, viewport={'width': 1200, 'height': 700}); pg = ctx.new_page()
    errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/host.html'); pg.wait_for_function('window.ready')
    n, e = demo_field_case()
    n, e = convert_edge_equipment_to_nodes(n, [dict(x, kind='choke', length_m=0.0) if x['id'] == e[0]['id'] else x for x in e], {e[0]['id']})
    n.append({'id': 'J1', 'kind': 'joint', 'name': 'Tie-in', 'pressure_bar': None, 'x': 600, 'y': 500, 'params': {}})
    pal = palette()
    args = {'nodes': n, 'edges': e, 'pressures': {}, 'rates': {}, 'status': 'UNSOLVED', 'status_message': '', 'height': 700, 'palette': pal, 'labels': {'P1': '1,234 m³/d · WC 20%'}}
    pg.evaluate('a=>window.renderArgs(a)', args); pg.wait_for_timeout(300)
    fr = pg.frames[1]; fr.evaluate("document.getElementById('fit').click()")
    groups = fr.evaluate("[...document.querySelectorAll('.pal-group')].map(x=>x.textContent)")
    check(groups[:4] == ['Reservoir', 'Wells', 'Connections', 'Equipment'], f'palette grouped from Python: {groups}')
    check(fr.evaluate("document.querySelectorAll('#pal button').length") == sum(len(g['items']) for g in pal), 'every catalogue item has a button')
    check(fr.evaluate("document.querySelectorAll('.node.joint').length") == 1 and fr.evaluate("document.querySelectorAll('.node.equip').length") >= 1, 'joint and inline equipment render with their own shapes')
    check('1,234' in fr.evaluate("document.querySelector('.node[data-id=\"P1\"] .result').textContent"), 'node result label from Python shown')
    check('producer' in fr.evaluate("document.querySelector('.node[data-id=\"P1\"] .tag')?.textContent||''"), 'well shows role · phase tag')
    # add a gas producer from the palette: carries role/phase params
    fr.evaluate("[...document.querySelectorAll('#pal button')].find(b=>/(^|\s)Well$/.test(b.textContent.trim())).click()"); pg.wait_for_timeout(100); fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    last = pg.evaluate('window.msgs')[-1]; gp = last['nodes'][-1]
    check(gp['kind'] == 'well' and gp['params'].get('phase', 'oil') == 'oil', 'the single Well palette item creates an oil producer (role / phase are set in the settings)')
    fr.evaluate("[...document.querySelectorAll('#pal button')].find(b=>b.textContent.includes('Choke')).click()"); pg.wait_for_timeout(100); fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    last = pg.evaluate('window.msgs')[-1]; check(last['nodes'][-1]['kind'] == 'choke' and last['nodes'][-1]['params']['cv'] == 80, 'choke is added as a node')
    fr.evaluate("[...document.querySelectorAll('.pal-group')].find(g=>g.textContent==='Wells').click()"); pg.wait_for_timeout(50)
    check(fr.evaluate("[...document.querySelectorAll('.pal-group')].find(g=>g.textContent==='Wells').nextSibling.style.display")=='none', 'palette groups collapse')
    # tank <-> tank communication
    n2 = [dict(x) for x in n]; n2.append({'id': 'T9', 'kind': 'reservoir', 'name': 'Tank 9', 'pressure_bar': None, 'x': 60, 'y': 560, 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 5e6, 'reservoir_pressure_bar': 230}})
    pg.evaluate('a=>window.renderArgs(a)', {**args, 'nodes': n2}); pg.wait_for_timeout(200); fr.evaluate("document.getElementById('fit').click()")
    tid = [x['id'] for x in n2 if x['kind'] == 'reservoir'][0]
    port = lambda nid, w: fr.evaluate(f"(()=>{{const r=document.querySelector('.node[data-id=\"{nid}\"] .p{w}').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]}})()")
    n0 = pg.evaluate('window.msgs.length'); a = port(tid, 'out'); t = port('T9', 'in'); pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(*t, steps=8)
    check('communicate' in fr.evaluate("document.getElementById('status').textContent"), 'hover explains tank communication')
    pg.mouse.up(); pg.wait_for_timeout(150); fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    last = pg.evaluate('window.msgs')[-1]; src = [x for x in last['nodes'] if x['id'] == tid][0]
    check(pg.evaluate('window.msgs.length') == n0 + 1 and src['params']['communication'][0]['to'] == 'T9' and len(last['edges']) == len(e), 'tank→tank drop creates a communication link (no pipe)')
    check(fr.evaluate("document.querySelectorAll('path.comm').length") == 1, 'communication link drawn')
    fr.evaluate("document.querySelector('path.hit[data-edge^=\"comm:\"]').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}))"); pg.wait_for_timeout(100)
    check(pg.evaluate('window.msgs')[-1]['selected'].startswith('comm:'), 'communication link can be selected')
    fr.evaluate("document.getElementById('del').click()"); pg.wait_for_timeout(100); fr.evaluate("document.getElementById('apply').click()"); pg.wait_for_timeout(80)
    check('communication' not in [x for x in pg.evaluate('window.msgs')[-1]['nodes'] if x['id'] == tid][0]['params'], 'communication link can be deleted')
    # SVG export
    with pg.expect_download(timeout=5000) as dl:
        fr.evaluate("document.getElementById('svgx').click()")
    path = dl.value.path(); svg = Path(path).read_text()
    check(svg.startswith('<svg') and 'Tie-in' in svg and 'CHOKE' in svg and svg.count('<path') >= len(e), f'SVG export downloads a drawing ({len(svg)} bytes)')
    check(not errs, 'no JS errors: ' + '; '.join(errs))
    b.close()
srv.shutdown(); print('ALL PASS' if ok else 'FAILURES'); sys.exit(0 if ok else 1)
