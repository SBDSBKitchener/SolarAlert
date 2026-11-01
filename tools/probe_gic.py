"""One-off probe: which public sources give CURRENT ground magnetic / geoelectric data for each world region?

Runs on a GitHub Actions runner (open internet). Writes probe.json: per source the HTTP status, size, newest
timestamp found, how old that is, and the start of the body. Stdlib only. Not part of the alert feed."""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "solar-alerts-rss-probe/1.0 (+https://github.com/SBDSBKitchener/SolarAlert)"
now = datetime.now(timezone.utc)
day = now.strftime("%Y-%m-%d")


def gin(code, state="Best%20available"):
    return ("https://imag-data.bgs.ac.uk/GIN_V1/GINServices?Request=GetData&format=iaga2002&testObsys=0"
            f"&observatoryIagaCode={code}&samplesPerDay=minute&publicationState={state}"
            f"&dataStartDate={day}&dataDuration=1")


def usgs(code):
    return (f"https://geomag.usgs.gov/ws/data/?id={code}&type=variation&format=iaga2002&sampling_period=60"
            f"&starttime={(now - timedelta(hours=3)):%Y-%m-%dT%H:%M:%SZ}&endtime={now:%Y-%m-%dT%H:%M:%SZ}")


SOURCES = {
    "North America | NOAA geoelectric US-Canada-1D listing": "https://services.swpc.noaa.gov/json/lists/rgeojson/US-Canada-1D/",
    "North America | NOAA geoelectric InterMagEarthScope listing": "https://services.swpc.noaa.gov/json/lists/rgeojson/InterMagEarthScope/",
    "North America | USGS Boulder (BOU)": usgs("BOU"),
    "North America | USGS Fredericksburg (FRD)": usgs("FRD"),
    "UK | BGS public GIC page": "http://www.geomag.bgs.ac.uk/gicpublic",
    "UK | BGS GIC services page": "https://eap.bgs.ac.uk/data_service/space_weather/gic_services.html",
}
for region, codes in {
        "Europe": ["HAD", "LER", "ESK", "NUR", "NGK", "FUR", "HRN", "BEL"],
        "Russia": ["MOS", "IRT", "NVS", "SPG", "KHB"],
        "Asia": ["KAK", "MMB", "BJI", "KNY", "KDU"],
        "Australasia": ["CNB", "CTA", "EYR", "API"],
        "South America": ["VSS", "TRW", "PIL", "HUA"]}.items():
    for c in codes:
        SOURCES[f"{region} | INTERMAGNET via BGS GIN best-available ({c})"] = gin(c)
        SOURCES[f"{region} | INTERMAGNET via BGS GIN reported ({c})"] = gin(c, "Reported")

TS = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")


def probe(name, url):
    out = {"url": url}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            out.update(status=r.status, bytes=len(body), content_type=r.headers.get("Content-Type"))
    except Exception as e:
        out.update(status=None, error=f"{type(e).__name__}: {e}"[:300])
        return out
    # IAGA-2002 rows: "date time doy V1 V2 V3 V4"; 99999 / 88888 mark missing values. Newest row with real data:
    rows = [l.split() for l in body.splitlines() if TS.match(l)]
    valid = [r for r in rows if any(x.replace(".", "").lstrip("-").isdigit() and abs(float(x)) < 88888 for x in r[3:])]
    out.update(rows=len(rows), rows_with_data=len(valid))
    if valid:
        newest = datetime.strptime(f"{valid[-1][0]} {valid[-1][1][:8]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.update(newest=newest.isoformat(), age_minutes=round((now - newest).total_seconds() / 60, 1))
    elif rows:
        out["note"] = "file has rows but every value is missing (99999)"
    out["head"] = body[:240].replace("\n", " | ")
    out["tail"] = body[-160:].replace("\n", " | ")
    return out


results = {n: probe(n, u) for n, u in SOURCES.items()}
json.dump({"generated": now.isoformat(), "results": results}, open("probe.json", "w"), indent=1)
for n, r in results.items():
    print(f"{n:70s} rows_with_data={r.get('rows_with_data')} status={r.get('status')} age_min={r.get('age_minutes')} bytes={r.get('bytes')} {r.get('error','')}")
