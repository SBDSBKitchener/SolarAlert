"""Diagnostic: every record NOAA lists for sunspot region 4566 (and neighbours' counts). Writes calib_regions.txt."""
import json, urllib.request
out = []
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)
req = urllib.request.Request("https://services.swpc.noaa.gov/json/solar_regions.json", headers={"User-Agent": "solar-alerts-diagnostic"})
d = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
log("records:", len(d), "dates:", sorted({r.get("observed_date") for r in d}))
for r in d:
    if r.get("region") == 4566:
        log(json.dumps(r, sort_keys=True))
from collections import Counter
c = Counter((r.get("region"), r.get("observed_date")) for r in d)
log("duplicate (region,date) pairs:", [k for k, v in c.items() if v > 1][:20])
open("calib_regions.txt", "w").write("\n".join(out))
