"""Live-only layers: TAK online map source XML, never any downloaded data.

Used for layers whose licence forbids keeping them (King County's EagleView
aerials) and for public tile services handy when online.
"""
import os
from xml.sax.saxutils import escape

from . import core


def live_xml(src, ctx, inputs, outdir):
    o = src.options
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<customMapSource>\n'
        f"  <name>{escape(o['name'])}</name>\n"
        f"  <minZoom>{o.get('min_zoom', 0)}</minZoom>\n"
        f"  <maxZoom>{o['max_zoom']}</maxZoom>\n"
        f"  <tileType>{o.get('tile_type', 'png')}</tileType>\n"
        "  <tileUpdate>None</tileUpdate>\n"
        f"  <url>{escape(o['template'])}</url>\n"
        "  <backgroundColor>#000000</backgroundColor>\n"
        "</customMapSource>\n"
    )
    out = os.path.join(outdir, f"{src.key}.xml")
    with open(out, "w") as f:
        f.write(xml)
    return [out]


core.CONVERTERS["live-xml"] = live_xml
