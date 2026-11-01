"""Second probe: exact data layout of the GIC sources (columns, which components are present in the newest rows,
NOAA geoelectric listing and GeoJSON structure). Writes probe2.json. Diagnostic only."""
import json, re, urllib.request
from datetime import datetime, timedelta, timezone

UA = "solar-alerts-rss-probe/1.0 (+https://github.com/SBDSBKitchener/SolarAlert)"
now = datetime.now(timezone.utc)
day = now.strftime("%Y-%m-%d")


def get(url, n=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def gin(code):
    return ("https://imag-data.bgs.ac.uk/GIN_V1/GINServices?Request=GetData&format=iaga2002&testObsys=0"
            f"&observatoryIagaCode={code}&samplesPerDay=minute&publicationState=Best%20available"
            f"&dataStartDate={day}&dataDuration=1")


def usgs(code, typ="variation"):
    return (f"https://geomag.usgs.gov/ws/data/?id={code}&type={typ}&format=iaga2002&sampling_period=60"
            f"&starttime={(now - timedelta(hours=3)):%Y-%m-%dT%H:%M:%SZ}&endtime={now:%Y-%m-%dT%H:%M:%SZ}")


out = {"generated": now.isoformat(), "magnetometers": {}}
cands = {f"usgs:{c}:{t}": usgs(c, t) for c in ("BOU", "FRD") for t in ("variation", "adjusted", "quasi-definitive")}
cands.update({f"gin:{c}": gin(c) for c in ("NUR", "NGK", "FUR", "HRN", "BEL", "KAK", "MMB", "KNY", "CNB", "CTA", "KDU", "EYR", "VSS")})
for name, url in cands.items():
    try:
        body = get(url)
    except Exception as e:
        out["magnetometers"][name] = {"error": str(e)[:200]}
        continue
    lines = body.splitlines()
    hdr = next((l for l in lines if l.startswith("DATE")), None)
    rows = [l for l in lines if re.match(r"\d{4}-\d{2}-\d{2} ", l)]
    full = [l for l in rows if all(float(x) < 88888 for x in l.split()[3:5])]      # first two components present
    out["magnetometers"][name] = {"columns": hdr, "rows": len(rows), "rows_with_first_two": len(full),
                                  "newest_row": rows[-1] if rows else None,
                                  "newest_full_rows": full[-3:], "interval_hint": rows[:2]}
# NOAA geoelectric
base = "https://services.swpc.noaa.gov/json/lists/rgeojson/US-Canada-1D/"
try:
    listing = get(base)
    out["noaa_listing_head"] = listing[:600]
    names = sorted(set(re.findall(r'href="([^"]+\.json)"', listing)) | set(re.findall(r'>([^<>]+\.(?:json|geojson))<', listing)))
    out["noaa_files"] = len(names)
    out["noaa_last_names"] = names[-5:]
    if names:
        body = get(base + names[-1])
        out["noaa_newest_url"] = base + names[-1]
        out["noaa_newest_bytes"] = len(body)
        out["noaa_newest_head"] = body[:900]
        try:
            d = json.loads(body)
            out["noaa_keys"] = list(d)[:10] if isinstance(d, dict) else type(d).__name__
            feats = d.get("features", []) if isinstance(d, dict) else []
            out["noaa_features"] = len(feats)
            out["noaa_feature_sample"] = feats[:2]
        except Exception as e:
            out["noaa_parse_error"] = str(e)[:200]
except Exception as e:
    out["noaa_error"] = str(e)[:300]
json.dump(out, open("probe2.json", "w"), indent=1)
print(json.dumps(out)[:3000])
