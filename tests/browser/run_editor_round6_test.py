"""Real-browser test (Playwright + Chromium) of the round-6 canvas behaviour: staged edits + Apply, rebase when Python re-renders,
epoch reset (blank page), mask toggle, tank label with gas cap.   Run:  python tests/browser/run_editor_round6_test.py"""
import copy, sys, threading, http.server, functools, shutil, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from network.examples import demo_field_case
from ui.topology import auto_layout
D = tempfile.mkdtemp()
shutil.copy(ROOT / 'ui/fieldnet_canvas/build/index.html', Path(D) / 'editor.html'); shutil.copy(Path(__file__).parent / 'host.html', Path(D) / 'host.html')
_n, _e = demo_field_case(); nodes = auto_layout(_n, _e); edges = _e
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=D))
PORT = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = True
def check(c, msg):
    global ok; print(('PASS ' if c else 'FAIL ') + msg); ok &= bool(c)
base = {'pressures': {}, 'rates': {}, 'status': 'UNSOLVED', 'status_message': '', 'height': 640, 'epoch': 0}
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={'width': 1120, 'height': 660}); errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/host.html'); pg.wait_for_function('window.ready')
    pg.evaluate('a=>window.renderArgs(a)', {**base, 'nodes': nodes, 'edges': edges}); pg.wait_for_timeout(300)
    f = pg.frame_locator('#f'); fr = pg.frames[1]; nmsg = lambda: pg.evaluate('window.msgs.length')
    txt = lambda i: fr.evaluate(f"document.getElementById('{i}').textContent")
    pid = nodes[[n['kind'] for n in nodes].index('well')]['id']
    # mask: stage, render, apply
    fr.evaluate(f"selected='{pid}';render()"); f.locator('#mask').click(); pg.wait_for_timeout(100)
    check(fr.evaluate(f"document.querySelector('.node[data-id=\"{pid}\"]').classList.contains('masked')"), 'Mask greys out the selected well')
    check(nmsg() == 0 and txt('apply') == 'Apply 1 change', 'mask is staged until Apply')
    f.locator('#apply').click(); pg.wait_for_timeout(100)
    last = pg.evaluate('window.msgs')[-1]; w = [x for x in last['nodes'] if x['id'] == pid][0]
    check(w['params'].get('masked') is True, 'Apply sends params.masked = true')
    f.locator('#mask').click(); pg.wait_for_timeout(50)
    check(not fr.evaluate(f"document.querySelector('.node[data-id=\"{pid}\"]').classList.contains('masked')") and 'masked' not in [x for x in fr.evaluate('state.nodes') if x['id'] == pid][0]['params'], 'second click unmasks')
    # staged edits survive a Python re-render and take Python parameters (rebase)
    fr.evaluate("(()=>{const n=state.nodes[0];n.x=n.x+50;send()})()"); pg.wait_for_timeout(50)
    n2 = copy.deepcopy(nodes); n2[-1]['params']['pi_m3d_bar'] = 77.0; n2[-1]['name'] = 'RENAMED'
    pg.evaluate('a=>window.renderArgs(a)', {**base, 'nodes': n2, 'edges': edges}); pg.wait_for_timeout(100)
    st = fr.evaluate('state.nodes'); x0 = [x for x in st if x['id'] == nodes[0]['id']][0]
    check(x0['x'] == nodes[0]['x'] + 50, 'a staged move is kept when Python re-renders')
    s1 = [x for x in st if x['id'] == n2[-1]['id']][0]
    check(s1['name'] == 'RENAMED' and (s1['params'].get('pi_m3d_bar') == 77.0 or n2[-1]['kind'] != 'well'), 'parameters edited in the panel are taken over (rebase)')
    check(txt('apply').startswith('Apply'), 'still pending after the re-render')
    # epoch change drops staged edits (blank page / template)
    pg.evaluate('a=>window.renderArgs(a)', {**base, 'nodes': [], 'edges': [], 'epoch': 1}); pg.wait_for_timeout(100)
    check(fr.evaluate('state.nodes.length') == 0 and txt('apply') == 'No pending changes', 'new epoch (blank page) replaces the canvas and clears staged edits')
    # palette add on blank page is staged; tank label with gas cap
    fr.evaluate("document.querySelectorAll('#pal button')[0].click()"); pg.wait_for_timeout(100)
    check(fr.evaluate('state.nodes.length') == 1 and txt('apply') == 'Apply 1 change' and nmsg() == 1, 'adding from the palette is staged (nothing new sent)')
    tank = {'id': 'TK', 'kind': 'reservoir', 'name': 'Tank A', 'pressure_bar': None, 'x': 60, 'y': 60, 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 30e6, 'reservoir_pressure_bar': 300, 'gas_cap_m': 0.5}}
    pg.evaluate('a=>window.renderArgs(a)', {**base, 'nodes': [tank], 'edges': [], 'epoch': 2}); pg.wait_for_timeout(100)
    check('oil+cap' in fr.evaluate("document.querySelector('.node[data-id=\"TK\"] .result').textContent") and fr.evaluate("document.querySelector('.node[data-id=\"TK\"] .kind').textContent") == 'tank', 'tank shows "tank" and the oil+cap fluid')
    check(not errs, 'no JS errors: ' + '; '.join(errs))
    b.close()
srv.shutdown(); print('ALL PASS' if ok else 'FAILURES'); sys.exit(0 if ok else 1)
