"""Calibration study for a shock-arrival detector, using NASA OMNI 1-minute solar wind (time-shifted to the bow
shock). For three historic storms it lists the strongest candidate shocks and their jumps; for whole years it counts
how many candidates different threshold sets would have produced (false-alarm / event rate).
Diagnostic only. Output: calib.json and calib.txt."""
import json, statistics, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://spdf.gsfc.nasa.gov/pub/data/omni/high_res_omni/"
UA = "solar-alerts-rss-probe/1.0 (+https://github.com/SBDSBKitchener/SolarAlert)"
BAD = {"B": 9999.0, "V": 99999.0, "N": 999.0, "T": 9999999.0}
med = statistics.median


def load(year):
    """-> dict of lists indexed by minute since Jan 1 00:00 of `year`: B, V, N, T (None when missing)."""
    req = urllib.request.Request(BASE + f"omni_min{year}.asc", headers={"User-Agent": UA})
    n = 366 * 1440
    d = {k: [None] * n for k in "BVNT"}
    d["start"] = datetime(year, 1, 1, tzinfo=timezone.utc)
    rows = 0
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            p = raw.decode("latin1").split()
            if len(p) < 27:
                continue
            idx = (int(p[1]) - 1) * 1440 + int(p[2]) * 60 + int(p[3])
            vals = {"B": float(p[13]), "V": float(p[21]), "N": float(p[25]), "T": float(p[26])}
            for k, v in vals.items():
                if v < BAD[k]:
                    d[k][idx] = v
            rows += 1
    d["rows"] = rows
    return d


def window(series, a, b):
    v = [x for x in series[max(a, 0):max(b, 0)] if x is not None]
    return med(v) if len(v) >= 3 else None


def jumps(d, i):
    """Median of minutes i+2..i+7 against i-7..i-2 for each quantity."""
    out = {}
    for k in "VNBT":
        pre, post = window(d[k], i - 7, i - 1), window(d[k], i + 2, i + 8)
        out[k] = (pre, post)
    return out


def candidates(d, lo, hi, dv_min=20.0):
    """Minutes in [lo, hi) where speed jumps by >= dv_min; one candidate per 15-minute cluster (the largest)."""
    found = []
    last = -999
    for i in range(lo, hi):
        pre, post = window(d["V"], i - 7, i - 1), window(d["V"], i + 2, i + 8)
        if pre is None or post is None or post - pre < dv_min:
            continue
        dv = post - pre
        if i - last <= 15 and found:
            if dv > found[-1]["dv"]:
                found[-1] = {"i": i, "dv": dv}
            last = i
            continue
        found.append({"i": i, "dv": dv})
        last = i
    res = []
    for c in found:
        j = jumps(d, c["i"])
        v0, v1 = j["V"]
        n0, n1 = j["N"]
        b0, b1 = j["B"]
        t0, t1 = j["T"]
        hold = window(d["V"], c["i"] + 8, c["i"] + 25)
        res.append({"time": (d["start"] + timedelta(minutes=c["i"])).strftime("%Y-%m-%d %H:%M"),
                    "dV": round(v1 - v0, 1), "V_before": round(v0), "V_after": round(v1),
                    "hold_dV": round(hold - v0, 1) if hold is not None else None,
                    "N_ratio": round(n1 / n0, 2) if n0 and n1 else None,
                    "B_ratio": round(b1 / b0, 2) if b0 and b1 else None,
                    "T_ratio": round(t1 / t0, 2) if t0 and t1 else None,
                    "B_after": round(b1, 1) if b1 else None, "N_after": round(n1, 1) if n1 else None})
    return res


def rule(c, dv, k, nmin, bmin, tmin, both=True):
    """Candidate passes if speed jump >= dv, density AND field both rise, and >= k of the three ratios are met."""
    if c["dV"] < dv or c["N_ratio"] is None or c["B_ratio"] is None:
        return False
    if both and not (c["N_ratio"] > 1.15 and c["B_ratio"] > 1.1):
        return False
    hits = sum([c["N_ratio"] >= nmin, c["B_ratio"] >= bmin, (c["T_ratio"] or 0) >= tmin])
    return hits >= k


out = {"events": {}, "rates": {}}
text = []
years = {y: load(y) for y in (1989, 2024, 2025)}
for y, d in years.items():
    text.append(f"{y}: {d['rows']} rows, speed valid {sum(x is not None for x in d['V'])}")
EVENTS = {"1989-03 Quebec": (1989, datetime(1989, 3, 12, tzinfo=timezone.utc), datetime(1989, 3, 14, tzinfo=timezone.utc)),
          "2024-05 superstorm": (2024, datetime(2024, 5, 9, tzinfo=timezone.utc), datetime(2024, 5, 12, tzinfo=timezone.utc)),
          "2025-01 storm": (2025, datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 4, tzinfo=timezone.utc))}
for name, (y, a, b) in EVENTS.items():
    d = years[y]
    lo, hi = int((a - d["start"]).total_seconds() // 60), int((b - d["start"]).total_seconds() // 60)
    valid = sum(x is not None for x in d["V"][lo:hi])
    c = sorted(candidates(d, lo, hi, 10.0), key=lambda x: -x["dV"])[:10]
    out["events"][name] = {"speed_valid_minutes": valid, "of": hi - lo, "top": c}
    text.append(f"\n== {name} (speed valid {valid}/{hi - lo} min) top candidates by speed jump")
    for r in c:
        text.append("  " + json.dumps(r))
# event rates over whole years 2024 and 2025 (a year of the current solar maximum)
PARAMS = {"loose  dV>=30 k>=2 n1.4 b1.25 t1.3": (30, 2, 1.4, 1.25, 1.3),
          "medium dV>=40 k>=2 n1.5 b1.3 t1.5": (40, 2, 1.5, 1.3, 1.5),
          "strong dV>=70 k>=2 n1.6 b1.5 t1.8": (70, 2, 1.6, 1.5, 1.8),
          "severe dV>=100 k>=2 n1.8 b1.7 t2.0": (100, 2, 1.8, 1.7, 2.0)}
for y in (2024, 2025):
    d = years[y]
    cands = candidates(d, 0, 366 * 1440, 20.0)
    out["rates"][str(y)] = {"candidates_dV20": len(cands)}
    text.append(f"\n== {y}: {len(cands)} speed-jump candidates (dV>=20)")
    for pname, p in PARAMS.items():
        hits = [c for c in cands if rule(c, *p)]
        out["rates"][str(y)][pname] = {"count": len(hits), "list": [(h["time"], h["dV"], h["N_ratio"], h["B_ratio"], h["T_ratio"]) for h in hits[:40]]}
        text.append(f"  {pname}: {len(hits)}")
        if len(hits) <= 25:
            for h in hits:
                text.append(f"      {h['time']} dV={h['dV']} N x{h['N_ratio']} B x{h['B_ratio']} T x{h['T_ratio']} Bafter={h['B_after']}")
json.dump(out, open("calib.json", "w"), indent=1)
open("calib.txt", "w").write("\n".join(text) + "\n")
print("\n".join(text)[:6000])
