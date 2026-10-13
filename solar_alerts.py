#!/usr/bin/env python3
"""Solar & geomagnetic alert RSS generator.

Polls NOAA SWPC and HEK, applies admin-configurable thresholds (config.toml),
stores new alerts in SQLite (for de-duplication) and writes a static RSS 2.0 file.
Requires Python 3.11+ (tomllib). No third-party packages.

    python3 solar_alerts.py --config config.toml            # one cycle (cron / systemd timer)
    python3 solar_alerts.py --config config.toml --daemon   # loop forever
    python3 solar_alerts.py --config config.toml --check-config
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

log = logging.getLogger("solar_alerts")

# --------------------------------------------------------------------------- helpers

CLASS_SCALE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}


def goes_flux(cls):
    """'M5.2' -> 5.2e-5 W/m^2 ; None if not a valid GOES class."""
    m = re.fullmatch(r"\s*([ABCMX])(\d+(?:\.\d+)?)\s*", str(cls or ""), re.I)
    return CLASS_SCALE[m.group(1).upper()] * float(m.group(2)) if m else None


def parse_time(s):
    s = str(s or "").strip().replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_float(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def first_present(row, *keys):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def table_rows(data):
    """SWPC returns either a list of dicts or a list of lists with a header row."""
    if data and isinstance(data[0], list):
        header = data[0]
        return [dict(zip(header, r)) for r in data[1:]]
    return list(data or [])


def fmt_loc(lon, lat):
    ns = "N" if lat >= 0 else "S"
    ew = "W" if lon >= 0 else "E"          # Stonyhurst: positive longitude = west
    return f"{ns}{abs(lat):02.0f}{ew}{abs(lon):02.0f}"


def facing(lon, full_max, partial_max):
    if lon is None:
        return None
    a = abs(lon)
    if a <= full_max:
        return "Earth-facing"
    if a <= partial_max:
        return "partially Earth-facing (near limb)"
    return None


class Fetcher:
    def __init__(self, user_agent, timeout):
        self.ua, self.timeout = user_agent, timeout

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def json(self, url):
        return json.loads(self._get(url))

    def text(self, url):
        return self._get(url)


# --------------------------------------------------------------------------- config

class ConfigError(Exception):
    pass


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


KINDS = {
    "str": lambda v: isinstance(v, str),
    "num": _is_num,
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "class": lambda v: goes_flux(v) is not None,
    "dir": lambda v: v in ("above", "below"),
    "loc": lambda v: v in ("ignore", "alert"),
    "regex": lambda v: bool(re.compile(v)) if isinstance(v, str) else False,
}

THRESHOLD_RULES = ["protons.ge10mev", "protons.ge100mev", "solar_wind.speed",
                   "solar_wind.density", "solar_wind.bt", "solar_wind.bz"]


def _schema():
    s = [("feed.title", "str"), ("feed.description", "str"), ("feed.link", "str"),
         ("feed.self_url", "str"), ("feed.output_path", "str"), ("feed.max_items", "int"),
         ("feed.max_age_days", "num"), ("feed.ttl_minutes", "int"),
         ("runtime.database", "str"), ("runtime.user_agent", "str"),
         ("runtime.http_timeout_seconds", "num"), ("runtime.retention_days", "num"),
         ("hek.url", "str"),
         ("solar_wind.plasma_url", "str"), ("solar_wind.mag_url", "str"),
         ("geomagnetic.coronal_hole.enabled", "bool"), ("geomagnetic.coronal_hole.url", "str"),
         ("geomagnetic.coronal_hole.include_regex", "regex"),
         ("geomagnetic.coronal_hole.exclude_regex", "regex"),
         ("geomagnetic.coronal_hole.cooldown_minutes", "num"),
         ("geomagnetic.storm_forecast.enabled", "bool"),
         ("geomagnetic.storm_forecast.alerts_url", "str"),
         ("geomagnetic.storm_forecast.min_g_level", "int"),
         ("geomagnetic.storm_forecast.max_age_hours", "num"),
         ("geomagnetic.storm_forecast.cooldown_minutes", "num"),
         ("geomagnetic.kp.enabled", "bool"), ("geomagnetic.kp.url", "str"),
         ("geomagnetic.kp.min_kp", "num"), ("geomagnetic.kp.lookback_hours", "num")]
    for sec in ("flares", "filaments"):
        s += [(f"{sec}.enabled", "bool"), (f"{sec}.lookback_hours", "num"),
              (f"{sec}.full_facing_max_longitude", "num"),
              (f"{sec}.partial_facing_max_longitude", "num"),
              (f"{sec}.unknown_location", "loc"), (f"{sec}.link", "str")]
    s.append(("flares.min_class", "class"))
    for r in THRESHOLD_RULES:
        s += [(f"{r}.enabled", "bool"), (f"{r}.threshold", "num"), (f"{r}.direction", "dir"),
              (f"{r}.lookback_minutes", "num"), (f"{r}.cooldown_minutes", "num")]
    for r in ("protons.ge10mev", "protons.ge100mev"):
        s += [(f"{r}.url", "str"), (f"{r}.energy", "str")]
    return s


def _dig(cfg, path):
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def validate_config(cfg):
    errors = []
    for path, kind in _schema():
        try:
            v = _dig(cfg, path)
        except KeyError:
            errors.append(f"missing setting: {path}")
            continue
        try:
            ok = KINDS[kind](v)
        except re.error as e:
            ok, path = False, f"{path} ({e})"
        if not ok:
            errors.append(f"invalid value for {path}: {v!r} (expected {kind})")
    if not errors:
        for sec in ("flares", "filaments"):
            full, part = cfg[sec]["full_facing_max_longitude"], cfg[sec]["partial_facing_max_longitude"]
            if not 0 <= full <= part <= 180:
                errors.append(f"{sec}: need 0 <= full_facing_max_longitude <= partial_facing_max_longitude <= 180")
    if errors:
        raise ConfigError("\n".join(errors))


def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    validate_config(cfg)
    return cfg


# --------------------------------------------------------------------------- model + store

@dataclass
class Alert:
    key: str                     # unique, persistent de-dupe key
    category: str
    title: str
    description: str
    link: str
    event_time: datetime
    group: str | None = None     # threshold/cooldown group
    cooldown_minutes: float = 0
    near_minutes: float = 0      # suppress if same category alerted within +/- this many minutes


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS alerts(
                id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE, category TEXT, title TEXT,
                description TEXT, link TEXT, event_time TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS groups(
                name TEXT PRIMARY KEY, active INTEGER DEFAULT 0, last_alert TEXT);
        """)

    def has_key(self, key):
        return self.db.execute("SELECT 1 FROM alerts WHERE dedupe_key=?", (key,)).fetchone() is not None

    def near(self, category, t, minutes):
        lo, hi = iso(t - timedelta(minutes=minutes)), iso(t + timedelta(minutes=minutes))
        return self.db.execute("SELECT 1 FROM alerts WHERE category=? AND event_time BETWEEN ? AND ?",
                               (category, lo, hi)).fetchone() is not None

    def group(self, name):
        r = self.db.execute("SELECT active,last_alert FROM groups WHERE name=?", (name,)).fetchone()
        return (bool(r[0]), parse_time(r[1])) if r else (False, None)

    def mark_alerted(self, name, now):
        self.db.execute("INSERT INTO groups(name,active,last_alert) VALUES(?,0,?) "
                        "ON CONFLICT(name) DO UPDATE SET last_alert=excluded.last_alert", (name, iso(now)))

    def set_active(self, name, active):
        self.db.execute("INSERT INTO groups(name,active) VALUES(?,?) "
                        "ON CONFLICT(name) DO UPDATE SET active=excluded.active", (name, int(active)))

    def add(self, a, now):
        self.db.execute("INSERT INTO alerts(dedupe_key,category,title,description,link,event_time,created_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (a.key, a.category, a.title, a.description, a.link, iso(a.event_time), iso(now)))

    def recent(self, max_age_days, limit, now):
        cutoff = iso(now - timedelta(days=max_age_days))
        cur = self.db.execute("SELECT dedupe_key,category,title,description,link,event_time,created_at "
                              "FROM alerts WHERE created_at>=? ORDER BY created_at DESC, id DESC LIMIT ?",
                              (cutoff, limit))
        return [dict(zip(("key", "category", "title", "description", "link", "event_time", "created_at"), r))
                for r in cur]

    def prune(self, days, now):
        self.db.execute("DELETE FROM alerts WHERE created_at<?", (iso(now - timedelta(days=days)),))

    def commit(self):
        self.db.commit()


# --------------------------------------------------------------------------- checks
# Each check returns (alerts, conditions). `conditions` maps group -> currently breached?
# so that continuous conditions alert once per episode instead of every poll.

def threshold_alert(rule, rows, field, now, group, category, label, unit, link):
    cutoff = now - timedelta(minutes=rule["lookback_minutes"])
    samples = []
    for r in rows:
        t, v = parse_time(r.get("time_tag")), to_float(r.get(field))
        if t and v is not None and cutoff <= t <= now + timedelta(minutes=5):
            samples.append((t, v))
    if not samples:
        return None, None                                  # no data: leave state unchanged
    above, thr = rule["direction"] == "above", rule["threshold"]
    hits = [s for s in samples if (s[1] > thr if above else s[1] < thr)]
    if not hits:
        return None, False
    t, v = max(hits, key=lambda s: s[1]) if above else min(hits, key=lambda s: s[1])
    latest = max(samples)[1]
    op = ">" if above else "<"
    return Alert(
        key=f"{group}:{iso(t)}", category=category,
        title=f"{label}: {v:g} {unit} (threshold {op} {thr:g})",
        description=f"{label} reached {v:g} {unit} at {t:%Y-%m-%d %H:%M} UTC, beyond the configured "
                    f"threshold ({op} {thr:g} {unit}). Latest reading: {latest:g} {unit}.",
        link=link, event_time=t, group=group, cooldown_minutes=rule["cooldown_minutes"]), True


def hek_events(cfg, fetch, event_type, hours, now):
    params = {"cosec": 2, "cmd": "search", "type": "column", "event_type": event_type,
              "event_starttime": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S"),
              "event_endtime": now.strftime("%Y-%m-%dT%H:%M:%S"),
              "event_coordsys": "helioprojective", "x1": -1200, "x2": 1200, "y1": -1200, "y2": 1200,
              "result_limit": 200}
    data = fetch.json(cfg["hek"]["url"] + "?" + urllib.parse.urlencode(params))
    return [e for e in data.get("result", []) if e.get("event_type", event_type) == event_type]


def _hek_position(ev, c):
    """-> (lon, lat, facing_label|None, include?)"""
    lon, lat = to_float(ev.get("hgs_x")), to_float(ev.get("hgs_y"))
    if lon is None or lat is None:
        return None, None, None, c["unknown_location"] == "alert"
    label = facing(lon, c["full_facing_max_longitude"], c["partial_facing_max_longitude"])
    return lon, lat, label, label is not None


def check_flares(cfg, fetch, now):
    c = cfg["flares"]
    if not c["enabled"]:
        return [], {}
    min_flux, alerts = goes_flux(c["min_class"]), []
    for ev in hek_events(cfg, fetch, "FL", c["lookback_hours"], now):
        cls = ev.get("fl_goescls")
        flux = goes_flux(cls)
        if flux is None or flux < min_flux:
            continue
        lon, lat, label, include = _hek_position(ev, c)
        if not include:
            continue
        t = parse_time(ev.get("event_peaktime")) or parse_time(ev.get("event_starttime")) or now
        where = f"{fmt_loc(lon, lat)}, {label}" if lon is not None else "location not yet reported"
        alerts.append(Alert(
            key=f"flare:{ev.get('kb_archivid') or ev.get('ivorn') or iso(t)}", category="Solar flare",
            title=f"{cls.strip().upper()} solar flare ({where})",
            description=f"A {cls.strip().upper()} class solar flare peaked at {t:%Y-%m-%d %H:%M} UTC. "
                        f"Position: {where}. Reported by: {ev.get('frm_name', 'unknown')}.",
            link=c["link"], event_time=t, near_minutes=15))
    return alerts, {}


def check_filaments(cfg, fetch, now):
    c = cfg["filaments"]
    if not c["enabled"]:
        return [], {}
    alerts = []
    for ev in hek_events(cfg, fetch, "FE", c["lookback_hours"], now):
        lon, lat, label, include = _hek_position(ev, c)
        if not include:
            continue
        t = parse_time(ev.get("event_starttime")) or now
        where = f"{fmt_loc(lon, lat)}, {label}" if lon is not None else "location not yet reported"
        alerts.append(Alert(
            key=f"filament:{ev.get('kb_archivid') or ev.get('ivorn') or iso(t)}", category="Filament eruption",
            title=f"Filament eruption ({where})",
            description=f"A filament eruption began at {t:%Y-%m-%d %H:%M} UTC. Position: {where}. "
                        f"Reported by: {ev.get('frm_name', 'unknown')}. A CME may follow; check coronagraph imagery.",
            link=c["link"], event_time=t, near_minutes=30))
    return alerts, {}


def check_coronal_hole(cfg, fetch, now):
    c = cfg["geomagnetic"]["coronal_hole"]
    if not c["enabled"]:
        return [], {}
    text = fetch.text(c["url"])
    m = re.search(r":Issued:\s*(\d{4} \w{3} \d{1,2} \d{4}) UTC", text)
    issued = datetime.strptime(m.group(1), "%Y %b %d %H%M").replace(tzinfo=timezone.utc) if m else now
    inc, exc = re.compile(c["include_regex"], re.I), re.compile(c["exclude_regex"], re.I)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    hits = [s for s in sentences if inc.search(s) and not exc.search(s)]
    if not hits:
        return [], {}
    return [Alert(
        key=f"coronal_hole:{iso(issued)}", category="Geomagnetic forecast",
        title="Coronal hole stream forecast to impact Earth",
        description="SWPC forecast discussion (issued %s UTC): %s" % (issued.strftime("%Y-%m-%d %H:%M"),
                                                                    " ".join(hits[:3])),
        link="https://www.swpc.noaa.gov/products/forecast-discussion", event_time=issued,
        group="coronal_hole", cooldown_minutes=c["cooldown_minutes"])], {}


WATCH_RE = re.compile(r"Geomagnetic Storm Category G(\d)\s+Predicted", re.I)
WARN_RE = re.compile(r"WARNING:\s*Geomagnetic K-index of (\d)\s+expected", re.I)


def check_storm_forecast(cfg, fetch, now):
    c = cfg["geomagnetic"]["storm_forecast"]
    if not c["enabled"]:
        return [], {}
    best = None
    for msg in fetch.json(c["alerts_url"]):
        t = parse_time(msg.get("issue_datetime"))
        if not t or now - t > timedelta(hours=c["max_age_hours"]):
            continue
        text = msg.get("message", "")
        level = max([int(x) for x in WATCH_RE.findall(text)] +
                    [int(x) - 4 for x in WARN_RE.findall(text)] + [0])    # K8 = G4, K9 = G5
        if level >= c["min_g_level"] and (best is None or t > best[0]):
            best = (t, level, text)
    if not best:
        return [], {}
    t, level, text = best
    return [Alert(
        key=f"storm_forecast:{iso(t)}", category="Geomagnetic forecast",
        title=f"G{level} geomagnetic storm forecast",
        description=f"SWPC issued {t:%Y-%m-%d %H:%M} UTC: " + " ".join(text.split())[:900],
        link="https://www.swpc.noaa.gov/products/alerts-watches-and-warnings", event_time=t,
        group="storm_forecast", cooldown_minutes=c["cooldown_minutes"])], {}


def check_kp(cfg, fetch, now):
    c = cfg["geomagnetic"]["kp"]
    if not c["enabled"]:
        return [], {}
    cutoff, alerts = now - timedelta(hours=c["lookback_hours"]), []
    for r in table_rows(fetch.json(c["url"])):
        t, kp = parse_time(r.get("time_tag")), to_float(first_present(r, "Kp", "kp_index", "kp"))
        if t and kp is not None and t >= cutoff and kp >= c["min_kp"]:
            g = max(1, int(kp + 0.5) - 4)
            alerts.append(Alert(
                key=f"kp:{iso(t)}", category="Geomagnetic storm",
                title=f"Kp index {kp:g} observed (about G{g})",
                description=f"Planetary Kp reached {kp:g} for the 3-hour period starting {t:%Y-%m-%d %H:%M} UTC "
                            f"(alert threshold {c['min_kp']:g}).",
                link="https://www.swpc.noaa.gov/products/planetary-k-index", event_time=t))
    return alerts, {}


def check_protons(cfg, fetch, now):
    alerts, conds, cache = [], {}, {}
    for name in ("ge10mev", "ge100mev"):
        rule = cfg["protons"][name]
        if not rule["enabled"]:
            continue
        if rule["url"] not in cache:
            cache[rule["url"]] = table_rows(fetch.json(rule["url"]))
        want = re.sub(r"\s", "", rule["energy"]).lower()
        rows = [r for r in cache[rule["url"]] if re.sub(r"\s", "", str(r.get("energy", ""))).lower() == want]
        group = f"protons_{name}"
        a, breached = threshold_alert(rule, rows, "flux", now, group, "Solar radiation storm",
                                      f"Proton flux {rule['energy']}", "pfu",
                                      "https://www.swpc.noaa.gov/products/goes-proton-flux")
        if a:
            alerts.append(a)
        if breached is not None:
            conds[group] = breached
    return alerts, conds


SW_FIELDS = {"speed": ("plasma_url", "speed", "Solar wind speed", "km/s"),
             "density": ("plasma_url", "density", "Solar wind density", "p/cm³"),
             "bt": ("mag_url", "bt", "IMF total field (Bt)", "nT"),
             "bz": ("mag_url", "bz_gsm", "IMF Bz (GSM)", "nT")}


def check_solar_wind(cfg, fetch, now):
    sw, alerts, conds, cache = cfg["solar_wind"], [], {}, {}
    for name, (url_key, field, label, unit) in SW_FIELDS.items():
        rule = sw[name]
        if not rule["enabled"]:
            continue
        try:
            if sw[url_key] not in cache:
                cache[sw[url_key]] = table_rows(fetch.json(sw[url_key]))
        except Exception as e:                              # one feed failing must not hide the others
            log.warning("solar wind %s: fetch failed: %s", name, e)
            continue
        group = f"solar_wind_{name}"
        a, breached = threshold_alert(rule, cache[sw[url_key]], field, now, group, "Solar wind", label, unit,
                                      "https://www.swpc.noaa.gov/products/real-time-solar-wind")
        if a:
            alerts.append(a)
        if breached is not None:
            conds[group] = breached
    return alerts, conds


CHECKS = [check_flares, check_filaments, check_coronal_hole, check_storm_forecast,
          check_kp, check_protons, check_solar_wind]


# --------------------------------------------------------------------------- pipeline + feed

def process(store, alerts, conditions, now):
    new = []
    for a in sorted(alerts, key=lambda x: x.event_time):
        if store.has_key(a.key):
            continue
        if a.near_minutes and store.near(a.category, a.event_time, a.near_minutes):
            continue
        if a.group:
            active, last = store.group(a.group)
            if active:
                continue
            if a.cooldown_minutes and last and now - last < timedelta(minutes=a.cooldown_minutes):
                continue
            store.mark_alerted(a.group, now)
        store.add(a, now)
        new.append(a)
    for g, breached in conditions.items():
        store.set_active(g, breached)
    store.commit()
    return new


def render_feed(cfg, items):
    f = cfg["feed"]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', "<channel>",
           f"<title>{escape(f['title'])}</title>", f"<link>{escape(f['link'])}</link>",
           f"<description>{escape(f['description'])}</description>", "<language>en</language>",
           f"<ttl>{f['ttl_minutes']}</ttl>",
           f'<atom:link href="{escape(f["self_url"])}" rel="self" type="application/rss+xml"/>']
    if items:
        out.append(f"<lastBuildDate>{format_datetime(parse_time(items[0]['created_at']))}</lastBuildDate>")
    for it in items:
        out += ["<item>", f"<title>{escape(it['title'])}</title>", f"<link>{escape(it['link'])}</link>",
                f'<guid isPermaLink="false">{escape(it["key"])}</guid>',
                f"<pubDate>{format_datetime(parse_time(it['event_time']))}</pubDate>",
                f"<category>{escape(it['category'])}</category>",
                f"<description>{escape(it['description'])}</description>", "</item>"]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def write_atomic(path, content):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == content:
                return
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def run_cycle(cfg, fetch, store, now=None):
    now = now or datetime.now(timezone.utc)
    alerts, conds = [], {}
    for check in CHECKS:
        try:
            a, c = check(cfg, fetch, now)
            alerts += a
            conds.update(c)
        except Exception as e:
            log.warning("%s failed: %s", check.__name__, e)
    new = process(store, alerts, conds, now)
    store.prune(cfg["runtime"]["retention_days"], now)
    store.commit()
    items = store.recent(cfg["feed"]["max_age_days"], cfg["feed"]["max_items"], now)
    write_atomic(cfg["feed"]["output_path"], render_feed(cfg, items))
    for a in new:
        log.info("NEW ALERT: %s", a.title)
    return new


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.toml")
    p.add_argument("--daemon", action="store_true", help="loop forever instead of one cycle")
    p.add_argument("--interval", type=int, default=300, help="seconds between cycles with --daemon")
    p.add_argument("--check-config", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"Config problem:\n{e}", file=sys.stderr)
        return 2
    if args.check_config:
        print("Config OK")
        return 0
    while True:
        try:
            cfg = load_config(args.config)                  # pick up admin edits each cycle
            fetch = Fetcher(cfg["runtime"]["user_agent"], cfg["runtime"]["http_timeout_seconds"])
            run_cycle(cfg, fetch, Store(cfg["runtime"]["database"]))
        except Exception as e:
            log.error("cycle failed: %s", e)
            if not args.daemon:
                return 1
        if not args.daemon:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
