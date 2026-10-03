"""Turn raw ADS-B snapshots (readsb/adsb.lol JSON) and OpenMapTiles vector tiles into the compact
flights.json / map.json the mockups draw from. Research tooling only; the device would read readsb's
own aircraft.json instead.

  python prep_flights.py <snapshot-dir> <now-file> <osm_z10.json> <osm_z12.json> <vrs-dir>

Inputs:
  <snapshot-dir>  trail_*.json polled every 15 s, e.g.
                  for i in $(seq -w 1 30); do curl -s https://api.adsb.lol/v2/point/-33.8688/151.2093/60 > trail_$i.json; sleep 15; done
  <now-file>      the snapshot to draw (trails are the earlier ones within 5 minutes)
  <osm_z*.json>   from fetch_map.py
  <vrs-dir>       VRS standing-data (CC0): <CALLSIGN>.json from https://vrs-standing-data.adsb.lol/routes/<XX>/<CALLSIGN>.json
                  and airlines.csv from github.com/vradarserver/standing-data (airlines/schema-01)

Home is the default config location (Sydney CBD), not a real address.
"""
import csv
import glob
import json
import math
import os
import sys

LAT0, LON0 = -33.8688, 151.2093
KX, KY = 111.32 * math.cos(math.radians(LAT0)), 110.57
FT = 0.3048

TYPES = {  # ICAO type designator -> friendly name (readsb --db-file supplies a long form in `desc`)
    'A21N': 'Airbus A321neo', 'A321': 'Airbus A321', 'A20N': 'Airbus A320neo', 'A320': 'Airbus A320', 'A333': 'Airbus A330-300',
    'A35K': 'Airbus A350-1000', 'A388': 'Airbus A380', 'B738': 'Boeing 737-800', 'B737': 'Boeing 737-700', 'B38M': 'Boeing 737 MAX 8',
    'B788': 'Boeing 787-8', 'B789': 'Boeing 787-9', 'B350': 'King Air 350', 'A139': 'Leonardo AW139', 'DH8D': 'Dash 8 Q400',
}


def read_json(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, separators=(',', ':'))


def km(lon, lat):
    return (lon - LON0) * KX, (lat - LAT0) * KY


def dp(pts, eps):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    i, dm = 0, -1.0
    for k in range(1, len(pts) - 1):
        d = abs(dy * pts[k][0] - dx * pts[k][1] + b[0] * a[1] - b[1] * a[0]) / n if n > 1e-6 else math.hypot(pts[k][0] - a[0], pts[k][1] - a[1])
        if d > dm:
            i, dm = k, d
    return dp(pts[: i + 1], eps)[:-1] + dp(pts[i:], eps) if dm > eps else [a, b]


def airborne(a):
    return 'lat' in a and a.get('alt_baro') not in (None, 'ground') and not str(a.get('category', '')).startswith('C') and a.get('t') != 'TWR'


def flights(snap_dir, now_file, vrs_dir):
    with open(os.path.join(vrs_dir, 'airlines.csv'), encoding='utf-8-sig') as fh:
        airlines = {row['ICAO']: row for row in csv.DictReader(fh) if row.get('ICAO')}
    files = sorted(glob.glob(os.path.join(snap_dir, 'trail_*.json')))
    now_d = read_json(now_file)
    now = now_d['now'] / 1000
    trails = {}
    for f in files:
        d = read_json(f)
        if d['now'] / 1000 > now:
            continue
        for a in d['ac']:
            if airborne(a):
                trails.setdefault(a['hex'], []).append((d['now'] / 1000 - a.get('seen_pos', 0), km(a['lon'], a['lat']), a['alt_baro']))
    out = []
    for a in now_d['ac']:
        if not airborne(a):
            continue
        x, y = km(a['lon'], a['lat'])
        dist = math.hypot(x, y)
        alt_ft = a['alt_baro']
        cs = (a.get('flight') or '').strip()
        route = None
        rf = os.path.join(vrs_dir, f'{cs}.json')
        if cs and os.path.exists(rf):
            r = read_json(rf)
            ap = r.get('_airports') or []
            al = airlines.get(r.get('airline_code'), {})
            if len(ap) >= 2:
                route = {'from': ap[0]['iata'], 'to': ap[-1]['iata'], 'fromName': ap[0]['location'], 'toName': ap[-1]['location'],
                         'airline': al.get('Name'), 'iata': f"{al['IATA']}{r['number']}" if al.get('IATA') else None}
        tr = sorted(trails.get(a['hex'], []))
        out.append({
            'hex': a['hex'], 'flight': cs, 'reg': a.get('r'), 'type': a.get('t'), 'typeName': TYPES.get(a.get('t'), a.get('t')),
            'alt_ft': alt_ft, 'gs_kt': a.get('gs'), 'track': a.get('track'), 'rate_fpm': a.get('baro_rate') or a.get('geom_rate') or 0,
            'squawk': a.get('squawk'), 'category': a.get('category'),
            'x': round(x, 3), 'y': round(y, 3), 'dist_km': round(dist, 2),
            'bearing': round((math.degrees(math.atan2(x, y)) + 360) % 360, 1),
            'elev_deg': round(math.degrees(math.atan2(alt_ft * FT / 1000, dist)), 1),
            'trail': [[round(px, 3), round(py, 3), alt] for ts, (px, py), alt in tr if (now - ts) <= 300],
            'route': route,
        })
    out.sort(key=lambda r: r['dist_km'])
    return {'now': now, 'home': [LAT0, LON0], 'aircraft': out}


def map_layers(z10, z12):
    def area(r):
        return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))

    def polys(d, cls, eps, box, min_area=0.0):
        res = []
        for f in d[cls[0]]:
            if 'rings' not in f or f['p'].get('class') not in cls[1]:
                continue
            for r in f['rings'][:1]:
                if all(abs(x) > box[0] or abs(y) > box[1] for x, y in r[:: max(1, len(r) // 20)]):
                    continue  # entirely outside the view
                if cls[0] == 'water' and abs(area(r)) < min_area:
                    continue  # ponds and pools: invisible at radar scale
                s = dp(r, eps)
                if len(s) >= 4:
                    res.append([[round(x, 3), round(y, 3)] for x, y in s])
        return res
    d10, d12 = read_json(z10), read_json(z12)
    return {
        'water_wide': polys(d10, ('water', {'ocean', 'lake', 'river'}), 0.08, (70, 45), 0.15),
        'water_near': polys(d12, ('water', {'ocean', 'lake', 'river', 'dock'}), 0.03, (25, 15), 0.04),
        'runways': polys(d12, ('aeroway', {'runway'}), 0.01, (25, 15)),
        'attribution': 'Map data © OpenStreetMap contributors (ODbL), via OpenFreeMap / OpenMapTiles',
    }


if __name__ == '__main__':
    snap_dir, now_file, z10, z12, vrs = sys.argv[1:6]
    here = os.path.dirname(os.path.abspath(__file__))
    f = flights(snap_dir, now_file, vrs)
    f['attribution'] = 'Aircraft data: adsb.lol (ODbL) live snapshot; routes and airlines: VRS standing-data (CC0)'
    write_json(os.path.join(here, 'data', 'flights.json'), f)
    m = map_layers(z10, z12)
    write_json(os.path.join(here, 'data', 'map.json'), m)
    print('aircraft', len(f['aircraft']), 'map', {k: len(v) for k, v in m.items() if isinstance(v, list)})
