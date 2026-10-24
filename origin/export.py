"""Export an original: binary STL plus a minimal, valid 3MF.

The 3MF is written here rather than through a library so the package is
self-contained and the vertices are exactly the ones checked. Creality's own
downloads are 3MF, so giving both formats matches what its users expect.
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import trimesh

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def write_3mf(mesh: trimesh.Trimesh, path: Path, title: str,
              designer: str = "", licence: str = "CC BY 4.0") -> Path:
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
    verts = "\n".join('<vertex x="%.3f" y="%.3f" z="%.3f"/>' % (a, b, c)
                      for a, b, c in v)
    tris = "\n".join('<triangle v1="%d" v2="%d" v3="%d"/>' % (a, b, c)
                     for a, b, c in f)
    meta = ['<metadata name="Title">%s</metadata>' % escape(title)]
    if designer:
        meta.append('<metadata name="Designer">%s</metadata>' % escape(designer))
    meta.append('<metadata name="License">%s</metadata>' % escape(licence))
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        + "\n".join(meta) + "\n"
        '<resources>\n<object id="1" type="model" name="%s">\n<mesh>\n'
        '<vertices>\n%s\n</vertices>\n<triangles>\n%s\n</triangles>\n'
        '</mesh>\n</object>\n</resources>\n'
        '<build><item objectid="1"/></build>\n</model>\n'
        % (escape(title), verts, tris))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", CONTENT_TYPES),
                           ("_rels/.rels", RELS),
                           ("3D/3dmodel.model", model)):
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return path


def check_3mf(path: Path, expect_faces: int) -> dict:
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for need in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"):
            if need not in names:
                return {"ok": False, "why": "missing %s" % need}
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    mesh = root.find(".//%smesh" % ns)
    if mesh is None:
        return {"ok": False, "why": "no mesh element"}
    nv = len(mesh.findall(".//%svertex" % ns))
    nt = len(mesh.findall(".//%striangle" % ns))
    if nt != expect_faces:
        return {"ok": False, "why": "%d triangles, STL has %d" % (nt, expect_faces)}
    return {"ok": True, "vertices": nv, "triangles": nt}


def export_model(mesh: trimesh.Trimesh, outdir: Path, slug: str,
                 title: str, designer: str = "",
                 licence: str = "CC BY 4.0") -> dict:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stl = outdir / ("%s.stl" % slug)
    mf = outdir / ("%s.3mf" % slug)
    mesh.export(stl, file_type="stl")
    write_3mf(mesh, mf, title, designer, licence)
    stl_bytes = stl.read_bytes()
    sha = hashlib.sha256(stl_bytes).hexdigest()
    mf_check = check_3mf(mf, len(mesh.faces))
    return {
        "stl": str(stl), "threemf": str(mf),
        "stl_bytes": len(stl_bytes), "stl_sha256": sha,
        "three_mf_ok": mf_check["ok"], "three_mf_detail": mf_check,
    }
