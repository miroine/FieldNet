import json, pytest
from network.case_manager import *
from network.case_share import *
from ui.graph_contract import to_builtin


def _demo():
    from network.examples import demo_field_case
    return demo_field_case()


def _lib():
    n, e = _demo(); lib = CaseLibrary(); lib.add(new_case('Base', n, e)); return lib


def test_duplicate_and_diff():
    lib = _lib(); a = lib.active; b = lib.duplicate(a, 'Plus', keep_results=False)
    assert b['parent_id'] == a and b['id'] != a and lib.active == b['id']
    w = next(n for n in b['nodes'] if n['kind'] == 'well'); w.setdefault('params', {})['water_cut'] = 0.77
    d = case_diff(lib.get(a), b)
    assert d['change_count'] >= 1 and any(r['Field'].endswith('water_cut') and r['B'] == 0.77 for r in d['rows'])
    assert case_diff(lib.get(a), lib.get(a))['change_count'] == 0


def test_duplicate_does_not_alias():
    lib = _lib(); a = lib.active; b = lib.duplicate(a)
    lib.get(b['id'])['nodes'][0]['name'] = 'X'; assert lib.get(a)['nodes'][0]['name'] != 'X'


def test_unique_names_rename_delete():
    lib = _lib(); a = lib.active; b = lib.duplicate(a, 'Base'); assert b['name'] == 'Base (2)'
    with pytest.raises(ValueError): lib.rename(b['id'], 'Base')
    lib.rename(b['id'], 'Alt'); lib.delete(b['id']); assert lib.active == a and len(lib) == 1


def test_save_overwrites_and_keeps_results_if_unchanged():
    lib = _lib(); a = lib.active; lib.get(a)['solve'] = {'liquid_m3d': 5.0}
    n, e = _demo(); lib.save(a, n, e, 'norwegian_si'); assert lib.get(a)['solve'] == {'liquid_m3d': 5.0}
    n[0]['name'] = 'changed'; lib.save(a, n, e, 'norwegian_si'); assert lib.get(a)['solve'] is None


def test_copy_into():
    lib = _lib(); a = lib.active; b = lib.duplicate(a, 'B')['id']; lib.get(b)['nodes'][0]['name'] = 'Z'
    lib.copy_into(a, b, ('model',)); assert lib.get(b)['nodes'][0]['name'] == lib.get(a)['nodes'][0]['name']


def test_json_zip_roundtrip_and_tamper():
    lib = _lib(); lib.duplicate(lib.active, 'B')
    assert [c['name'] for c in import_json(export_json(lib)).cases.values()] == ['Base', 'B']
    z = export_zip(lib); l2 = import_zip(z); assert len(l2) == 2 and l2.active == lib.active
    import zipfile, io
    buf = io.BytesIO(); zin = zipfile.ZipFile(io.BytesIO(z))
    with zipfile.ZipFile(buf, 'w') as zo:
        for nme in zin.namelist():
            b = zin.read(nme); zo.writestr(nme, b.replace(b'Base', b'Hack') if nme.endswith('case.json') else b)
    with pytest.raises(ValueError, match='checksum'): import_zip(buf.getvalue())


def test_invalid_file_rejected():
    with pytest.raises(ValueError): import_json(b'{"x":1}')


def test_encrypt_roundtrip_wrong_password_tamper():
    lib = _lib(); blob = share_package(lib, None, 'Secret#2026')
    assert b'Base' not in blob and open_package(blob, 'Secret#2026').cases.keys() == lib.cases.keys()
    with pytest.raises(ShareError, match='Wrong password'): open_package(blob, 'Wrong#2026x')
    bad = bytearray(blob); bad[-5] ^= 1
    with pytest.raises(ShareError): open_package(bytes(bad), 'Secret#2026')
    with pytest.raises(ShareError): share_package(lib, None, 'short')
    with pytest.raises(ShareError): open_package(b'garbage', 'Secret#2026')


def test_link_roundtrip():
    lib = _lib(); blob = share_package(lib, None, 'Secret#2026'); link = to_link(blob)
    assert link.startswith('fieldnet-share:') and from_link('  ' + link[:30] + '\n' + link[30:]) == blob
    with pytest.raises(ShareError): from_link('fieldnet-share:@@@')


def test_compare_table_and_profiles():
    lib = _lib(); a = lib.get(lib.active)
    b = lib.duplicate(a['id'], 'B')
    a['solve'] = {'liquid_m3d': 100.0, 'violations': 0}; b['solve'] = {'liquid_m3d': 130.0, 'violations': 1}
    rows = compare_table([a, b]); r = next(x for x in rows if x['Metric'] == 'Liquid [m³/d]')
    assert r['Base'] == 100.0 and r['B'] == 130.0 and r['Δ B'] == 30.0
    fc = {'field': [{'Date': '2027-01-01', 'Day': 0, 'Oil [m3/d]': 10, 'Cumulative oil [Sm3]': 0}, {'Date': '2027-02-01', 'Day': 31, 'Oil [m3/d]': 8, 'Cumulative oil [Sm3]': 300}], 'recovery': []}
    c = new_case('C', a['nodes'], a['edges'], forecast=fc); assert c['forecast_kpis']['peak_oil_m3d'] == 10
    assert profile_series([c])['C'][1] == [10, 8]
