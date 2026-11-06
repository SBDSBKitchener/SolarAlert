"""Diagnostic: was the 6 Nov 2026 ALERT 'Europe 1038 nT/min at Nurmijarvi (NUR), 08:45 UTC' real? Writes calib_dbdt.txt."""
import json, math, re, statistics, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "solar-alerts-diagnostic"}
out = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    out.append(s)


def get(url, timeout=90):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


T0 = datetime(2026, 11, 6, 8, 45, tzinfo=timezone.utc)
log("== Kp around the alert")
try:
    rows = json.loads(get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))
    for r in rows[1:] if isinstance(rows[0], list) else rows:
        t = r[0] if isinstance(r, list) else r.get("time_tag")
        if t and "2026-11-05 18" <= t[:13] <= "2026-11-06 12":
            log("  ", r)
except Exception as e:
    log("Kp failed", e)


def parse(text):
    rows, comps = [], None
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
        raw = (a, b)
        if abs(a) >= 88888 or abs(b) >= 88888:
            rows.append((t, None, None, raw))
            continue
        if comps and comps[:2] == ["H", "D"]:
            a, b = a * math.cos(math.radians(b / 60)), a * math.sin(math.radians(b / 60))
        rows.append((t, a, b, raw))
    return rows, comps


start, end = T0 - timedelta(hours=1), T0 + timedelta(hours=1)
for state in ("Best available", "reported"):
    log(f"== NUR, publicationState={state}")
    try:
        url = "https://imag-data.bgs.ac.uk/GIN_V1/GINServices?" + urllib.parse.urlencode({
            "Request": "GetData", "format": "iaga2002", "testObsys": 0, "observatoryIagaCode": "NUR",
            "samplesPerDay": "minute", "publicationState": state, "dataStartDate": "2026-11-06",
            "dataDuration": 1}, quote_via=urllib.parse.quote)
        rows, comps = parse(get(url))
        log("components", comps, "rows", len(rows))
        sel = [r for r in rows if T0 - timedelta(minutes=12) <= r[0] <= T0 + timedelta(minutes=12)]
        prev = None
        for t, x, y, raw in sel:
            rate = ""
            if prev and x is not None and prev[1] is not None and (t - prev[0]).total_seconds() == 60:
                rate = f"{math.hypot(x - prev[1], y - prev[2]):.0f}"
            log(f"  {t:%H:%M} raw={raw} rate={rate}")
            prev = (t, x, y)
    except Exception as e:
        log("  failed", type(e).__name__, str(e)[:150])

log("== other stations, peak rate within +-30 min of the alert and in the 2 h window")
for code in ["SOD", "KEV", "LYC", "ABK", "TRO", "MEK", "HLP", "BEL", "DOB", "UPS", "HAN", "TAR", "BFE", "NGK", "WNG", "LER", "ESK", "HRN", "BJN", "IVA", "KIR"]:
    try:
        url = "https://imag-data.bgs.ac.uk/GIN_V1/GINServices?" + urllib.parse.urlencode({
            "Request": "GetData", "format": "iaga2002", "testObsys": 0, "observatoryIagaCode": code,
            "samplesPerDay": "minute", "publicationState": "Best available", "dataStartDate": "2026-11-06",
            "dataDuration": 1}, quote_via=urllib.parse.quote)
        rows = [r for r in parse(get(url))[0] if r[1] is not None and start <= r[0] <= end]
        if len(rows) < 3:
            log(f"  {code}: {len(rows)} readings")
            continue
        rates = [(t1, math.hypot(x1 - x0, y1 - y0)) for (t0, x0, y0, _), (t1, x1, y1, _) in zip(rows, rows[1:]) if (t1 - t0).total_seconds() == 60]
        pk = max(rates, key=lambda r: r[1])
        near = [f"{t:%H:%M}={v:.0f}" for t, v in rates if abs((t - T0).total_seconds()) <= 1800 and v >= 30]
        log(f"  {code}: n={len(rows)} peak {pk[1]:.0f} nT/min at {pk[0]:%H:%M}; >=30 within +-30 min: {near}")
    except Exception as e:
        log(f"  {code}: failed {type(e).__name__} {str(e)[:100]}")

open("calib_dbdt.txt", "w").write("\n".join(out))
