"""Calibration study, part 2: (a) March 1989 from OMNI2 HOURLY data (no 1-minute plasma exists for that storm);
(b) alternative shock rules that do not require the field to rise (a storm inside a storm already has a high
field), with a persistence test, counted over 2024 and 2025. Diagnostic only. Output: calib.json, calib.txt."""
import json, urllib.request
src = open("tools/omni_shocks.py").read()
exec(src[:src.index('out = {"events"')])      # reuse load(), candidates(), etc.

text, out = [], {}
# ---- (a) 1989 hourly -------------------------------------------------------------------------------
rows = []
for url in (BASE.replace("high_res_omni", "low_res_omni") + "omni2_1989.dat",
            BASE.replace("high_res_omni", "low_res_omni") + "omni2_all_years.dat"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                p = raw.decode("latin1").split()
                if len(p) > 24 and p[0] == "1989" and 60 <= int(p[1]) <= 75:
                    rows.append(p)
                elif len(p) > 24 and p[0].isdigit() and int(p[0]) > 1989:
                    break
        text.append(f"1989 hourly source: {url} ({len(rows)} rows)")
        break
    except Exception as e:
        text.append(f"{url}: {e}")
text.append("1989 hourly (DOY 71 = 12 March, 72 = 13 March): year doy hr | B(nT) T(K) N(/cc) V(km/s)  [999.9 / 9999999 / 9999 = missing]")
for p in rows:
    doy, hr = int(p[1]), int(p[2])
    if (doy, hr) >= (71, 12) and (doy, hr) <= (73, 12):
        text.append(f"  {p[0]} {doy} {hr:02d} | B={p[8]} T={p[22]} N={p[23]} V={p[24]}")
# ---- (b) rules ------------------------------------------------------------------------------------------
def rule2(c, dv, k, nmin, bmin, tmin, hold):
    if c["dV"] < dv or c["hold_dV"] is None or c["hold_dV"] < hold * c["dV"]:
        return False
    hits = sum([(c["N_ratio"] or 0) >= nmin, (c["B_ratio"] or 0) >= bmin, (c["T_ratio"] or 0) >= tmin])
    return hits >= k

RULES = {
    "W1 dV>=40 k2 (N1.5 B1.3 T1.3) hold.6": (40, 2, 1.5, 1.3, 1.3, 0.6),
    "W2 dV>=45 k2 (N1.8 B1.3 T1.3) hold.6": (45, 2, 1.8, 1.3, 1.3, 0.6),
    "W3 dV>=50 k2 (N2.0 B1.3 T1.3) hold.6": (50, 2, 2.0, 1.3, 1.3, 0.6),
    "A1 dV>=100 k2 (N1.8 B1.7 T2.0) hold.6": (100, 2, 1.8, 1.7, 2.0, 0.6),
    "A2 dV>=100 k3 (N2.0 B1.5 T2.0) hold.6": (100, 3, 2.0, 1.5, 2.0, 0.6),
    "A3 dV>=120 k2 (N2.0 B1.5 T2.0) hold.6": (120, 2, 2.0, 1.5, 2.0, 0.6),
}
years = {y: load(y) for y in (2024, 2025)}
allc = {}
for y, d in years.items():
    allc[y] = candidates(d, 0, 366 * 1440, 20.0)
KNOWN = {"2024-05-10 17:0": "May 2024 main shock", "2024-12-31 16:5": "31 Dec 2024 shock", "2025-01-01 17:0": "Jan 2025 storm shock"}
for name, p in RULES.items():
    line = [f"\n== {name}"]
    for y in (2024, 2025):
        hits = [c for c in allc[y] if rule2(c, *p)]
        line.append(f"  {y}: {len(hits)} detections")
        if len(hits) <= 14:
            for h in hits:
                line.append(f"      {h['time']} dV={h['dV']} hold={h['hold_dV']} N x{h['N_ratio']} B x{h['B_ratio']} T x{h['T_ratio']}")
        for h in hits:
            for k_, lbl in KNOWN.items():
                if h["time"].startswith(k_):
                    line.append(f"      >>> {lbl} detected: {h['time']} dV={h['dV']} N x{h['N_ratio']} B x{h['B_ratio']} T x{h['T_ratio']}")
    text += line
# the Jan 2025 shock and neighbours, for reference
text.append("\n== all candidates dV>=20 on 1-2 Jan 2025 with a density jump >= 1.5")
for c in allc[2025]:
    if c["time"].startswith(("2025-01-01", "2025-01-02")) and (c["N_ratio"] or 0) >= 1.5:
        text.append("   " + json.dumps(c))
json.dump({"text": text}, open("calib.json", "w"))
open("calib.txt", "w").write("\n".join(text) + "\n")
print("\n".join(text)[:4000])
