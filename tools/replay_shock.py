"""Replays the PRODUCTION shock detector (solar_alerts.check_shock) on historic OMNI minutes, as if they were the
live rtsw feeds, at several moments around each event. Diagnostic only. Output: calib.txt."""
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ".")
import solar_alerts as sa
src = open("tools/omni_shocks.py").read()
exec(src[:src.index('out = {"events"')])

cfg = sa.load_config("config.toml")
EVENTS = [("May 2024 superstorm shock", 2024, "2024-05-10 17:07"), ("31 Dec 2024 shock", 2024, "2024-12-31 16:58"),
          ("Jan 2025 storm shock", 2025, "2025-01-01 17:08"), ("Oct 2024 G4 shock", 2024, "2024-10-10 15:20"),
          ("Nov 2025 shock", 2025, "2025-11-12 00:29")]

class FakeFetch:
    def __init__(self, data): self.data = data
    def json(self, url): return self.data[url]

text = []
data = {y: load(y) for y in (2024, 2025)}
for name, y, when in EVENTS:
    d = data[y]
    t0 = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    i0 = int((t0 - d["start"]).total_seconds() // 60)
    text.append(f"\n== {name} (OMNI step at {when})")
    for lag in (8, 10, 12, 15, 20, 40):
        now = t0 + timedelta(minutes=lag)
        plasma, mag = [], []
        for k in range(i0 + lag - 150, i0 + lag + 1):
            t = (d["start"] + timedelta(minutes=k)).strftime("%Y-%m-%dT%H:%M:%S")
            if d["V"][k] is not None:
                plasma.append({"time_tag": t, "source": "OMNI", "active": True, "proton_speed": d["V"][k],
                               "proton_density": d["N"][k], "proton_temperature": d["T"][k]})
            if d["B"][k] is not None:
                mag.append({"time_tag": t, "source": "OMNI", "active": True, "bt": d["B"][k]})
        fetch = FakeFetch({cfg["solar_wind"]["plasma_url"]: plasma, cfg["solar_wind"]["mag_url"]: mag})
        sa._STATE["rtsw"] = {}
        alerts, _ = sa.check_shock(cfg, fetch, now)
        res = [(a.level, a.title) for a in alerts]
        cand = sa._NOTES["shock"]["candidates"]
        text.append(f"  +{lag:2d} min after step: {res if res else 'no alert'}   candidates={[(c['time'][11:16], c['level'], c['speed_jump'], c['hold_jump'], c['density_ratio'], c['field_ratio'], c['temperature_ratio']) for c in cand]}")
open("calib.txt", "w").write("\n".join(text) + "\n")
json.dump({"text": text}, open("calib.json", "w"))
print("\n".join(text))
