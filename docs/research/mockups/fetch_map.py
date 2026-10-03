"""Fetch OpenFreeMap vector tiles (OpenMapTiles schema, OpenStreetMap data) around home and save the water,
runway and place features in local km coordinates, for prep_flights.py.

  pip install mapbox-vector-tile
  python fetch_map.py <out-dir>          # writes <out-dir>/osm_z10.json and osm_z12.json

Map data © OpenStreetMap contributors (ODbL).
"""
import json
import math
import os
import sys
import urllib.request

import mapbox_vector_tile

LAT0, LON0 = -33.8688, 151.2093  # default config location (Sydney CBD)
KX, KY = 111.32 * math.cos(math.radians(LAT0)), 110.57
TILEJSON = 'https://tiles.openfreemap.org/planet'
UA = {'User-Agent': 'dawn-research/0.1 (mockup renders)'}


def km(lon, lat):
    return (lon - LON0) * KX, (lat - LAT0) * KY


def tile_xy(lon, lat, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def lonlat(z, tx, ty, px, py, extent):
    n = 2 ** z
    x, y = tx + px / extent, ty + py / extent
    return x / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def convert(coords, z, tx, ty, extent):
    return [km(*lonlat(z, tx, ty, px, py, extent)) for px, py in coords]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def collect(template, z, dlon, dlat, layers=('water', 'aeroway', 'place')):
    x0, y0 = tile_xy(LON0 - dlon, LAT0 + dlat, z)
    x1, y1 = tile_xy(LON0 + dlon, LAT0 - dlat, z)
    out = {k: [] for k in layers}
    for tx in range(int(x0), int(x1) + 1):
        for ty in range(int(y0), int(y1) + 1):
            t = mapbox_vector_tile.decode(get(template.format(z=z, x=tx, y=ty)), default_options={'y_coord_down': True})
            for name in layers:
                layer = t.get(name)
                if not layer:
                    continue
                ext = layer.get('extent', 4096)
                for f in layer['features']:
                    g, props = f['geometry'], f['properties']
                    if g['type'] in ('Polygon', 'MultiPolygon'):
                        for poly in ([g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']):
                            out[name].append({'rings': [convert(r, z, tx, ty, ext) for r in poly], 'p': props})
                    elif g['type'] == 'LineString':
                        out[name].append({'line': convert(g['coordinates'], z, tx, ty, ext), 'p': props})
                    elif g['type'] == 'Point':
                        out[name].append({'pt': convert([g['coordinates']], z, tx, ty, ext)[0], 'p': props})
    return out


if __name__ == '__main__':
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    template = json.loads(get(TILEJSON))['tiles'][0]
    for z, dlon, dlat in ((10, 0.62, 0.36), (12, 0.20, 0.12)):
        d = collect(template, z, dlon, dlat)
        with open(os.path.join(out_dir, f'osm_z{z}.json'), 'w', encoding='utf-8') as fh:
            json.dump(d, fh)
        print(z, {k: len(v) for k, v in d.items()})
