import xml.etree.ElementTree as ET
from network.examples import demo_field_case
from network.equipment import convert_edge_equipment_to_nodes
from ui.svg_export import network_svg


def test_svg_is_valid_xml_and_contains_every_element():
    n, e = demo_field_case(); n, e = convert_edge_equipment_to_nodes(n, [dict(x, kind='choke') if x is e[0] else x for x in e], {e[0]['id']})
    n.append({'id': 'J', 'kind': 'joint', 'name': 'Tie-in & <J>', 'x': 10, 'y': 10, 'params': {}})
    svg = network_svg(n, e, labels={n[0]['id']: '1,000 m³/d'}, rates={e[-1]['id']: 12.0}); root = ET.fromstring(svg)
    ns = '{http://www.w3.org/2000/svg}'
    assert root.tag == ns + 'svg' and len(root.findall(f'.//{ns}path')) >= len(e) and 'Tie-in &amp; &lt;J&gt;' in svg and 'CHOKE' in svg and '1,000 m³/d' in svg
def test_empty_network():
    assert ET.fromstring(network_svg([], [])).tag.endswith('svg')
