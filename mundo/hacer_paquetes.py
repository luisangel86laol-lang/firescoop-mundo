#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Firescoop · paquetes por país (8-oct-2026)

Para la sección «Mapas» del juego: el jugador baja un país entero y vuela sin conexión.
Lee  v1/paises.json  (lo hace hacer_mundo.py) y, si existe,  v1/aeropuertos/indice.json, y para cada país:
  - añade sus aeródromos del X-Plane Scenery Gateway (los que caen en sus cuadrículas);
  - hace un paquete  v1/paquetes/<pais>.fsp  con todo junto (un solo archivo: se baja en segundo plano).

Formato del paquete (.fsp):
  8 bytes  "FSPACK1\\n"
  4 bytes  largo N de la cabecera (entero sin signo, big-endian)
  N bytes  cabecera JSON: {"v":1,"id":…,"made":…,"files":[["t/39_-38.jz", inicio, largo], …]}
  datos    los archivos tal cual (inicio contado desde el final de la cabecera)
Van también los índices (indice.json y aeropuertos/indice.json), para poder abrir el mapa sin red.

Las cuadrículas son © colaboradores de OpenStreetMap (ODbL) y los aeródromos, © sus autores en el
X-Plane Scenery Gateway (GPL): el paquete solo los junta para la descarga, cada uno con su licencia.

Uso:  python mundo/hacer_paquetes.py public/v1
"""
import json
import math
import os
import struct
import sys

SCALE = 5
MAGIC = b"FSPACK1\n"


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "public/v1"
    path = os.path.join(base, "paises.json")
    with open(path, encoding="utf-8") as fh:
        paises = json.load(fh)
    airports = {}
    ap_made = None
    ap_index = os.path.join(base, "aeropuertos", "indice.json")
    if os.path.exists(ap_index):
        with open(ap_index, encoding="utf-8") as fh:
            j = json.load(fh)
        airports = j.get("airports", {})
        ap_made = j.get("made")
    out = os.path.join(base, "paquetes")
    os.makedirs(out, exist_ok=True)
    for c in paises["countries"]:
        keys = set(c["tiles"])
        codes = sorted(code for code, v in airports.items()
                       if len(v) >= 2 and f"{math.floor(v[0] * SCALE)}_{math.floor(v[1] * SCALE)}" in keys)
        files = ["indice.json"] + [f"t/{k}.jz" for k in c["tiles"]]
        if os.path.exists(ap_index):
            files.append("aeropuertos/indice.json")
        files += [f"aeropuertos/{code}.jz" for code in codes]
        entries, blobs, offset = [], [], 0
        for rel in files:
            fp = os.path.join(base, rel)
            if not os.path.exists(fp):
                continue
            with open(fp, "rb") as fh:
                data = fh.read()
            entries.append([rel, offset, len(data)])
            blobs.append(data)
            offset += len(data)
        header = json.dumps({"v": 1, "id": c["id"], "made": paises["made"], "airportsMade": ap_made,
                             "files": entries}, separators=(",", ":")).encode("utf-8")
        slug = c["id"].replace("/", "_")
        pack = os.path.join(out, slug + ".fsp")
        with open(pack, "wb") as fh:
            fh.write(MAGIC)
            fh.write(struct.pack(">I", len(header)))
            fh.write(header)
            for b in blobs:
                fh.write(b)
        c["airports"] = codes
        c["airportBytes"] = sum(airports[code][2] for code in codes if len(airports[code]) >= 3)
        c["pack"] = f"paquetes/{slug}.fsp"
        c["packBytes"] = os.path.getsize(pack)
        print(f"  {c['id']}: {len(c['tiles'])} cuadrículas, {len(codes)} aeródromos, {c['packBytes'] / 1e6:.1f} MB", flush=True)
    paises["airportsMade"] = ap_made
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(paises, fh, separators=(",", ":"), ensure_ascii=False)
    print("✓ paquetes hechos")


if __name__ == "__main__":
    main()
