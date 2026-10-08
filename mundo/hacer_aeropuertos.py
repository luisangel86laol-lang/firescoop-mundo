#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Firescoop · aeródromos del X-Plane Scenery Gateway (7-oct-2026)

Para cada aeródromo del Gateway que cae en las cuadrículas ya hechas (public/v1/indice.json), baja su
escenario recomendado y saca de su apt.dat lo que el juego pinta sobre la foto de satélite:
  - pavimentos (rodaje, plataformas…) con su superficie, contorno y huecos
  - líneas pintadas (centro de rodaje, bordes, puntos de espera…) y sus luces
  - puestos de aparcamiento, mangas de viento y pistas
Cada aeródromo va en  v1/aeropuertos/<ICAO>.jz  (JSON con DEFLATE sin cabecera) y el índice en
v1/aeropuertos/indice.json.

Licencia: los datos del Gateway son GPL versión 2 o posterior (© sus autores, gateway.x-plane.com).
Lo que sale de aquí es una obra derivada y se publica con la misma licencia (LICENCIA.txt). Va en
archivos aparte de los de OpenStreetMap (ODbL): no se mezclan.

Uso:  python mundo/hacer_aeropuertos.py public/v1
"""
import base64
import io
import json
import math
import os
import sys
import time
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

API = "https://gateway.x-plane.com/apiv1"
UA = "firescoop-mundo (github.com/luisangel86laol-lang/firescoop-mundo)"
SCALE = 5

LICENCIA = """Aeródromos de Firescoop · mundo (v1/aeropuertos)

Estos archivos se han hecho a partir de los escenarios de aeródromo del X-Plane Scenery Gateway
(https://gateway.x-plane.com), © sus autores, publicados con la GNU General Public License, versión 2
o (a tu elección) cualquier versión posterior. Son una obra derivada y se distribuyen con la misma
licencia: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html

Cada archivo <ICAO>.jz es JSON comprimido con DEFLATE (sin cabecera) y lleva el número del escenario
del Gateway del que sale ("scenery"). El programa que los genera es mundo/hacer_aeropuertos.py de
https://github.com/luisangel86laol-lang/firescoop-mundo
"""


def get_json(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


# ---------- apt.dat ----------

def bezier(p0, c0, c1, p1, steps=8):
    """Tramo de p0 a p1 con los tiradores c0 (salida) y c1 (llegada); None = recto."""
    if c0 is None and c1 is None:
        return [p1]
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        u = 1 - t
        if c0 is not None and c1 is not None:
            x = u**3 * p0[0] + 3 * u * u * t * c0[0] + 3 * u * t * t * c1[0] + t**3 * p1[0]
            y = u**3 * p0[1] + 3 * u * u * t * c0[1] + 3 * u * t * t * c1[1] + t**3 * p1[1]
        else:
            c = c0 if c0 is not None else c1
            x = u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0]
            y = u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]
        out.append((x, y))
    return out


def flatten(nodes, closed):
    """nodes: [(punto, tirador o None)] → polilínea (lat, lon). En X-Plane el tirador de un nodo es el de
    salida; el de llegada es su simétrico respecto al nodo."""
    if not nodes:
        return []
    pts = [nodes[0][0]]
    seq = nodes + ([nodes[0]] if closed else [])
    for a, b in zip(seq, seq[1:]):
        c0 = a[1]
        c1 = (2 * b[0][0] - b[1][0], 2 * b[0][1] - b[1][1]) if b[1] is not None else None
        pts += bezier(a[0], c0, c1, b[0])
    if closed and len(pts) > 1:
        pts.pop()
    return pts


def flat(pts):
    out = []
    for la, lo in pts:
        out += [round(la, 6), round(lo, 6)]
    return out


def parse_apt(text):
    ap = {"pav": [], "lin": [], "park": [], "parkNames": [], "parkFor": [], "sock": [], "rwy": []}
    mode = None          # "pav" | "lin"
    cur = None           # pavimento o línea en curso
    ring = []            # nodos del anillo / tramo en curso
    line_type = 0
    light_type = 0

    def end_ring(closed):
        nonlocal ring
        if mode == "pav" and len(ring) >= 3:
            cur["r"].append(flat(flatten(ring, True)))
        elif mode == "lin" and len(ring) >= 2:
            ap["lin"].append({"t": line_type, "l": light_type, "p": flat(flatten(ring, closed))})
        ring = []

    for raw in text.splitlines():
        f = raw.split()
        if not f:
            continue
        try:
            code = int(f[0])
        except ValueError:
            continue
        if code in (1, 16, 17) and len(f) >= 5:
            if "icao" in ap:
                break                         # un solo aeródromo por archivo
            ap["elev"] = float(f[1]) * 0.3048
            ap["icao"] = f[4]
            ap["name"] = " ".join(f[5:])
        elif code == 100 and len(f) >= 26:
            ap["rwy"].append([float(f[1]), int(f[2]), round(float(f[9]), 6), round(float(f[10]), 6),
                              round(float(f[18]), 6), round(float(f[19]), 6), f[8], f[17]])
        elif code == 110:
            if mode and ring:
                end_ring(True)
            mode = "pav"
            cur = {"s": int(float(f[1])), "r": []}
            ap["pav"].append(cur)
            ring = []
        elif code in (120, 130):
            if mode and ring:
                end_ring(False)
            mode = "lin" if code == 120 else None
            ring = []
            line_type = light_type = 0
        elif code in (111, 112, 113, 114, 115, 116) and mode:
            p = (float(f[1]), float(f[2]))
            bez = code in (112, 114, 116)
            c = (float(f[3]), float(f[4])) if bez and len(f) >= 5 else None
            rest = f[5:] if bez else f[3:]
            if mode == "lin":
                for v in rest:
                    try:
                        n = int(v)
                    except ValueError:
                        continue
                    if 100 <= n < 200:
                        light_type = light_type or n
                    elif n > 0:
                        line_type = line_type or n
            ring.append((p, c))
            if code in (113, 114):
                end_ring(True)
            elif code in (115, 116):
                end_ring(False)
        elif code == 1300 and len(f) >= 4:
            ap["park"].append([round(float(f[1]), 6), round(float(f[2]), 6), round(float(f[3]), 1)])
            ap["parkNames"].append(" ".join(f[6:]))
            ap["parkFor"].append(f[5] if len(f) > 5 else "all")   # heavy|jets|turboprops|props|helos|fighters|all
        elif code == 15 and len(f) >= 4:
            ap["park"].append([round(float(f[1]), 6), round(float(f[2]), 6), round(float(f[3]), 1)])
            ap["parkNames"].append(" ".join(f[4:]))
            ap["parkFor"].append("all")
        elif code == 19 and len(f) >= 3:
            ap["sock"].append([round(float(f[1]), 6), round(float(f[2]), 6)])
        else:
            if mode and ring and code not in (111, 112, 113, 114, 115, 116):
                end_ring(mode == "pav")
                mode = None
    if mode and ring:
        end_ring(mode == "pav")
    ap["pav"] = [p for p in ap["pav"] if p["r"]]
    return ap


# ---------- Gateway ----------

def one(entry, out):
    code = entry["AirportCode"]
    sid = entry["RecommendedSceneryId"]
    try:
        sc = get_json(f"{API}/scenery/{sid}")["scenery"]
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(sc["masterZipBlob"])))
        name = next((n for n in z.namelist() if n.lower().endswith(".dat")
                     and "__MACOSX" not in n and not os.path.basename(n).startswith("._")), None)
        if not name:
            return None
        ap = parse_apt(z.read(name).decode("utf-8", "replace"))
    except Exception as e:
        print(f"  {code}: {e}", flush=True)
        return None
    if not ap["pav"] and not ap["rwy"]:
        return None
    ap["icao"] = code
    ap["lat"] = entry["Latitude"]
    ap["lon"] = entry["Longitude"]
    ap["scenery"] = sid
    ap["author"] = sc.get("userName") or ""          # crédito: el autor del escenario en el Gateway
    raw = json.dumps(ap, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = comp.compress(raw) + comp.flush()
    with open(os.path.join(out, code + ".jz"), "wb") as fh:
        fh.write(data)
    return code, [round(entry["Latitude"], 5), round(entry["Longitude"], 5), len(data)]


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "public/v1"
    with open(os.path.join(base, "indice.json"), encoding="utf-8") as fh:
        tiles = set(json.load(fh)["tiles"].keys())
    out = os.path.join(base, "aeropuertos")
    os.makedirs(out, exist_ok=True)
    print("Lista de aeródromos del Gateway…", flush=True)
    every = get_json(f"{API}/airports")["airports"]
    wanted = []
    for a in every:
        if not a.get("RecommendedSceneryId") or a.get("Deprecated") or a.get("Latitude") is None:
            continue
        key = f"{math.floor(a['Latitude'] * SCALE)}_{math.floor(a['Longitude'] * SCALE)}"
        if key in tiles:
            wanted.append(a)
    print(f"{len(wanted)} aeródromos en las zonas publicadas", flush=True)
    index = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for r in pool.map(lambda e: one(e, out), wanted):
            if r:
                index[r[0]] = r[1]
    with open(os.path.join(out, "indice.json"), "w", encoding="utf-8") as fh:
        json.dump({"v": 1, "made": date.today().isoformat(),
                   "source": "X-Plane Scenery Gateway (GPL-2.0-or-later)", "airports": index},
                  fh, separators=(",", ":"))
    with open(os.path.join(out, "LICENCIA.txt"), "w", encoding="utf-8") as fh:
        fh.write(LICENCIA)
    print(f"Hecho: {len(index)} aeródromos, {sum(v[2] for v in index.values()) / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
