"""Writing a finished vector layer in the TAK-ready format for its type.

KMZ (LIBKML) for points and lines, unless the layer is too big for TAK
clients to handle smoothly; GeoPackage for large polygon layers and
anything over the feature limit. See core.FORMATS (set by the iTAK check).
"""
import html
import os
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET

from . import core

# TAK clients slow down on KMZ files with more than about this many features.
KMZ_MAX_FEATURES = 20000

# Icons TAK clients recognise from Google's KML icon set (iTAK showed the
# camera as a coloured camera symbol in the owner's test).
ICONS = {name: f"http://maps.google.com/mapfiles/kml/shapes/{name}.png"
         for name in ("camera", "hospitals", "firedept", "police", "airports", "hiker", "target", "info",
                      "placemark_circle", "triangle")}
HSTORE = re.compile(r'"([^"]+)"=>"([^"]*)"')


KML = "http://www.opengis.net/kml/2.2"


def _k(tag):
    return f"{{{KML}}}{tag}"


def _style(geometry, icon):
    style = ET.Element(_k("Style"), id="style")
    if geometry == "points":
        icon_style = ET.SubElement(style, _k("IconStyle"))
        ET.SubElement(icon_style, _k("scale")).text = "1.0"
        ET.SubElement(ET.SubElement(icon_style, _k("Icon")), _k("href")).text = ICONS[icon or "placemark_circle"]
        ET.SubElement(ET.SubElement(style, _k("LabelStyle")), _k("scale")).text = "1.0"
    else:
        line = ET.SubElement(style, _k("LineStyle"))
        ET.SubElement(line, _k("color")).text = "ff0080ff"
        ET.SubElement(line, _k("width")).text = "3"
        ET.SubElement(ET.SubElement(style, _k("PolyStyle")), _k("color")).text = "400080ff"
        ET.SubElement(ET.SubElement(style, _k("LabelStyle")), _k("scale")).text = "0.8"
    return style


def _details(text):
    """Raw OSM tag dumps become an HTML table; anything else is kept as text."""
    if text and "=>" in text:
        rows = "".join(f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(v)}</td></tr>"
                       for k, v in sorted(HSTORE.findall(text)))
        return f"<table>{rows}</table>"
    # Plain third-party text (FCC addresses, RepeaterBook locations): TAK
    # clients render descriptions as HTML, so escape it.
    return html.escape(text) if text else text


def tak_style(kml_text, geometry, icon=None, fallback_name="Feature"):
    """Rewrite LIBKML output so TAK clients show labels and icons.

    Parsed as XML and edited per Placemark (its own <name>/<description>
    children only): values are crowdsourced, and markup inside them must
    never become a label.
    """
    ET.register_namespace("", KML)
    root = ET.fromstring(kml_text.encode("utf-8"))
    document = root.find(_k("Document"))
    document.insert(0, _style(geometry, icon))
    for placemark in root.iter(_k("Placemark")):
        for extended in placemark.findall(_k("ExtendedData")):
            placemark.remove(extended)
        name = placemark.find(_k("name"))
        if name is None:
            name = ET.Element(_k("name"))
            placemark.insert(0, name)
        if not (name.text or "").strip():
            name.text = fallback_name
        description = placemark.find(_k("description"))
        if description is not None:
            description.text = _details(description.text)
        style_url = ET.Element(_k("styleUrl"))
        style_url.text = "#style"
        placemark.insert(list(placemark).index(name) + 1, style_url)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def count(path):
    from osgeo import ogr
    ds = ogr.Open(path)
    return sum(layer.GetFeatureCount() for layer in ds)


def _fields(path):
    from osgeo import ogr
    ds = ogr.Open(path)
    defn = ds.GetLayer(0).GetLayerDefn()
    return {defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())}


def write_layer(gpkg_in, out_noext, geometry, attribution, link="", name_field="name", desc_field="other_tags",
                allow_empty=False, icon=None, fallback_name=None):
    """Write gpkg_in as out_noext.kmz or .gpkg; returns the path written.

    Fails on an empty layer (unless allow_empty) and on a label/description
    field missing from the schema: both mean upstream changed under us, and
    shipping an empty or unlabelled layer would quietly replace a good one.
    """
    n = count(gpkg_in)
    if n == 0 and not allow_empty:
        raise RuntimeError("conversion produced an empty layer")
    missing = [f for f in (name_field, desc_field) if f and f not in _fields(gpkg_in)]
    if missing and n:
        raise RuntimeError(f"label/description field(s) {missing} not in the layer")
    fmt = core.FORMATS.get(geometry, "kmz")
    if fmt == "kmz" and n > KMZ_MAX_FEATURES:
        fmt = "gpkg"
    out = f"{out_noext}.{fmt}"
    if fmt == "gpkg":
        subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", out, gpkg_in, "-lco", f"DESCRIPTION={attribution}"],
                       check=True)
    else:
        # Written as plain KML, then zipped as the KMZ's single doc.kml: a
        # LIBKML-written KMZ puts each layer behind a NetworkLink, which TAK
        # clients handle unevenly.
        kml = f"{out_noext}.kml"
        env = dict(os.environ, LIBKML_NAME_FIELD=name_field or "name", LIBKML_DESCRIPTION_FIELD=desc_field or "")
        args = ["ogr2ogr", "-f", "LIBKML", kml, gpkg_in, "-dsco", f"AUTHOR_NAME={attribution}"]
        if link:
            args += ["-dsco", f"LINK={link}"]
        subprocess.run(args, check=True, env=env)
        with open(kml, encoding="utf-8") as f:
            text = tak_style(f.read(), geometry, icon, fallback_name or os.path.basename(out_noext).split("_")[0])
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", text)
        os.remove(kml)
    return out
