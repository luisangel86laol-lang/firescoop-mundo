# Firescoop · mundo

Map zones for the Firescoop game (iPhone / iPad), built from OpenStreetMap.

- `mundo/hacer_mundo.py` reads the OpenStreetMap country extracts (Geofabrik) listed in `paises.txt` and splits them
  into 0.2° × 0.2° tiles: buildings (as rotated rectangles), runways, taxiways, aprons, helipads, windsocks,
  roads, towns and churches. Each tile is compressed (raw DEFLATE) as `v1/t/<i>_<j>.jz`, with `i = floor(lat × 5)`
  and `j = floor(lon × 5)`, plus the index `v1/indice.json`.
- `.github/workflows/mundo.yml` runs it on GitHub (manually, whenever `paises.txt` changes, and on the 1st of every month) and publishes the
  result on GitHub Pages.

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
