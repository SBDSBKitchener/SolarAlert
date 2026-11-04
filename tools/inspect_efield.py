"""Diagnostic: what did the space environment look like when the 4 Nov 2026 geoelectric WARNING fired
(1207 mV/km near 60.0N 109.5W at 07:51 UTC)? Writes calib_inspect.txt."""
import json, math, re, statistics, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "solar-alerts-diagnostic"}
out = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    out.append(s)


def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


T0 = datetime(2026, 11, 4, 7, 51, tzinfo=timezone.utc)

log("== Kp, 3 Nov 18:00 to 4 Nov 15:00 UTC")
try:
    rows = json.loads(get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))
    for r in rows[1:] if isinstance(rows[0], list) else rows:
        t = r[0] if isinstance(r, list) else r.get("time_tag")
        if t and "2026-11-03 18" <= t[:13] <= "2026-11-04 15":
            log("  ", r)
except Exception as e:
    log("Kp failed", e)

log("== geoelectric files near the alert")
base = "https://services.swpc.noaa.gov/json/lists/rgeojson/US-Canada-1D/"
try:
    listing = get(base)
    files = {}
    for m in re.finditer(r'href="((\d{8}T\d{6})[^"]*\.json)"', listing):
        files[datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)] = m.group(1)
    log("files listed:", len(files), "oldest", min(files), "newest", max(files))
    sel = sorted(t for t in files if T0 - timedelta(minutes=40) <= t <= T0 + timedelta(minutes=40))
    log("files in window:", len(sel))
    first = True
    for t in sel[::3] + [x for x in sel if abs((x - T0).total_seconds()) <= 120]:
        try:
            d = json.loads(get(base + files[t], 120))
        except Exception as e:
            log(t, "fetch failed", e)
            continue
        feats = d.get("features", [])
        if first:
            log("example properties:", json.dumps((feats[0].get("properties") if feats else {}))[:400],
                "| top-level keys:", [k for k in d if k != "features"])
            first = False
        vals = []
        for f in feats:
            p = f.get("properties") or {}
            ex, ey = p.get("Ex"), p.get("Ey")
            if ex is None or ey is None:
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            vals.append((math.hypot(ex, ey), lat, lon, p.get("distance_nearest_station"), p.get("quality_flag")))
        vals.sort(reverse=True)
        n1000 = sum(1 for v in vals if v[0] >= 1000)
        log(f"{t:%H:%M:%S} points={len(vals)} >=1000:{n1000} >=500:{sum(1 for v in vals if v[0]>=500)} median={statistics.median(v[0] for v in vals):.1f}")
        for v in vals[:4]:
            log(f"     {v[0]:.0f} mV/km at {v[1]:.1f}N {v[2]:.1f}E  nearest_station_km={v[3]} quality={v[4]}")
except Exception as e:
    log("geoelectric failed", e)

log("== ground magnetometers: dB/dt (nT/min), 06:30-09:00 UTC 4 Nov, peak and time")
def parse(text):
    rows = []
    comps = None
    for line in text.splitlines():
        if line.startswith("DATE"):
            comps = [c[-1] for c in line.replace("|", " ").split()[3:7]]
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", line):
            continue
        p = line.split()
        try:
            t = datetime.strptime(f"{p[0]} {p[1][:8]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            a, b = float(p[3]), float(p[4])
        except Exception:
            continue
        if abs(a) >= 88888 or abs(b) >= 88888:
            continue
        if comps and comps[:2] == ["H", "D"]:
            a, b = a * math.cos(math.radians(b / 60)), a * math.sin(math.radians(b / 60))
        rows.append((t, a, b))
    return rows

start, end = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc), datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
for code, src in [("FSM", "gin"), ("MEA", "gin"), ("FCC", "gin"), ("YKC", "gin"), ("BLC", "gin"), ("OTT", "gin"), ("BOU", "usgs"), ("FRD", "usgs"), ("SIT", "usgs"), ("CMO", "usgs")]:
    try:
        if src == "usgs":
            url = "https://geomag.usgs.gov/ws/data/?" + urllib.parse.urlencode({
                "id": code, "type": "variation", "format": "iaga2002", "sampling_period": 60,
                "starttime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endtime": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        else:
            url = "https://imag-data.bgs.ac.uk/GIN_V1/GINServices?" + urllib.parse.urlencode({
                "Request": "GetData", "format": "iaga2002", "testObsys": 0, "observatoryIagaCode": code,
                "samplesPerDay": "minute", "publicationState": "Best available",
                "dataStartDate": "2026-11-04", "dataDuration": 1}, quote_via=urllib.parse.quote)
        rows = [r for r in parse(get(url)) if start <= r[0] <= end]
        if len(rows) < 3:
            log(f"  {code}: {len(rows)} readings")
            continue
        rates = [(t1, math.hypot(x1 - x0, y1 - y0)) for (t0, x0, y0), (t1, x1, y1) in zip(rows, rows[1:]) if (t1 - t0).total_seconds() == 60]
        pk = max(rates, key=lambda r: r[1])
        near = [f"{t:%H:%M}={v:.0f}" for t, v in rates if abs((t - T0).total_seconds()) <= 600 and v >= 40]
        log(f"  {code}: {len(rows)} readings, peak {pk[1]:.0f} nT/min at {pk[0]:%H:%M}; within +-10 min of alert (>=40): {near}; newest {rows[-1][0]:%H:%M}")
    except Exception as e:
        log(f"  {code}: failed {type(e).__name__} {str(e)[:100]}")

open("calib_inspect.txt", "w").write("\n".join(out))
