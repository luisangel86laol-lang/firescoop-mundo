# Firescoop · mundo

Map zones for the Firescoop game (iPhone / iPad), built from OpenStreetMap.

- `mundo/hacer_mundo.py` reads the OpenStreetMap country extracts (Geofabrik) listed in `paises.txt` and splits them
  into 0.2° × 0.2° tiles: buildings (as rotated rectangles), runways, taxiways, aprons, helipads, windsocks,
  roads, towns and churches. Each tile is compressed (raw DEFLATE) as `v1/t/<i>_<j>.jz`, with `i = floor(lat × 5)`
  and `j = floor(lon × 5)`, plus the index `v1/indice.json`.
- `.github/workflows/mundo.yml` runs it on GitHub (manually, whenever `paises.txt` changes, and on the 1st of every month) and publishes the
  result on GitHub Pages.

- `mundo/hacer_aeropuertos.py` takes, for every airport of the [X-Plane Scenery Gateway](https://gateway.x-plane.com)
  inside those tiles, its recommended scenery and extracts pavements, painted lines, lights, parking spots,
  windsocks and runways into `v1/aeropuertos/<ICAO>.jz` (+ `v1/aeropuertos/indice.json`). That data is
  **GPL-2.0-or-later** (© the Gateway authors) and is published under the same license, in separate files
  (see `v1/aeropuertos/LICENCIA.txt`); it is never mixed with the OpenStreetMap data.

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
