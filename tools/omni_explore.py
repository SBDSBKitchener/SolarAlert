"""Diagnostic: confirm the NASA OMNI 1-minute file layout and availability before calibrating the shock detector."""
import json, urllib.request
BASE = "https://spdf.gsfc.nasa.gov/pub/data/omni/high_res_omni/"
UA = "solar-alerts-rss-probe/1.0 (+https://github.com/SBDSBKitchener/SolarAlert)"
out = {}
def get(url, n=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if n is None else r.read(n)
try:
    out["format_doc"] = get(BASE + "hroformat.txt").decode("latin1")[:6000]
except Exception as e:
    out["format_doc_error"] = str(e)
for y in (1989, 2024, 2025):
    name = f"omni_min{y}.asc"
    try:
        req = urllib.request.Request(BASE + name, headers={"User-Agent": UA, "Range": "bytes=0-600"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out[name] = {"status": r.status, "length": r.headers.get("Content-Range") or r.headers.get("Content-Length"),
                         "head": r.read().decode("latin1")}
    except Exception as e:
        out[name] = {"error": str(e)[:200]}
try:
    listing = get(BASE).decode("latin1")
    out["listing_has"] = [n for n in ("omni_min1989.asc", "omni_min2024.asc", "omni_min2025.asc", "omni_min2026.asc") if n in listing]
except Exception as e:
    out["listing_error"] = str(e)[:200]
json.dump(out, open("calib.json", "w"), indent=1)
print(json.dumps(out)[:2500])
