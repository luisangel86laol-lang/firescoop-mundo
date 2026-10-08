#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Firescoop · mundo (7-oct-2026)

Lee los mapas completos de OpenStreetMap por país (archivos .osm.pbf de Geofabrik) y los parte en
cuadrículas de 0,2° × 0,2° (unos 22 × 17 km), que el juego descarga según por dónde vuela.
Cada cuadrícula lleva:
  - cada edificio, como rectángulo girado: [lat, lon, largo, ancho, giro, altura, tipo]
  - pistas, calles de rodaje, plataformas, helipuertos y mangas de viento
  - carreteras (autovías a locales) con sentido único, puentes y túneles
  - núcleos (ciudad, pueblo, aldea) e iglesias
  - tendidos eléctricos (alta tensión y líneas de distribución) con sus apoyos, y aerogeneradores
    (8-oct: para que los cables y molinos salgan en el juego donde están de verdad)
Va comprimida (DEFLATE sin cabecera, lo que lee Apple con `.zlib`) en  v1/t/<i>_<j>.jz,
con i = floor(lat × 5) y j = floor(lon × 5), y un índice  v1/indice.json.

Uso:  python mundo/hacer_mundo.py portugal.osm.pbf spain.osm.pbf --salida public/v1
Datos © colaboradores de OpenStreetMap (ODbL).
"""
import json
import math
import os
import shutil
import sys
import time
import zlib
from datetime import date

import osmium

SCALE = 5                      # cuadrículas de 1/5 de grado
FLUSH = 300_000                # líneas en memoria antes de escribir a disco


def tile_of(lat, lon):
    return math.floor(lat * SCALE), math.floor(lon * SCALE)


# ---------- geometría ----------

def local_m(lat0, lon0):
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    return (lambda la, lo: ((lo - lon0) * kx, (la - lat0) * ky)), (lambda x, y: (lat0 + y / ky, lon0 + x / kx))


def hull(pts):
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_rect(pts):
    """Rectángulo girado de menor área: (cx, cy, largo, ancho, giro en grados desde el este hacia el norte)."""
    h = hull(pts)
    if len(h) < 3:
        return None
    best = None
    for i in range(len(h)):
        x1, y1 = h[i]
        x2, y2 = h[(i + 1) % len(h)]
        a = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(a), math.sin(a)
        us = [x * c + y * s for x, y in h]
        vs = [-x * s + y * c for x, y in h]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, a, min(us), max(us), min(vs), max(vs))
    _, a, u0, u1, v0, v1 = best
    c, s = math.cos(a), math.sin(a)
    uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
    cx, cy = uc * c - vc * s, uc * s + vc * c
    length, width = u1 - u0, v1 - v0
    if width > length:
        length, width = width, length
        a += math.pi / 2
    return cx, cy, length, width, math.degrees(a) % 180


def num(v):
    try:
        return float(str(v).replace(",", ".").split(";")[0].strip().split(" ")[0])
    except (TypeError, ValueError):
        return None


# 0 casa · 1 pisos · 2 nave/industria/comercio · 3 iglesia · 4 hangar · 5 público · 6 torre de control
def kind_of(tags):
    b = tags.get("building", "yes")
    if tags.get("aeroway") == "control_tower":
        return 6
    if b in ("church", "chapel", "cathedral", "basilica") or tags.get("amenity") == "place_of_worship":
        return 3
    if b == "hangar" or tags.get("aeroway") == "hangar":
        return 4
    if b in ("industrial", "warehouse", "commercial", "retail", "supermarket", "factory", "manufacture", "farm_auxiliary",
             "barn", "storage_tank", "service", "transportation", "garages", "parking", "greenhouse", "shed", "cowshed"):
        return 2
    if b in ("apartments", "residential", "hotel", "dormitory"):
        return 1
    if b in ("school", "hospital", "public", "civic", "government", "university", "college", "train_station", "terminal",
             "office", "stadium", "sports_hall", "kindergarten", "fire_station"):
        return 5
    return 0


def height_of(tags, kind):
    h = num(tags.get("height"))
    if h and 2 <= h <= 300:
        return h
    lv = num(tags.get("building:levels"))
    if lv and 1 <= lv <= 80:
        return lv * 3.0 + (1.0 if kind in (0, 1) else 0.5)
    return 0.0


ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary",
         "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
AERO_WAYS = {"runway", "taxiway", "taxilane", "apron", "helipad"}
PLACES = {"town": 0, "village": 1, "hamlet": 2}


def r5(v):
    return round(v, 5)


# ---------- lectura ----------

class Tiles:
    """Líneas por cuadrícula, guardadas en disco poco a poco (España tiene millones de edificios)."""
    def __init__(self, tmp):
        self.tmp = tmp
        os.makedirs(tmp, exist_ok=True)
        self.buf = {}
        self.count = 0
        self.touched = set()           # cuadrículas del país que se está leyendo (para paises.json)

    def add(self, key, kind, item):
        self.touched.add(key)
        self.buf.setdefault(key, []).append(kind + "|" + json.dumps(item, separators=(",", ":")))
        self.count += 1
        if self.count >= FLUSH:
            self.flush()

    def flush(self):
        for key, lines in self.buf.items():
            with open(os.path.join(self.tmp, f"{key[0]}_{key[1]}.txt"), "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.buf = {}
        self.count = 0


def read(path, tiles, stats):
    fp = (osmium.FileProcessor(path)
          .with_locations(storage="sparse_mem_array")
          .with_filter(osmium.filter.KeyFilter("building", "highway", "aeroway", "place", "amenity", "power")))
    for o in fp:
        tags = o.tags
        if o.is_node():
            if not o.location.valid():
                continue
            lat, lon = o.location.lat, o.location.lon
            key = tile_of(lat, lon)
            place = tags.get("place")
            aero = tags.get("aeroway")
            if place in PLACES:
                tiles.add(key, "p", [r5(lat), r5(lon), PLACES[place]])
            elif tags.get("amenity") == "place_of_worship":
                tiles.add(key, "c", [r5(lat), r5(lon)])
            elif aero == "windsock":
                tiles.add(key, "s", [r5(lat), r5(lon)])
            elif aero == "helipad":
                tiles.add(key, "h", [r5(lat), r5(lon)])
            elif tags.get("power") == "generator" and (tags.get("generator:source") == "wind"
                                                       or tags.get("generator:method") == "wind_turbine"):
                # Aerogenerador: [lat, lon, altura total (m, 0 = sin dato), diámetro del rotor (m, 0 = sin dato)]
                tiles.add(key, "g", [r5(lat), r5(lon), round(num(tags.get("height")) or 0, 1),
                                     round(num(tags.get("rotor:diameter")) or 0, 1)])
                stats["molinos"] = stats.get("molinos", 0) + 1
            continue
        if not o.is_way():
            continue
        pts = [(n.lat, n.lon) for n in o.nodes if n.location.valid()]
        if len(pts) < 2:
            continue
        closed = len(pts) > 3 and o.nodes[0].ref == o.nodes[-1].ref
        hw = tags.get("highway")
        aero = tags.get("aeroway")
        if "building" in tags or aero in ("hangar", "control_tower"):
            if len(pts) < 3:
                continue
            lat0 = sum(p[0] for p in pts) / len(pts)
            lon0 = sum(p[1] for p in pts) / len(pts)
            m, back = local_m(lat0, lon0)
            r = min_rect([m(*p) for p in pts])
            if not r:
                continue
            cx, cy, length, width, deg = r
            if length < 2.5 or width < 2 or length > 800:
                continue
            k = kind_of(tags)
            la, lo = back(cx, cy)
            tiles.add(tile_of(la, lo), "b", [r5(la), r5(lo), round(length * 2) / 2, round(width * 2) / 2,
                                            int(round(deg)) % 180, round(height_of(tags, k), 1), k])
            stats["edificios"] += 1
        elif hw in ROADS:
            ow = tags.get("oneway", "")
            oneway = ow in ("yes", "1", "true") or tags.get("junction") == "roundabout" or (hw == "motorway" and ow != "no")
            if ow == "-1":
                oneway = True
                pts.reverse()
            bridge = tags.get("bridge", "no") != "no"
            tunnel = tags.get("tunnel", "no") != "no"
            # Partida por cuadrículas: cada trozo con los tramos cuyo punto medio cae en ella
            # (comparten el punto de corte: en el juego se unen solas)
            run, cur = [pts[0]], None
            for a, b in zip(pts, pts[1:]):
                key = tile_of((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if cur is not None and key != cur:
                    tiles.add(cur, "r", {"k": hw, "o": int(oneway), "b": int(bridge), "t": int(tunnel),
                                         "pts": [[r5(x), r5(y)] for x, y in run]})
                    run = [a]
                cur = key
                run.append(b)
            if cur is not None and len(run) >= 2:
                tiles.add(cur, "r", {"k": hw, "o": int(oneway), "b": int(bridge), "t": int(tunnel),
                                     "pts": [[r5(x), r5(y)] for x, y in run]})
            stats["carreteras"] += 1
        elif tags.get("power") in ("line", "minor_line"):
            # Tendido eléctrico: los nodos son los apoyos. k = 0 alta tensión · 1 distribución;
            # v = tensión en kV (la más alta si hay varias; 0 = sin dato)
            kv = 0
            for part in tags.get("voltage", "").replace(",", ";").split(";"):
                val = num(part)
                if val:
                    kv = max(kv, int(val / 1000) if val >= 1000 else int(val))
            kind = 0 if tags.get("power") == "line" else 1
            run, cur = [pts[0]], None
            for a, b in zip(pts, pts[1:]):
                key = tile_of((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if cur is not None and key != cur:
                    tiles.add(cur, "e", {"k": kind, "v": kv, "pts": [[r5(x), r5(y)] for x, y in run]})
                    run = [a]
                cur = key
                run.append(b)
            if cur is not None and len(run) >= 2:
                tiles.add(cur, "e", {"k": kind, "v": kv, "pts": [[r5(x), r5(y)] for x, y in run]})
            stats["tendidos"] = stats.get("tendidos", 0) + 1
        elif aero in AERO_WAYS:
            lat0 = sum(p[0] for p in pts) / len(pts)
            lon0 = sum(p[1] for p in pts) / len(pts)
            key = tile_of(lat0, lon0)
            if aero == "runway":
                if closed or tags.get("area") == "yes":
                    m, back = local_m(lat0, lon0)
                    r = min_rect([m(*p) for p in pts])
                    if not r:
                        continue
                    cx, cy, length, width, deg = r
                    a = math.radians(deg)
                    e1 = back(cx - math.cos(a) * length / 2, cy - math.sin(a) * length / 2)
                    e2 = back(cx + math.cos(a) * length / 2, cy + math.sin(a) * length / 2)
                    tiles.add(key, "R", {"ref": tags.get("ref", ""), "w": round(width, 1), "surf": tags.get("surface", ""),
                                         "pts": [[r5(e1[0]), r5(e1[1])], [r5(e2[0]), r5(e2[1])]]})
                else:
                    tiles.add(key, "R", {"ref": tags.get("ref", ""), "w": num(tags.get("width")) or 0,
                                         "surf": tags.get("surface", ""), "pts": [[r5(x), r5(y)] for x, y in pts]})
                stats["pistas"] += 1
            elif aero in ("taxiway", "taxilane"):
                tiles.add(key, "T", {"w": num(tags.get("width")) or (8 if aero == "taxilane" else 12),
                                     "pts": [[r5(x), r5(y)] for x, y in pts]})
            elif aero == "apron" and closed:
                tiles.add(key, "A", [[r5(x), r5(y)] for x, y in pts])
            elif aero == "helipad":
                tiles.add(key, "h", [r5(lat0), r5(lon0)])
        elif tags.get("amenity") == "place_of_worship" and len(pts) >= 3:
            tiles.add(tile_of(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)), "c",
                      [r5(sum(p[0] for p in pts) / len(pts)), r5(sum(p[1] for p in pts) / len(pts))])


def write(tmp, out, made):
    tdir = os.path.join(out, "t")
    os.makedirs(tdir, exist_ok=True)
    index = {}
    total = 0
    names = {"b": "buildings", "R": "runways", "T": "taxiways", "A": "aprons", "h": "helipads", "s": "windsocks",
             "r": "roads", "p": "places", "c": "churches", "e": "power", "g": "turbines"}
    for fname in sorted(os.listdir(tmp)):
        if not fname.endswith(".txt"):
            continue
        key = fname[:-4]
        i, j = (int(v) for v in key.split("_"))
        tile = {"v": 1, "id": "t_" + key, "made": made,
                "bbox": [i / SCALE, j / SCALE, (i + 1) / SCALE, (j + 1) / SCALE],
                "source": "© OpenStreetMap contributors (ODbL)"}
        lists = {v: [] for v in names.values()}
        with open(os.path.join(tmp, fname), encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if len(line) < 3:
                    continue
                lists[names[line[0]]].append(json.loads(line[2:]))
        tile.update({k: v for k, v in lists.items() if v})
        raw = json.dumps(tile, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)          # DEFLATE sin cabecera (Apple .zlib)
        data = comp.compress(raw) + comp.flush()
        with open(os.path.join(tdir, key + ".jz"), "wb") as f:
            f.write(data)
        index[key] = len(data)
        total += len(data)
    with open(os.path.join(out, "indice.json"), "w", encoding="utf-8") as f:
        json.dump({"v": 1, "tile": 1 / SCALE, "made": made, "source": "© OpenStreetMap contributors (ODbL)",
                   "tiles": index}, f, separators=(",", ":"))
    return len(index), total


# Nombres de los países en los 4 idiomas del juego (los que no estén aquí salen con el nombre de Geofabrik)
NOMBRES = {
    "europe/portugal": ("Portugal", "Portugal", "Portugal", "Portugal"),
    "europe/spain": ("España", "Spain", "Espanha", "Espagne"),
    "africa/canary-islands": ("Canarias", "Canary Islands", "Canárias", "Canaries"),
    "europe/france": ("Francia", "France", "França", "France"),
    "europe/italy": ("Italia", "Italy", "Itália", "Italie"),
    "europe/greece": ("Grecia", "Greece", "Grécia", "Grèce"),
    "europe/andorra": ("Andorra", "Andorra", "Andorra", "Andorre"),
    "europe/croatia": ("Croacia", "Croatia", "Croácia", "Croatie"),
    "europe/turkey": ("Turquía", "Turkey", "Turquia", "Turquie"),
    "europe/cyprus": ("Chipre", "Cyprus", "Chipre", "Chypre"),
    "africa/morocco": ("Marruecos", "Morocco", "Marrocos", "Maroc"),
    "north-america/canada": ("Canadá", "Canada", "Canadá", "Canada"),
    "north-america/us/california": ("California", "California", "Califórnia", "Californie"),
    "australia-oceania/australia": ("Australia", "Australia", "Austrália", "Australie"),
    "south-america/chile": ("Chile", "Chile", "Chile", "Chili"),
}


def country_id(path):
    """pbf/europe_portugal.osm.pbf → europe/portugal (el nombre de paises.txt)."""
    base = os.path.basename(path)
    for suf in (".osm.pbf", "-latest.osm.pbf", ".pbf"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return base.replace("_", "/")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = "public/v1"
    if "--salida" in sys.argv:
        out = sys.argv[sys.argv.index("--salida") + 1]
        args = [a for a in args if a != out]
    if not args:
        print(__doc__)
        sys.exit(1)
    made = date.today().isoformat()
    tmp = os.path.join(os.path.dirname(os.path.abspath(out)) or ".", "_tmp_mundo")
    shutil.rmtree(tmp, ignore_errors=True)
    tiles = Tiles(tmp)
    stats = {"edificios": 0, "carreteras": 0, "pistas": 0}
    per_country = []
    for path in args:
        t0 = time.time()
        print(f"· {path}…", flush=True)
        tiles.touched = set()
        read(path, tiles, stats)
        tiles.flush()
        per_country.append((country_id(path), set(tiles.touched)))
        print(f"  {stats}  ({time.time() - t0:.0f} s)", flush=True)
    n, total = write(tmp, out, made)
    shutil.rmtree(tmp, ignore_errors=True)
    # 8-oct: lista de países para la sección «Mapas» del juego (descargas sin conexión). Las cuadrículas de
    # frontera salen en los dos países; el juego no las baja dos veces. hacer_paquetes.py añade luego los
    # aeródromos y el paquete de cada país.
    with open(os.path.join(out, "indice.json"), encoding="utf-8") as f:
        sizes = json.load(f)["tiles"]
    countries = []
    for cid, keys in per_country:
        keys = sorted(k for k in (f"{i}_{j}" for i, j in keys) if k in sizes)
        if not keys:
            continue
        ij = [tuple(int(v) for v in k.split("_")) for k in keys]
        es, en, pt, fr = NOMBRES.get(cid) or (cid.split("/")[-1].replace("-", " ").title(),) * 4
        countries.append({
            "id": cid,
            "name": {"es": es, "en": en, "pt": pt, "fr": fr},
            "bbox": [min(i for i, _ in ij) / SCALE, min(j for _, j in ij) / SCALE,
                     (max(i for i, _ in ij) + 1) / SCALE, (max(j for _, j in ij) + 1) / SCALE],
            "tiles": keys,
            "bytes": sum(sizes[k] for k in keys),
        })
    with open(os.path.join(out, "paises.json"), "w", encoding="utf-8") as f:
        json.dump({"v": 1, "made": made, "countries": countries}, f, separators=(",", ":"), ensure_ascii=False)
    print(f"✓ paises.json: {', '.join(c['id'] for c in countries)}")
    # Portada con la atribución (la licencia ODbL lo pide)
    root = os.path.dirname(os.path.abspath(out))
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>Firescoop · mundo</title>
<body style="font-family:-apple-system,sans-serif;max-width:640px;margin:40px auto;padding:0 16px">
<h1>Firescoop · mundo</h1><p>Zonas del mapa que usa el juego Firescoop ({made}).</p>
<p>Datos © <a href="https://www.openstreetmap.org/copyright">colaboradores de OpenStreetMap</a>,
bajo licencia <a href="https://opendatacommons.org/licenses/odbl/">ODbL</a>.</p></body>""")
    print(f"✓ {n} cuadrículas · {total / 1e6:.1f} MB  →  {out}")


if __name__ == "__main__":
    main()
