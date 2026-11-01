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
import statistics
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
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
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
        return "partially Earth-facing (near the Sun's edge)"
    return None


def summarize(data):
    """Small description of a fetched JSON document, for health.json."""
    if isinstance(data, dict):
        rows = data.get("result") if isinstance(data.get("result"), list) else [data]
        cols = None
    else:
        rows = table_rows(data)
        cols = data[0] if data and isinstance(data[0], list) else None
    rows = [r for r in rows if isinstance(r, dict)]
    out = {"rows": len(rows)}
    if cols:
        out["columns"] = cols
    times = []
    for r in rows:
        t = parse_time(first_present(r, "time_tag", "observed_date", "event_starttime", "issue_datetime"))
        if t:
            times.append((t, r))
    if times:
        out["oldest"], out["newest"] = iso(min(times, key=lambda x: x[0])[0]), iso(max(times, key=lambda x: x[0])[0])
        out["newest_row"] = dict(list(max(times, key=lambda x: x[0])[1].items())[:14])
    energies = sorted({str(r["energy"]) for r in rows if "energy" in r})
    if energies:
        out["energies"] = energies
    return out


class Fetcher:
    def __init__(self, user_agent, timeout):
        self.ua, self.timeout, self.log = user_agent, timeout, []

    def _get(self, url):
        entry = {"url": url}
        self.log.append(entry)
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.ua})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except Exception as e:
            entry.update(ok=False, error=f"{type(e).__name__}: {e}")
            raise
        entry.update(ok=True, bytes=len(body), seconds=round(time.time() - t0, 2))
        return body

    def json(self, url):
        body = self._get(url)
        try:
            data = json.loads(body)
            self.log[-1].update(summarize(data))
        except Exception as e:
            self.log[-1].update(ok=False, error=f"{type(e).__name__}: {e}")
            raise
        return data

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
    "strs": lambda v: isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) and x.strip() for x in v),
    "strs0": lambda v: isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v),   # may be empty
    "mcintosh": lambda v: isinstance(v, list) and all(
        isinstance(x, str) and re.fullmatch(r"[ABCDEFH][XRSAHK][XOIC]", x.strip(), re.I) for x in v),
    "level": lambda v: v in ("off",) + LEVELS,
    "stations": lambda v: isinstance(v, list) and bool(v) and all(
        isinstance(x, dict) and isinstance(x.get("code"), str) and x["code"].strip()
        and isinstance(x.get("region"), str) and x["region"].strip() and x.get("source") in ("gin", "usgs") for x in v),
    "classoff": lambda v: goes_flux(v) is not None or (isinstance(v, str) and v.strip().lower() == "off"),
    "nonneg": lambda v: _is_num(v) and v >= 0,
    "posint": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
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
         ("solar_wind.plasma_url", "str"), ("solar_wind.mag_url", "str"), ("solar_wind.only_active", "bool"),
         ("geomagnetic.coronal_hole.enabled", "bool"), ("geomagnetic.coronal_hole.url", "str"),
         ("geomagnetic.coronal_hole.include_regex", "regex"),
         ("geomagnetic.coronal_hole.exclude_regex", "regex"),
         ("geomagnetic.coronal_hole.cooldown_minutes", "num"), ("geomagnetic.coronal_hole.min_g_level", "int"),
         ("geomagnetic.storm_forecast.enabled", "bool"),
         ("geomagnetic.storm_forecast.alerts_url", "str"),
         ("geomagnetic.storm_forecast.min_g_level", "int"),
         ("geomagnetic.storm_forecast.max_age_hours", "num"),
         ("geomagnetic.storm_forecast.cooldown_minutes", "num"),
         ("geomagnetic.kp.enabled", "bool"), ("geomagnetic.kp.url", "str"),
         ("geomagnetic.kp.min_kp", "num"), ("geomagnetic.kp.lookback_hours", "num")]
    s += [("geomagnetic.coupling.enabled", "bool"), ("geomagnetic.coupling.kp_url", "str"),
          ("geomagnetic.coupling.min_kp", "num"), ("geomagnetic.coupling.kp_valid_hours", "num"),
          ("geomagnetic.coupling.clock_angle_min", "num"), ("geomagnetic.coupling.clock_angle_max", "num"),
          ("geomagnetic.coupling.smoothing_window", "posint"), ("geomagnetic.coupling.min_minutes", "posint"),
          ("geomagnetic.coupling.lookback_minutes", "posint"), ("geomagnetic.coupling.cooldown_minutes", "nonneg"),
          ("geomagnetic.coupling.level", "level"), ("geomagnetic.coupling.link", "str")]
    for sec in ("flares", "filaments"):
        s += [(f"{sec}.enabled", "bool"), (f"{sec}.lookback_hours", "num"),
              (f"{sec}.full_facing_max_longitude", "num"),
              (f"{sec}.partial_facing_max_longitude", "num"),
              (f"{sec}.unknown_location", "loc"), (f"{sec}.link", "str")]
    s.append(("flares.long_duration_min_minutes", "nonneg"))
    for kind_ in ("impulsive", "long_duration"):
        s += [(f"flares.{kind_}.warning_min_class", "classoff"), (f"flares.{kind_}.alert_min_class", "classoff")]
    s += [("sunspots.enabled", "bool"), ("sunspots.url", "str"), ("sunspots.dangerous_classes", "strs0"),
          ("sunspots.max_longitude", "num"), ("sunspots.max_age_days", "nonneg"),
          ("sunspots.cooldown_minutes", "nonneg"), ("sunspots.link", "str"),
          ("sunspots.levels.class", "level"), ("sunspots.levels.growth", "level"),
          ("sunspots.levels.xprob", "level"), ("sunspots.levels.flares", "level"), ("sunspots.mcintosh_classes", "mcintosh"), ("sunspots.growth_window_days", "posint"),
          ("sunspots.growth_area_min_increase", "nonneg"), ("sunspots.growth_spots_min_increase", "nonneg"),
          ("sunspots.x_flare_probability_min", "nonneg"), ("sunspots.flare_history_min_events", "nonneg")]
    s += [("solar_wind.shock.enabled", "bool"), ("solar_wind.shock.lookback_minutes", "posint"),
          ("solar_wind.shock.hold_fraction", "nonneg"), ("solar_wind.shock.hold_min_readings", "posint"),
          ("solar_wind.shock.min_density_ratio", "nonneg"), ("solar_wind.shock.distance_km", "num"),
          ("solar_wind.shock.link", "str")]
    for lv_ in ("warning", "alert"):
        s += [(f"solar_wind.shock.{lv_}.speed_jump", "nonneg"), (f"solar_wind.shock.{lv_}.density_ratio", "nonneg"),
              (f"solar_wind.shock.{lv_}.field_ratio", "nonneg"), (f"solar_wind.shock.{lv_}.temperature_ratio", "nonneg"),
              (f"solar_wind.shock.{lv_}.min_met", "posint")]
    s += [("gic.enabled", "bool"), ("gic.cooldown_minutes", "nonneg"), ("gic.link", "str")]
    s += [(f"gic.dbdt.{k}", kind) for k, kind in (
        ("enabled", "bool"), ("gin_url", "str"), ("usgs_url", "str"), ("lookback_minutes", "posint"),
        ("smoothing_window", "posint"), ("max_plausible", "nonneg"), ("max_station_age_minutes", "posint"),
        ("warning_threshold", "num"), ("alert_threshold", "num"), ("stations", "stations"))]
    s += [(f"gic.geoelectric.{k}", kind) for k, kind in (
        ("enabled", "bool"), ("listing_url", "str"), ("lookback_minutes", "posint"), ("max_files_per_run", "posint"),
        ("min_points", "posint"), ("max_nearest_station_km", "nonneg"),
        ("warning_threshold", "num"), ("alert_threshold", "num"))]
    for r in ("speed", "density", "bt", "bz"):
        s += [(f"solar_wind.{r}.field", "str"), (f"solar_wind.{r}.smoothing_window", "posint"),
              (f"solar_wind.{r}.max_plausible", "nonneg")]
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
        for kind_ in ("impulsive", "long_duration"):
            w, a_ = goes_flux(cfg["flares"][kind_]["warning_min_class"]), goes_flux(cfg["flares"][kind_]["alert_min_class"])
            if w is not None and a_ is not None and w > a_:
                errors.append(f"flares.{kind_}: warning_min_class must not be higher than alert_min_class")
        sh = cfg["solar_wind"]["shock"]
        if sh["warning"]["speed_jump"] and sh["alert"]["speed_jump"] and sh["warning"]["speed_jump"] > sh["alert"]["speed_jump"]:
            errors.append("solar_wind.shock: warning.speed_jump must not be higher than alert.speed_jump")
        for lv_ in ("warning", "alert"):
            if sh[lv_]["min_met"] > 3:
                errors.append(f"solar_wind.shock.{lv_}.min_met: at most 3 (density, field, temperature)")
        for kind_ in ("dbdt", "geoelectric"):
            if cfg["gic"][kind_]["warning_threshold"] > cfg["gic"][kind_]["alert_threshold"]:
                errors.append(f"gic.{kind_}: warning_threshold must not be higher than alert_threshold")
        if not 0 <= cfg["sunspots"]["max_longitude"] <= 180:
            errors.append("sunspots: need 0 <= max_longitude <= 180")
    if errors:
        raise ConfigError("\n".join(errors))


def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    validate_config(cfg)
    return cfg


# --------------------------------------------------------------------------- model + store

LEVELS = ("watch", "warning", "alert")           # lowest to highest
LEVEL_LABEL = {"watch": "WATCH", "warning": "WARNING", "alert": "ALERT"}


LEVEL_MEANING = {"watch": "WATCH = something worth keeping an eye on",
                 "warning": "WARNING = a significant event is likely or under way",
                 "alert": "ALERT = an extreme event"}
G_NAMES = {1: "Minor", 2: "Moderate", 3: "Strong", 4: "Severe", 5: "Extreme"}
G_EFFECTS = {1: "Weak power grid fluctuations are possible and aurora may reach high latitudes.",
             2: "Aurora may be seen at about 55 degrees geomagnetic latitude and high-latitude power systems may see voltage alarms.",
             3: "Aurora may be seen at about 50 degrees geomagnetic latitude; satellites, GPS and radio may be affected and power systems may need voltage corrections.",
             4: "Aurora may be seen as far south as about 45 degrees geomagnetic latitude; power grids, satellite navigation (GPS) and radio can be disrupted.",
             5: "Aurora may be seen at very low latitudes; widespread power grid problems, satellite damage and radio and GPS outages are possible."}


def g_text(g):
    g = max(1, min(5, int(g)))
    return f"{G_NAMES[g]} (G{g})"


def an(word):
    """'a' or 'an' for flare classes such as X2.1 or M6.0 (both start with a vowel sound)."""
    return "an" if word[:1].upper() in "MX" else "a"


STATION_NAMES = {"BOU": "Boulder, Colorado, USA", "FRD": "Fredericksburg, Virginia, USA",
                 "NUR": "Nurmijarvi, Finland", "NGK": "Niemegk, Germany", "FUR": "Fuerstenfeldbruck, Germany",
                 "HRN": "Hornsund, Svalbard, Norway", "BEL": "Belsk, Poland", "KAK": "Kakioka, Japan",
                 "MMB": "Memambetsu, Japan", "KNY": "Kanoya, Japan", "CNB": "Canberra, Australia",
                 "CTA": "Charters Towers, Australia", "KDU": "Kakadu, Australia", "EYR": "Eyrewell, New Zealand",
                 "VSS": "Vassouras, Brazil"}


def station_text(code):
    return f"{STATION_NAMES[code]} ({code})" if code in STATION_NAMES else f"station {code}"


def level_rank(level):
    return LEVELS.index(level) if level in LEVELS else -1


def top_level(levels):
    levels = [x for x in levels if x in LEVELS]
    return max(levels, key=level_rank) if levels else None


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
    attrs: tuple = ()            # attribute codes met now; lets later runs announce newly met ones
    on_update: object = None     # callable(newly_met_codes, already_announced) -> (title, description, level)
    level: str = ""              # watch | warning | alert (shown in the title and as an extra feed category)
    update_only: bool = False    # may only announce changes to an episode that is already open, never start one


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS alerts(
                id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE, category TEXT, title TEXT,
                description TEXT, link TEXT, event_time TEXT, created_at TEXT, level TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS groups(
                name TEXT PRIMARY KEY, active INTEGER DEFAULT 0, last_alert TEXT);
            CREATE TABLE IF NOT EXISTS reported(
                grp TEXT, attr TEXT, PRIMARY KEY(grp, attr));
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        """)
        if "level" not in {r[1] for r in self.db.execute("PRAGMA table_info(alerts)")}:
            self.db.execute("ALTER TABLE alerts ADD COLUMN level TEXT DEFAULT ''")

    def get_kv(self, key):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return r[0] if r else None

    def set_kv(self, key, value):
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))

    def has_key(self, key):
        return self.db.execute("SELECT 1 FROM alerts WHERE dedupe_key=?", (key,)).fetchone() is not None

    def near(self, category, t, minutes):
        lo, hi = iso(t - timedelta(minutes=minutes)), iso(t + timedelta(minutes=minutes))
        return self.db.execute("SELECT 1 FROM alerts WHERE category=? AND event_time BETWEEN ? AND ?",
                               (category, lo, hi)).fetchone() is not None

    def group(self, name):
        r = self.db.execute("SELECT active,last_alert FROM groups WHERE name=?", (name,)).fetchone()
        return (bool(r[0]), parse_time(r[1])) if r else (False, None)

    def reported(self, group):
        return {r[0] for r in self.db.execute("SELECT attr FROM reported WHERE grp=?", (group,))}

    def add_reported(self, group, attrs):
        self.db.executemany("INSERT OR IGNORE INTO reported(grp, attr) VALUES(?,?)", [(group, a) for a in attrs])

    def clear_reported(self, group):
        self.db.execute("DELETE FROM reported WHERE grp=?", (group,))

    def mark_alerted(self, name, now):
        self.db.execute("INSERT INTO groups(name,active,last_alert) VALUES(?,0,?) "
                        "ON CONFLICT(name) DO UPDATE SET last_alert=excluded.last_alert", (name, iso(now)))

    def set_active(self, name, active):
        self.db.execute("INSERT INTO groups(name,active) VALUES(?,?) "
                        "ON CONFLICT(name) DO UPDATE SET active=excluded.active", (name, int(active)))

    def add(self, a, now):
        self.db.execute("INSERT INTO alerts(dedupe_key,category,title,description,link,event_time,created_at,level) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (a.key, a.category, a.title, a.description, a.link, iso(a.event_time), iso(now), a.level or ""))

    def recent(self, max_age_days, limit, now):
        cutoff = iso(now - timedelta(days=max_age_days))
        cur = self.db.execute("SELECT dedupe_key,category,title,description,link,event_time,created_at,level "
                              "FROM alerts WHERE created_at>=? ORDER BY created_at DESC, id DESC LIMIT ?",
                              (cutoff, limit))
        return [dict(zip(("key", "category", "title", "description", "link", "event_time", "created_at", "level"), r))
                for r in cur]

    def prune(self, days, now):
        self.db.execute("DELETE FROM alerts WHERE created_at<?", (iso(now - timedelta(days=days)),))

    def commit(self):
        self.db.commit()


# --------------------------------------------------------------------------- checks
# Each check returns (alerts, conditions). `conditions` maps group -> currently breached?
# so that continuous conditions alert once per episode instead of every poll.

SPIKE_GAP = timedelta(minutes=30)     # breaching samples further apart than this are separate spikes


def threshold_alert(rule, rows, field, now, group, category, label, unit, link, headline=None, explain=""):
    cutoff = now - timedelta(minutes=rule["lookback_minutes"])
    samples = []
    for r in rows:
        t, v = parse_time(r.get("time_tag")), to_float(r.get(field))
        if t and v is not None and cutoff <= t <= now + timedelta(minutes=5):
            samples.append((t, v))
    # Spike filters (solar wind): drop physically implausible readings, then replace each reading by the median
    # of the last smoothing_window readings, so a glitch lasting fewer than half a window cannot cross the threshold.
    cap, window = rule.get("max_plausible", 0), rule.get("smoothing_window", 1)
    dropped = 0
    if cap > 0:
        kept = [x for x in samples if abs(x[1]) <= cap]
        dropped, samples = len(samples) - len(kept), kept
    if window > 1:
        samples.sort()
        # a reading is judged only once a full window of readings exists up to it; earlier ones are unsmoothed-out
        samples = [(samples[i][0], statistics.median(v for _, v in samples[i - window + 1:i + 1]))
                   for i in range(window - 1, len(samples))]
    _NOTES.setdefault("solar_wind_filter", {})[group] = {"discarded_implausible": dropped, "smoothing_window": window,
                                                         "max_plausible": cap or None, "readings_used": len(samples)}
    if not samples:
        return None, None                                  # no data: leave state unchanged
    above, thr = rule["direction"] == "above", rule["threshold"]
    hits = sorted(s for s in samples if (s[1] > thr if above else s[1] < thr))
    if not hits:
        return None, False
    # Group the breaching samples into spikes separated by quiet gaps and report the MOST RECENT
    # spike, keyed by when it started. A bigger earlier spike therefore cannot hide a later one,
    # and the same spike is not reported twice however many runs see it.
    spike = [hits[-1]]
    for h in reversed(hits[:-1]):
        if spike[0][0] - h[0] > SPIKE_GAP:
            break
        spike.insert(0, h)
    start = spike[0][0]
    t, v = max(spike, key=lambda x: x[1]) if above else min(spike, key=lambda x: x[1])
    latest = max(samples)[1]
    still = latest > thr if above else latest < thr       # the episode stays open only while still beyond
    op = ">" if above else "<"
    side = "above" if above else "below"
    return Alert(
        key=f"{group}:{iso(start)}", category=category,
        title=f"{headline or label}: {v:g} {unit}",
        description=f"{label} reached {v:g} {unit} at {t:%Y-%m-%d %H:%M} UTC (it first went {side} the alert "
                    f"level of {thr:g} {unit} at {start:%H:%M} UTC). Latest reading: {latest:g} {unit}. {explain}".strip(),
        link=link, event_time=t, group=group, cooldown_minutes=rule["cooldown_minutes"]), still


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
    if lon is None or lat is None or (lon == 0 and lat == 0):      # SWPC reports 0,0 when it has no position
        return None, None, None, c["unknown_location"] == "alert"
    label = facing(lon, c["full_facing_max_longitude"], c["partial_facing_max_longitude"])
    return lon, lat, label, label is not None


FLARE_TYPE_KEY = {"impulsive": "impulsive", "long-duration": "long_duration"}


def flare_duration_type(ev, now, long_min):
    """-> (type, minutes). type is 'impulsive', 'long-duration' or 'pending' (end not reported yet and
    the flare has not yet lasted long enough to be certain). minutes is the start-to-end duration if known."""
    start, end = parse_time(ev.get("event_starttime")), parse_time(ev.get("event_endtime"))
    if start and end and end > start:
        mins = (end - start).total_seconds() / 60
        return ("long-duration" if mins >= long_min else "impulsive"), mins
    if start and (now - start).total_seconds() / 60 >= long_min:
        return "long-duration", None                        # still going after long_min minutes: certainly long
    return "pending", None


def flare_level(c, flux, ftype):
    """Highest level a flare of this GOES flux reaches under the thresholds for its type. A flare whose type is
    not known yet is assumed impulsive (long-duration thresholds apply only once it is known to be long-duration).
    A class of "off" disables that level."""
    k = FLARE_TYPE_KEY.get(ftype, "impulsive")
    for level in ("alert", "warning"):
        thr = goes_flux(c[k][f"{level}_min_class"])          # None when "off"
        if thr is not None and flux >= thr:
            return level
    return None


def merge_flare_records(events):
    """HEK lists one flare once per source (SWPC, SSW Latest Events, ...), each with different gaps: SWPC often
    has no position (0,0), another may lack the end time or the active region. Fill each record's gaps from the
    other records of the same flare (peak within 15 minutes and the same class)."""
    def peak(e):
        return parse_time(e.get("event_peaktime")) or parse_time(e.get("event_starttime"))

    def has_pos(e):
        x, y = to_float(e.get("hgs_x")), to_float(e.get("hgs_y"))
        return x is not None and y is not None and not (x == 0 and y == 0)

    def has_span(e):
        st, en = parse_time(e.get("event_starttime")), parse_time(e.get("event_endtime"))
        return bool(st and en and en > st)

    def has_ar(e):
        return to_float(e.get("ar_noaanum")) not in (None, 0.0)

    merged = []
    for e in events:
        m, pe = dict(e), peak(e)
        for o in events:
            po = peak(o)
            if o is e or pe is None or po is None or abs((pe - po).total_seconds()) > 900:
                continue
            ce, co = str(e.get("fl_goescls") or "").strip().upper(), str(o.get("fl_goescls") or "").strip().upper()
            if ce and co and ce != co:
                continue
            if not has_span(m) and has_span(o):
                m["event_starttime"], m["event_endtime"] = o["event_starttime"], o["event_endtime"]
            if not has_ar(m) and has_ar(o):
                m["ar_noaanum"] = o["ar_noaanum"]
            if not has_pos(m) and has_pos(o):
                m["hgs_x"], m["hgs_y"] = o["hgs_x"], o["hgs_y"]
        merged.append(m)
    return merged


def check_flares(cfg, fetch, now):
    """Earth-facing flares at WARNING or ALERT level, typed impulsive or long-duration.

    The level comes from the flare's GOES class against the thresholds for its type ([flares.impulsive] and
    [flares.long_duration]). A flare whose type is not yet known is assumed impulsive; long-duration thresholds
    apply only once it is known to be long-duration, at which point a new alert is issued (or an update, if it was
    already reported as impulsive)."""
    c = cfg["flares"]
    if not c["enabled"]:
        return [], {}
    long_min = c["long_duration_min_minutes"]
    events = merge_flare_records(hek_events(cfg, fetch, "FL", c["lookback_hours"], now))
    big = sorted(events, key=lambda e: goes_flux(e.get("fl_goescls")) or 0, reverse=True)[:5]
    _NOTES["flares"] = {
        "events": len(events), "with_class": sum(1 for e in events if goes_flux(e.get("fl_goescls"))),
        "with_position": sum(1 for e in events if to_float(e.get("hgs_x")) not in (None, 0.0)),
        "with_end_time": sum(1 for e in events if parse_time(e.get("event_endtime"))),
        "with_active_region": sum(1 for e in events if to_float(e.get("ar_noaanum")) not in (None, 0.0)),
        "strongest": [{**{k: e.get(k) for k in ("fl_goescls", "hgs_x", "hgs_y", "event_starttime", "event_peaktime",
                                                 "event_endtime", "ar_noaanum", "frm_name")},
                       "type": flare_duration_type(e, now, long_min)[0]} for e in big]}
    alerts, conds = [], {}
    for ev in events:
        cls = str(ev.get("fl_goescls") or "").strip().upper()
        flux = goes_flux(cls)
        if flux is None:
            continue
        ftype, mins = flare_duration_type(ev, now, long_min)
        level = flare_level(c, flux, ftype)
        provisional = flare_level(c, flux, "pending")
        if level is None and not (ftype != "pending" and provisional is not None):
            continue                                                    # below every threshold
        lon, lat, label, include = _hek_position(ev, c)
        if not include:
            continue
        t = parse_time(ev.get("event_peaktime")) or parse_time(ev.get("event_starttime")) or now
        where = f"{fmt_loc(lon, lat)}, {label}" if lon is not None else "location not yet reported"
        ar = to_float(ev.get("ar_noaanum"))
        ar_txt = f" from AR{int(ar)}" if ar not in (None, 0.0) else ""
        kb = ev.get("kb_archivid") or ev.get("ivorn") or iso(t)
        group = f"flare_{kb}"
        start, end = parse_time(ev.get("event_starttime")), parse_time(ev.get("event_endtime"))
        times = f"Start {start:%H:%M}, " if start else ""
        times += f"peak {t:%H:%M}" + (f", end {end:%H:%M}" if end and start and end > start else "") + " UTC"

        cme_note = "a type more often followed by a coronal mass ejection (a huge cloud of solar plasma)"

        def describe_type(ftype=ftype, mins=mins, start=start):
            if ftype == "pending":
                return ("The flare has not ended yet, so its length is not known; until it ends it is treated as a "
                        "short (impulsive) flare.")
            if mins is not None and ftype == "impulsive":
                return (f"It lasted {mins:.0f} minutes, so it counts as a short (impulsive) flare. Flares lasting "
                                        f"{long_min:g} minutes or more are called long-duration and are more often followed by "
                                        f"a coronal mass ejection (a huge cloud of solar plasma).")
            if mins is not None:
                return f"It lasted {mins:.0f} minutes, so it counts as a long-duration flare, {cme_note}."
            elapsed = (now - start).total_seconds() / 60 if start else long_min
            return (f"It has already lasted {elapsed:.0f} minutes and has not ended, so it counts as a "
                    f"long-duration flare, {cme_note}.")

        meaning = {"X": "X is the strongest class of solar flare; flares this size can black out high-frequency radio "
                        "across the whole sunlit side of Earth.",
                   "M": "M is the medium class of solar flare; flares this size can cause brief radio blackouts "
                        "near the poles."}.get(cls[:1], "")

        def updater(fresh, seen, cls=cls, flux=flux, ftype=ftype, level=level, ar_txt=ar_txt, t=t,
                    times=times, describe_type=describe_type):
            before = top_level([flare_level(c, flux, x) for x in seen])
            if level is None:
                head, tail = "UPDATE", (f"It is below the alert thresholds for {ftype} flares, so no alert level "
                                        f"applies any more.")
            else:
                head, tail = LEVEL_LABEL[level], f"Level: {LEVEL_LABEL[level]}."
            if level != before and before:
                tail += f" Previously reported as {LEVEL_LABEL[before]}."
            return (f"{head}: {cls} solar flare{ar_txt} update, now classified {ftype if ftype != 'pending' else 'pending'}",
                    f"The {cls} solar flare{ar_txt} that peaked at {t:%Y-%m-%d %H:%M} UTC has been reclassified. "
                    f"{describe_type()} {times}. {tail}", level or "")

        if level is None:                                               # only to close out a provisional alert
            alerts.append(Alert(key=f"flare:{kb}", category="Solar flare", title="", description="", link=c["link"],
                                event_time=t, group=group, attrs=(ftype,), on_update=updater, update_only=True))
            conds[group] = True
            continue
        type_words = {"impulsive": "impulsive ", "long-duration": "long-duration ", "pending": ""}[ftype]
        pending_note = " (duration type pending)" if ftype == "pending" else ""
        alerts.append(Alert(
            key=f"flare:{kb}", category="Solar flare", level=level,
            title=f"{LEVEL_LABEL[level]}: {cls} {type_words}solar flare{pending_note}{ar_txt} ({where})",
            description=f"{an(cls).capitalize()} {cls} solar flare{ar_txt} (a burst of radiation from the Sun) peaked at "
                        f"{t:%Y-%m-%d %H:%M} UTC. Where on the Sun: {where}. {meaning} {describe_type()} {times}. "
                        f"Level: {LEVEL_LABEL[level]}"
                        + (" (judged by the short-flare thresholds until its length is known)" if ftype == "pending" else "")
                        + f". Reported by: {ev.get('frm_name', 'unknown')}.",
            link=c["link"], event_time=t, near_minutes=15, group=group, cooldown_minutes=0,
            attrs=(ftype,), on_update=updater))
        conds[group] = True
    return alerts, conds


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
            title=f"Solar filament eruption ({where}); a coronal mass ejection may follow",
            description=f"A solar filament (a long strand of cooler gas held above the Sun's surface) began erupting at "
                        f"{t:%Y-%m-%d %H:%M} UTC. Where on the Sun: {where}. Eruptions like this can throw out a coronal "
                        f"mass ejection (a huge cloud of solar plasma). Whether one was launched, and whether it is aimed "
                        f"at Earth, is confirmed from satellite images over the next hours. No alert level applies yet. "
                        f"Reported by: {ev.get('frm_name', 'unknown')}.",
            link=c["link"], event_time=t, near_minutes=30))
    return alerts, {}


_NOTES = {}      # per-cycle debugging detail written to health.json
_STATE = {}      # the open Store, for checks that remember something between runs (set by run_cycle)


def check_coronal_hole(cfg, fetch, now):
    c = cfg["geomagnetic"]["coronal_hole"]
    if not c["enabled"]:
        return [], {}
    text = fetch.text(c["url"])
    m = re.search(r":Issued:\s*(\d{4} \w{3} \d{1,2} \d{4}) UTC", text)
    issued = datetime.strptime(m.group(1), "%Y %b %d %H%M").replace(tzinfo=timezone.utc) if m else now
    inc, exc = re.compile(c["include_regex"], re.I), re.compile(c["exclude_regex"], re.I)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    stream = re.compile(r"coronal hole|CH HSS|high[- ]speed stream|\bHSS\b", re.I)
    g_re = re.compile(r"\bG([1-5])\b")
    hits, notes, level = [], [], 0
    for i, sent in enumerate(sentences):
        if not stream.search(sent):
            continue
        arrival = bool(inc.search(sent)) and not exc.search(sent)
        # Forecast strength: the highest G-level named in the sentence, or failing that in the sentence after it
        # (SWPC often words it "... HSS is expected to arrive. Storms to G2 (Moderate) are likely.").
        g = max([int(x) for x in g_re.findall(sent)] or
                [int(x) for x in g_re.findall(sentences[i + 1] if i + 1 < len(sentences) else "")] or [0])
        ok = arrival and g >= c["min_g_level"]
        notes.append({"alert": ok, "forecast_g_level": g or None, "arrival_wording": arrival, "text": sent[:300]})
        if ok:
            hits.append(sent)
            level = max(level, g)
    _NOTES["coronal_hole"] = {"issued": iso(issued), "stream_sentences": notes[:12]}
    # The episode stays open while a discussion still announces an arrival, so the same
    # stream is not re-announced every 12 hours.
    conds = {"coronal_hole": bool(hits)}
    if not hits:
        return [], conds
    return [Alert(
        key=f"coronal_hole:{iso(issued)}", category="Geomagnetic forecast",
        title=f"Fast solar wind stream from a coronal hole forecast to reach Earth: {g_text(level)} geomagnetic storm",
        description=f"NOAA forecasts a stream of fast solar wind from a coronal hole (a dark opening in the Sun's outer "
                    f"atmosphere) to reach Earth, and says it could drive geomagnetic storms up to {g_text(level)}. "
                    f"{G_EFFECTS[level]} These storms are usually slower to build than those from eruptions. "
                    f"NOAA's wording (forecast discussion issued {issued:%Y-%m-%d %H:%M} UTC): "
                    + " ".join(re.sub(r"^:Issued:.*?UTC\s*", "", h) for h in hits[:3]),
        link="https://www.swpc.noaa.gov/products/forecast-discussion", event_time=issued,
        group="coronal_hole", cooldown_minutes=c["cooldown_minutes"])], conds


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
    body = "\n".join(l for l in text.splitlines()
                     if not re.match(r"\s*(Space Weather Message Code|Serial Number|Issue Time)", l))
    return [Alert(
        key=f"storm_forecast:{iso(t)}", category="Geomagnetic forecast",
        title=f"{g_text(level)} geomagnetic storm forecast",
        description=f"NOAA's Space Weather Prediction Center expects a {g_text(level)} geomagnetic storm (a disturbance "
                    f"of Earth's magnetic field caused by the Sun). {G_EFFECTS[level]} "
                    f"NOAA's message, issued {t:%Y-%m-%d %H:%M} UTC: " + " ".join(body.split())[:900],
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
            g = min(5, max(1, int(kp + 0.5) - 4))
            alerts.append(Alert(
                key=f"kp:{iso(t)}", category="Geomagnetic storm",
                title=f"Geomagnetic storm under way: Kp {kp:g}, {g_text(g)} level",
                description=f"Kp, the standard 0-9 scale of how disturbed Earth's magnetic field is, reached {kp:g} "
                            f"for the 3-hour period starting {t:%Y-%m-%d %H:%M} UTC. That is a {g_text(g)} geomagnetic "
                            f"storm. {G_EFFECTS[min(g, 5)]} (Alert threshold: Kp {c['min_kp']:g}.)",
                link="https://www.swpc.noaa.gov/products/planetary-k-index", event_time=t))
    return alerts, {}


PROTON_EXPLAIN = {
    "ge10mev": "pfu means particles per square centimetre per second per steradian, the unit NOAA uses to count solar "
               "protons passing the GOES satellites. Raised levels can harm satellite electronics and are a radiation "
               "concern for astronauts and for polar flights, and can disturb high-frequency radio at the poles. "
               "On NOAA's S scale, 10 pfu is S1 (Minor), 100 is S2, 1,000 is S3, 10,000 is S4 and 100,000 is S5.",
    "ge100mev": "pfu means particles per square centimetre per second per steradian. Protons of 100 MeV and above are "
                "the most penetrating; a rise means an unusually energetic radiation storm that matters for satellites, "
                "astronauts and high-altitude flights."}


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
                                      "https://www.swpc.noaa.gov/products/goes-proton-flux",
                                      headline=f"Solar radiation storm: high-energy protons ({rule['energy']})",
                                      explain=PROTON_EXPLAIN[name])
        if a:
            alerts.append(a)
        if breached is not None:
            conds[group] = breached
    return alerts, conds


SW_FIELDS = {"speed": ("plasma_url", "Solar wind speed", "km/s"),
             "density": ("plasma_url", "Solar wind density", "p/cm³"),
             "bt": ("mag_url", "IMF total field (Bt)", "nT"),
             "bz": ("mag_url", "IMF Bz (GSM)", "nT")}
SW_PLAIN = {
    "speed": ("Very fast solar wind",
              "The solar wind is the stream of charged particles flowing from the Sun; it normally travels at about "
              "300-500 km/s. A fast wind can drive geomagnetic storms."),
    "density": ("Dense solar wind",
                "Particle density (particles per cubic centimetre) is normally under 10. A dense wind pushes harder on "
                "Earth's magnetic field, which can compress it and make storms stronger."),
    "bt": ("Strong magnetic field in the solar wind (Bt)",
           "This is the total strength of the magnetic field carried by the solar wind (the interplanetary magnetic field, "
           "IMF), in nanotesla (nT); about 5 nT is typical. A strong field means more energy is available to drive "
           "a geomagnetic storm."),
    "bz": ("Solar wind magnetic field pointing strongly south (Bz)",
           "Bz is the north-south part of the magnetic field carried by the solar wind. When it points south (negative) it "
           "links up with Earth's magnetic field and lets solar wind energy in, which is the main driver of geomagnetic "
           "storms; the more negative, the stronger the effect.")}
SW_SOURCE = " These readings come from a NOAA spacecraft about 1.5 million km from Earth, so effects reach Earth in roughly 30-60 minutes."


def active_rows(rows, only_active):
    """SWPC's rtsw files interleave several spacecraft; each row says whether it is the active source."""
    if not only_active or not any("active" in r for r in rows):
        return rows
    chosen = [r for r in rows if r.get("active") is True]
    if not chosen:
        log.warning("solar wind: no row is flagged active; using all spacecraft")
    return chosen or rows


def rtsw_rows(cfg, fetch, url_key):
    """The active-spacecraft rows of one real-time solar wind file, fetched once per cycle and shared by the
    threshold rules and the shock detector."""
    sw = cfg["solar_wind"]
    cache = _STATE.setdefault("rtsw", {})
    key = (sw[url_key], id(fetch))
    if key not in cache:
        cache[key] = active_rows(table_rows(fetch.json(sw[url_key])), sw["only_active"])
    return cache[key]


def check_solar_wind(cfg, fetch, now):
    sw, alerts, conds = cfg["solar_wind"], [], {}
    for name, (url_key, label, unit) in SW_FIELDS.items():
        rule, field = sw[name], sw[name]["field"]
        if not rule["enabled"]:
            continue
        try:
            rows = rtsw_rows(cfg, fetch, url_key)
        except Exception as e:                              # one feed failing must not hide the others
            log.warning("solar wind %s: fetch failed: %s", name, e)
            continue
        group = f"solar_wind_{name}"
        a, breached = threshold_alert(rule, rows, field, now, group, "Solar wind", label, unit,
                                      "https://www.swpc.noaa.gov/products/real-time-solar-wind",
                                      headline=SW_PLAIN[name][0], explain=SW_PLAIN[name][1] + SW_SOURCE)
        if a:
            alerts.append(a)
        if breached is not None:
            conds[group] = breached
    return alerts, conds


SPOT_LOC_RE = re.compile(r"^([NS])(\d{1,2})([EW])(\d{1,3})$", re.I)
HALE_NAMES = {"A": "alpha", "B": "beta", "G": "gamma", "D": "delta"}


def check_sunspots(cfg, fetch, now):
    """Earth-facing sunspot regions that look potentially dangerous.

    A region is flagged when it is within max_longitude of disk centre AND at least one enabled attribute is
    met (none is required on its own). Each attribute has its own level ([sunspots.levels]); the alert takes
    the highest level among the attributes met (default: fast growth = WATCH, X-flare chance = WARNING):
      * magnetic class in dangerous_classes (default BGD)
      * rapid growth: area and/or spot count up by the given amount per day versus the previous record,
        counted only when the region's McIntosh class is in mcintosh_classes
      * NOAA's X-class flare probability >= x_flare_probability_min   (M-class probability is ignored)
      * M/X-class flares already reported for the region (flare_history_min_events)
    A value of 0 (or an empty class list) turns an attribute off. While a region stays flagged, a further
    alert is issued whenever an attribute is met that has not been announced yet.

    Source: NOAA Solar Region Summary (one record per region per day, ~1 day old). Longitude is read from
    the location text (e.g. S15E07) and compared by absolute value, so the E/W sign convention doesn't matter."""
    c = cfg["sunspots"]
    if not c["enabled"]:
        return [], {}
    wanted = {x.strip().upper() for x in c["dangerous_classes"]}
    mcintosh = {x.strip().upper() for x in c["mcintosh_classes"]}
    newest_ok = now - timedelta(days=c["max_age_days"])
    horizon = newest_ok - timedelta(days=c["growth_window_days"])
    history = {}
    for r in fetch.json(c["url"]):
        t, num = parse_time(r.get("observed_date")), r.get("region")
        if t is not None and t >= horizon and num not in (None, ""):
            history.setdefault(num, []).append((t, r))
    alerts, conds, notes = [], {}, []
    for num, recs in sorted(history.items(), key=lambda kv: str(kv[0])):
        recs.sort(key=lambda x: x[0])
        t, r = recs[-1]
        if t < newest_ok:
            continue                                        # no recent record for this region
        loc = str(r.get("location") or "").strip().upper()
        m = SPOT_LOC_RE.match(loc)
        lon = float(m.group(4)) if m else to_float(r.get("longitude"))
        if lon is None:
            continue                                        # position unknown: leave state unchanged
        facing_now = abs(lon) <= c["max_longitude"]
        cls = re.sub(r"\s", "", str(r.get("mag_class") or "")).upper()
        name = "-".join(HALE_NAMES.get(ch, ch) for ch in cls)
        spot_class = str(r.get("spot_class") or "").strip()
        why = []                                            # (code, short label, long explanation)
        if cls and cls in wanted:
            why.append(("class", f"complex magnetic layout, {name}",
                        f"a complex magnetic layout, class {cls} ({name}), the kind most likely to produce major flares"))
        prev = recs[-2] if len(recs) > 1 and (t - recs[-2][0]) <= timedelta(days=c["growth_window_days"]) else None
        growth_class_ok = spot_class.upper() in mcintosh
        if prev and growth_class_ok:
            days = max((t - prev[0]).days, 1)
            a_now, a_prev = to_float(r.get("area")), to_float(prev[1].get("area"))
            n_now, n_prev = to_float(r.get("number_spots")), to_float(prev[1].get("number_spots"))
            da = a_now - a_prev if a_now is not None and a_prev is not None else None
            ds = n_now - n_prev if n_now is not None and n_prev is not None else None
            grew = []
            if c["growth_area_min_increase"] > 0 and da is not None and da / days >= c["growth_area_min_increase"]:
                grew.append(f"area up {da:g} millionths of the Sun's visible half")
            if c["growth_spots_min_increase"] > 0 and ds is not None and ds / days >= c["growth_spots_min_increase"]:
                grew.append(f"{ds:g} more sunspots")
            if grew:
                why.append(("growth", "rapid growth",
                            f"rapid growth ({', '.join(grew)} in {days} day{'s' if days > 1 else ''}; "
                            f"it is a large, complex type of sunspot group, class {spot_class})"))
        xp = to_float(r.get("x_flare_probability"))
        if c["x_flare_probability_min"] > 0 and xp is not None and xp >= c["x_flare_probability_min"]:
            why.append(("xprob", f"{xp:g}% chance of an X-class flare",
                        f"NOAA gives it a {xp:g}% chance of producing an X-class (the strongest) flare in the next 24 hours"))
        nflares = (to_float(r.get("m_xray_events")) or 0) + (to_float(r.get("x_xray_events")) or 0)
        if c["flare_history_min_events"] > 0 and nflares >= c["flare_history_min_events"]:
            why.append(("flares", "has already produced strong flares",
                        f"it has already produced {nflares:g} medium or strong (M or X class) flare{'s' if nflares != 1 else ''}"))
        why = [w for w in why if c["levels"][w[0]] != "off"]          # an attribute set to "off" is ignored
        group = f"sunspot_{num}"
        breached = facing_now and bool(why)
        conds[group] = breached
        if facing_now or why:
            notes.append({"region": num, "location": loc, "mag_class": cls, "spot_class": spot_class,
                          "earth_facing": facing_now, "reasons": [w[1] for w in why], "flagged": breached})
        if not breached:
            continue
        where = loc or f"{abs(lon):g} deg from disk centre"
        extra = []
        if spot_class:
            extra.append(f"Shape class {spot_class}")
        if to_float(r.get("area")) is not None:
            extra.append(f"covers {r['area']} millionths of the Sun's visible half")
        if to_float(r.get("number_spots")) is not None:
            extra.append(f"{r['number_spots']} spots")
        basis = (f"Based on NOAA's region summary for {t:%Y-%m-%d} UTC (updated daily, so up to a day old). "
                 f"Sunspot regions are where solar flares and eruptions come from; because this one faces Earth, "
                 f"anything it throws out could be aimed our way.")

        level = top_level([c["levels"][w[0]] for w in why])

        def updater(fresh, seen, why=why, num=num, where=where, basis=basis, level=level):
            news = [w for w in why if w[0] in fresh]
            before = top_level([c["levels"][x] for x in seen if x in c["levels"]])
            raised = (f" Level raised from {LEVEL_LABEL[before]} to {LEVEL_LABEL[level]}."
                      if before and level_rank(level) > level_rank(before) else f" Level: {LEVEL_LABEL[level]}.")
            return (f"{LEVEL_LABEL[level]}: Sunspot AR{num} update, now also: {'; '.join(w[1] for w in news)}",
                    f"Sunspot region AR{num} (at {where} on the Sun, facing Earth) was already flagged as potentially "
                    f"dangerous and now also has: " + "; ".join(w[2] for w in news) + ". Everything it has now: "
                    + "; ".join(w[2] for w in why) + f".{raised} {basis}", level)

        alerts.append(Alert(
            key=f"sunspot:{num}:{iso(t)}", category="Sunspot", level=level,
            title=f"{LEVEL_LABEL[level]}: Potentially dangerous sunspot AR{num} faces Earth, {where} ({'; '.join(w[1] for w in why)})",
            description=f"Level: {LEVEL_LABEL[level]}. Sunspot region AR{num} (AR means active region; this is NOAA's "
                        f"number for it) is facing Earth, at {where} on the Sun, and has: "
                        + "; ".join(w[2] for w in why) + ". "
                        + ("; ".join(extra) + ". " if extra else "") + basis,
            link=c["link"], event_time=t, group=group, cooldown_minutes=c["cooldown_minutes"],
            attrs=tuple(w[0] for w in why), on_update=updater))
    _NOTES["sunspots"] = notes[:20]
    return alerts, conds


# --------------------------------------------------------------------------- interplanetary shock arrival

def _minute_series(rows, field, cutoff, n):
    vals, srcs = [None] * n, [None] * n
    for r in rows:
        t, v = parse_time(r.get("time_tag")), to_float(r.get(field))
        if t is None or v is None:
            continue
        i = int((t - cutoff).total_seconds() // 60)
        if 0 <= i < n:
            vals[i], srcs[i] = v, r.get("source")
    return vals, srcs


def _wmed(arr, a, b, minimum=3):
    v = [x for x in arr[max(a, 0):max(b, 0)] if x is not None]
    return statistics.median(v) if len(v) >= minimum else None


def _ratio(pair):
    return pair[1] / pair[0] if pair[0] and pair[1] else None


def shock_level(sc, m):
    """'alert', 'warning' or None for a candidate's measured jumps m (speed_jump, hold, density/field/temp ratios)."""
    if m["hold_jump"] is None or m["hold_jump"] < sc["hold_fraction"] * m["speed_jump"]:
        return None                                        # did not persist: not a shock
    if m["density_ratio"] is None or m["density_ratio"] < sc["min_density_ratio"]:
        return None                                        # a fast-forward shock always compresses the plasma
    for level in ("alert", "warning"):
        L = sc[level]
        if not L["speed_jump"] or m["speed_jump"] < L["speed_jump"]:
            continue
        met = sum([m["density_ratio"] >= L["density_ratio"], (m["field_ratio"] or 0) >= L["field_ratio"],
                   (m["temperature_ratio"] or 0) >= L["temperature_ratio"]])
        if met >= L["min_met"]:
            return level
    return None


def check_shock(cfg, fetch, now):
    """Arrival of an interplanetary shock at L1: a sudden, lasting jump of solar wind speed with the plasma
    compressed and at least `min_met` of density, field strength and temperature also jumping.

    Each minute's 6-minute median from 2 minutes later is compared with the 6-minute median ending 2 minutes
    earlier (so one- or two-minute glitches cannot qualify), and the speed must stay raised (hold test) over the
    following minutes. Thresholds were calibrated on OMNI data for 2024-2025 (see config.toml)."""
    sw = cfg["solar_wind"]
    sc = sw["shock"]
    if not sc["enabled"]:
        return [], {}
    cutoff = (now - timedelta(minutes=sc["lookback_minutes"])).replace(second=0, microsecond=0)
    n = sc["lookback_minutes"] + 6
    plasma, mag = rtsw_rows(cfg, fetch, "plasma_url"), rtsw_rows(cfg, fetch, "mag_url")
    V, vsrc = _minute_series(plasma, "proton_speed", cutoff, n)
    N, _ = _minute_series(plasma, "proton_density", cutoff, n)
    T, _ = _minute_series(plasma, "proton_temperature", cutoff, n)
    B, bsrc = _minute_series(mag, "bt", cutoff, n)
    min_dv = min([sc[lv]["speed_jump"] for lv in ("warning", "alert") if sc[lv]["speed_jump"]] or [0])
    if not min_dv:
        return [], {}
    found, last = [], -999
    for i in range(7, n - 8):
        pre, post = _wmed(V, i - 7, i - 1), _wmed(V, i + 2, i + 8)
        if pre is None or post is None or post - pre < min_dv:
            continue
        dv = post - pre
        if found and i - last <= 15:
            if dv > found[-1][1]:
                found[-1] = (i, dv)
        else:
            found.append((i, dv))
        last = i
    alerts, notes = [], []
    for i, dv in found:
        pre, post = _wmed(V, i - 7, i - 1), _wmed(V, i + 2, i + 8)
        hold = _wmed(V, i + 8, i + 15, sc["hold_min_readings"])
        m = {"speed_jump": dv, "hold_jump": None if hold is None else hold - pre,
             "density_ratio": _ratio((_wmed(N, i - 7, i - 1), _wmed(N, i + 2, i + 8))),
             "field_ratio": _ratio((_wmed(B, i - 7, i - 1), _wmed(B, i + 2, i + 8))),
             "temperature_ratio": _ratio((_wmed(T, i - 7, i - 1), _wmed(T, i + 2, i + 8)))}
        # the medians cross their half-way point a few minutes before the step itself: report the first minute
        # at which the speed actually passed half-way between the before and after levels
        j = next((k for k in range(max(i - 3, 0), i + 8) if V[k] is not None and V[k] >= pre + dv / 2), i)
        t = cutoff + timedelta(minutes=j)
        mixed = len({x for x in vsrc[i - 7:i + 8] if x} | {x for x in bsrc[i - 7:i + 8] if x}) > 1
        level = None if mixed else shock_level(sc, m)
        notes.append({"time": iso(t), "speed_jump": round(dv, 1),
                      "hold_jump": None if hold is None else round(hold - pre, 1),
                      **{k: None if m[k] is None else round(m[k], 2) for k in ("density_ratio", "field_ratio", "temperature_ratio")},
                      "level": level, "spacecraft_changed": mixed, "waiting_for_hold_data": hold is None})
        if not level:
            continue
        eta_min = sc["distance_km"] / post / 60
        arrival = t + timedelta(minutes=eta_min)
        ratios = ", ".join(f"{lbl} x{m[k]:.1f}" for lbl, k in (("density", "density_ratio"), ("field", "field_ratio"),
                                                              ("temperature", "temperature_ratio")) if m[k])
        dist = f"{sc['distance_km'] / 1e6:g} million km"
        parts = [f"{w} became {x:.1f} times {adj}" for x, w, adj in
                 ((m["density_ratio"], "density", "higher"), (m["field_ratio"], "magnetic field", "stronger"),
                  (m["temperature_ratio"], "temperature", "higher")) if x and x >= 1.1]
        parts = ", ".join(parts[:-1]) + " and its " + parts[-1] if len(parts) > 1 else "".join(parts)
        alerts.append(Alert(
            key=f"shock:{iso(t)}", category="Solar wind shock", level=level, near_minutes=20,
            title=f"{LEVEL_LABEL[level]}: Shock wave in the solar wind heading for Earth, due about {arrival:%H:%M} UTC "
                  f"(wind speed jumped from {pre:.0f} to {post:.0f} km/s)",
            description=f"A shock wave (a sudden jump in the speed of the solar wind, usually driven by a coronal mass "
                        f"ejection, a cloud thrown out by the Sun) passed the NOAA spacecraft about {dist} sunward of "
                        f"Earth at about {t:%Y-%m-%d %H:%M} UTC. Solar wind speed rose from {pre:.0f} to {post:.0f} km/s "
                        f"(+{dv:.0f}) and stayed up; at the same time its {parts}. "
                        f"Level: {LEVEL_LABEL[level]}. It should reach Earth in roughly {eta_min:.0f} minutes, at about "
                        f"{arrival:%H:%M} UTC (give or take 15 minutes). When it arrives, Earth's magnetic field will "
                        f"be jolted suddenly (a 'sudden impulse'). Whether a geomagnetic storm follows depends on "
                        f"whether the magnetic field carried behind the shock points south; watch for follow-up "
                        f"storm alerts.",
            link=sc["link"], event_time=t))
    _NOTES["shock"] = {"candidates": notes[-8:]}
    return alerts, {}


# --------------------------------------------------------------------------- geomagnetically induced currents (GIC)

IAGA_ROW = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
GIC_CATEGORY = "Geomagnetically induced currents"


def parse_iaga(text):
    """IAGA-2002 one-minute magnetometer text -> [(time, X, Y)] in nT, keeping only rows where both horizontal
    components are present (99999/88888 mean missing). HDZF files are converted (D is in minutes of arc)."""
    comps, rows = None, []
    for line in text.splitlines():
        if line.startswith("DATE"):
            comps = [c[-1] for c in line.replace("|", " ").split()[3:7]]
            continue
        if not IAGA_ROW.match(line):
            continue
        parts = line.split()
        try:
            t = datetime.strptime(f"{parts[0]} {parts[1][:8]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            a, b = float(parts[3]), float(parts[4])
        except (ValueError, IndexError):
            continue
        if abs(a) >= 88888 or abs(b) >= 88888:
            continue
        if comps and comps[:2] == ["H", "D"]:
            a, b = a * math.cos(math.radians(b / 60)), a * math.sin(math.radians(b / 60))
        elif comps and comps[:2] != ["X", "Y"]:
            raise ValueError(f"unsupported components {comps}")
        rows.append((t, a, b))
    return rows


def dbdt_series(rows, cap, window):
    """Rate of change of the horizontal field in nT/min between consecutive minutes -> [(time, value)].
    Values above the plausibility cap are dropped; the rest are replaced by the median of the last `window`."""
    rates = []
    for (t0, x0, y0), (t1, x1, y1) in zip(rows, rows[1:]):
        gap = (t1 - t0).total_seconds() / 60
        if 0 < gap <= 3:
            r = math.hypot(x1 - x0, y1 - y0) / gap
            if not cap or r <= cap:
                rates.append((t1, r))
    if window > 1:
        rates = [(rates[i][0], statistics.median(v for _, v in rates[i - window + 1:i + 1]))
                 for i in range(window - 1, len(rates))]
    return rates


GIC_EXPLAIN = ("Why it matters: fast changes in Earth's magnetic field drive electric currents through the ground and into "
               "long conductors such as power lines, pipelines and railway circuits (geomagnetically induced currents). "
               "Strong ones can trip protection equipment, cause voltage problems or overheat transformers; the Quebec "
               "blackout of March 1989 was caused this way. Whether a particular grid is affected depends on the grid "
               "and the local rock beneath it.")


def gic_episode(group, headline, unit, samples, warn, alert_at, cooldown, link, basis):
    """Turn samples [(time, value, where)] into one alert for the most recent excursion at or above `warn`:
    WARNING, raised to ALERT when a value reaches `alert_at`. Later runs announce a rise to ALERT as an update."""
    hits = sorted(x for x in samples if x[1] >= warn)
    if not hits:
        return None
    spike = [hits[-1]]
    for h in reversed(hits[:-1]):
        if spike[0][0] - h[0] > SPIKE_GAP:
            break
        spike.insert(0, h)
    start = spike[0][0]
    t, v, where = max(spike, key=lambda x: x[1])
    reached = ("warning", "alert") if v >= alert_at else ("warning",)
    level = reached[-1]

    def text(lv):
        thr = alert_at if lv == "alert" else warn
        return (f"{headline(v, where)}. Level: {LEVEL_LABEL[lv]} (the {LEVEL_LABEL[lv]} threshold is {thr:g} {unit}). "
                f"The peak was {v:.0f} {unit} at {t:%Y-%m-%d %H:%M} UTC; it began at {start:%H:%M} UTC. {basis} "
                f"{GIC_EXPLAIN}")

    def updater(fresh, seen):
        lv = top_level(reached + tuple(fresh))
        before = top_level(seen)
        raised = f" Raised from {LEVEL_LABEL[before]}." if before and before != lv else ""
        return (f"{LEVEL_LABEL[lv]}: {headline(v, where)} (update)", text(lv) + raised, lv)

    return Alert(key=f"{group}:{iso(start)}", category=GIC_CATEGORY, level=level,
                 title=f"{LEVEL_LABEL[level]}: {headline(v, where)}", description=text(level), link=link,
                 event_time=t, group=group, cooldown_minutes=cooldown, attrs=reached, on_update=updater)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def gic_dbdt(cfg, fetch, now):
    g, c = cfg["gic"], cfg["gic"]["dbdt"]
    cutoff = now - timedelta(minutes=c["lookback_minutes"])
    start_day = cutoff.date()
    days = (now.date() - start_day).days + 1
    by_region, notes = {}, {}
    for st in c["stations"]:
        code, region = st["code"].strip().upper(), st["region"].strip()
        if st["source"] == "usgs":
            url = c["usgs_url"] + "?" + urllib.parse.urlencode({
                "id": code, "type": "variation", "format": "iaga2002", "sampling_period": 60,
                "starttime": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"), "endtime": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        else:
            url = c["gin_url"] + "?" + urllib.parse.urlencode({
                "Request": "GetData", "format": "iaga2002", "testObsys": 0, "observatoryIagaCode": code,
                "samplesPerDay": "minute", "publicationState": "Best available",
                "dataStartDate": start_day.isoformat(), "dataDuration": days}, quote_via=urllib.parse.quote)
        try:
            rows = [r for r in parse_iaga(fetch.text(url)) if cutoff <= r[0] <= now + timedelta(minutes=5)]
            series = dbdt_series(rows, c["max_plausible"], c["smoothing_window"])
        except Exception as e:                               # one station failing must not hide the others
            log.warning("GIC dB/dt %s: %s", code, e)
            notes[code] = {"error": f"{type(e).__name__}: {e}"[:200]}
            continue
        newest = rows[-1][0] if rows else None
        notes[code] = {"region": region, "readings": len(rows), "newest": iso(newest) if newest else None,
                       "age_minutes": round((now - newest).total_seconds() / 60, 1) if newest else None,
                       "max_smoothed_dbdt": round(max((v for _, v in series), default=0), 1)}
        if series:
            by_region.setdefault(region, []).append((code, series, newest))
    _NOTES.setdefault("gic", {})["dbdt_stations"] = notes
    alerts, conds = [], {}
    for region, stations in by_region.items():
        samples = [(t, v, code) for code, series, _ in stations for t, v in series]
        # the episode is still open while a recently reporting station's latest value is at or above the warning level
        still = any(series[-1][1] >= c["warning_threshold"] and (now - newest).total_seconds() / 60 <= c["max_station_age_minutes"]
                    for _, series, newest in stations)
        group = f"gic_dbdt_{_slug(region)}"
        conds[group] = still
        a = gic_episode(group, lambda v, w, region=region: f"Very rapid magnetic field change in {region}: "
                                                          f"{v:.0f} nT/min measured at {station_text(w)}; "
                                                          f"risk of currents in power grids",
                        "nT/min", samples, c["warning_threshold"], c["alert_threshold"], g["cooldown_minutes"], g["link"],
                        f"This is how fast the horizontal magnetic field changed, in nanotesla per minute (nT/min), "
                        f"measured by ground observatories in {region}; on quiet days it is usually only a few nT/min or less.")
        if a:
            alerts.append(a)
    return alerts, conds


def gic_geoelectric(cfg, fetch, now):
    g, c = cfg["gic"], cfg["gic"]["geoelectric"]
    store = _STATE["store"]
    listing = fetch.text(c["listing_url"])
    files = {}
    for m in re.finditer(r'href="((\d{8}T\d{6})[^"]*\.json)"', listing):
        try:
            files[datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)] = m.group(1)
        except ValueError:
            continue
    last = parse_time(store.get_kv("gic_efield_last"))
    floor = max(now - timedelta(minutes=c["lookback_minutes"]), last + timedelta(seconds=1)) if last else \
        now - timedelta(minutes=c["lookback_minutes"])
    todo = sorted(t for t in files if floor <= t <= now + timedelta(minutes=5))
    n = c["max_files_per_run"]
    if len(todo) > n:                                        # evenly spaced, always including the newest
        todo = sorted({todo[round(i * (len(todo) - 1) / (n - 1))] for i in range(n)}) if n > 1 else todo[-1:]
    notes = {"files_listed": len(files), "files_new": len(todo), "newest_listed": iso(max(files)) if files else None}
    _NOTES.setdefault("gic", {})["geoelectric"] = notes
    if not todo:
        return [], {}                                        # nothing new: leave the episode state unchanged
    samples, ok = [], []
    for t in todo:
        try:
            data = fetch.json(c["listing_url"].rstrip("/") + "/" + files[t])
        except Exception as e:
            log.warning("GIC geoelectric %s: %s", files[t], e)
            notes["error"] = f"{type(e).__name__}: {e}"[:200]
            continue
        vals = []
        for f in data.get("features", []):
            pr = f.get("properties") or {}
            ex, ey = to_float(pr.get("Ex")), to_float(pr.get("Ey"))
            if ex is None or ey is None:
                continue
            dist = to_float(pr.get("distance_nearest_station"))
            if c["max_nearest_station_km"] and dist is not None and dist > c["max_nearest_station_km"]:
                continue
            lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
            vals.append((math.hypot(ex, ey), lat, lon))
        if len(vals) < c["min_points"]:
            continue
        vals.sort(reverse=True)
        # the value of the min_points-th strongest grid point, so one odd grid point cannot trigger an alert
        v, lat, lon = vals[c["min_points"] - 1]
        samples.append((t, v, f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'} {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"))
        ok.append(t)
    if ok:
        store.set_kv("gic_efield_last", iso(max(ok)))
    notes.update(files_used=len(ok), strongest=round(max((s[1] for s in samples), default=0), 1))
    if not samples:
        return [], {}
    group = "gic_geoelectric_na"
    still = max(samples)[1] >= c["warning_threshold"]
    a = gic_episode(group, lambda v, w: f"Strong electric field in the ground across the US and Canada: {v:.0f} mV/km near {w}; "
                                          f"risk of currents in power grids",
                    "mV/km", samples, c["warning_threshold"], c["alert_threshold"], g["cooldown_minutes"], g["link"],
                    f"This is the electric field in the ground, in millivolts per kilometre (mV/km), from NOAA's geoelectric field "
                    f"model for the US and Canada; values this high are far above normal. The value shown was reached or "
                    f"exceeded at {c['min_points']} grid points, so one odd point cannot trigger an alert.")
    return ([a] if a else []), {group: still}


def check_gic(cfg, fetch, now):
    """Geomagnetically induced currents in power grids: WARNING and ALERT levels from (1) ground magnetometer
    dB/dt in several world regions and (2) NOAA's geoelectric field model for the US and Canada."""
    g = cfg["gic"]
    if not g["enabled"]:
        return [], {}
    alerts, conds = [], {}
    for enabled, fn in ((g["dbdt"]["enabled"], gic_dbdt), (g["geoelectric"]["enabled"], gic_geoelectric)):
        if not enabled:
            continue
        try:
            a, c = fn(cfg, fetch, now)
            alerts += a
            conds.update(c)
        except Exception as e:                               # the two sources fail independently
            log.warning("%s failed: %s", fn.__name__, e)
            _NOTES.setdefault("gic", {})[fn.__name__ + "_error"] = f"{type(e).__name__}: {e}"[:300]
    return alerts, conds


def check_coupling(cfg, fetch, now):
    """Magnetic coupling during a strong storm: Kp is at or above `min_kp` AND the IMF clock angle is inside
    the range clock_angle_min..clock_angle_max (default 150-210 degrees, i.e. pointing close to due south).

    The clock angle is the direction of the interplanetary magnetic field seen from the Sun, measured in the
    plane across the Earth-Sun line (GSM coordinates): atan2(By, Bz), 0 = north, 180 = south, 0-360 degrees.
    By and Bz are each smoothed (median of `smoothing_window` minutes) BEFORE the angle is computed, so angles
    either side of north cannot average into a false southward value. The angle must stay in the range for
    `min_minutes`. The alert starts when both conditions are true, whichever came first, and ends when either stops."""
    c = cfg["geomagnetic"]["coupling"]
    if not c["enabled"] or c["level"] == "off":
        return [], {}
    kp_now = None
    for r in table_rows(fetch.json(c["kp_url"])):
        t, kp = parse_time(r.get("time_tag")), to_float(first_present(r, "Kp", "kp_index", "kp"))
        if t and kp is not None and now - timedelta(hours=c["kp_valid_hours"]) <= t <= now + timedelta(minutes=5):
            if kp_now is None or kp > kp_now[1]:
                kp_now = (t, kp)
    pts = []
    for r in rtsw_rows(cfg, fetch, "mag_url"):
        t, by, bz = parse_time(r.get("time_tag")), to_float(r.get("by_gsm")), to_float(r.get("bz_gsm"))
        if t and by is not None and bz is not None and \
                now - timedelta(minutes=c["lookback_minutes"]) <= t <= now + timedelta(minutes=5):
            pts.append((t, by, bz))
    pts.sort()
    w = c["smoothing_window"]
    smooth = []
    for i in range(w - 1, len(pts)):
        by = statistics.median(x[1] for x in pts[i - w + 1:i + 1])
        bz = statistics.median(x[2] for x in pts[i - w + 1:i + 1])
        if by or bz:
            smooth.append((pts[i][0], math.degrees(math.atan2(by, bz)) % 360))
    lo, hi = c["clock_angle_min"], c["clock_angle_max"]
    kp_high = kp_now is not None and kp_now[1] >= c["min_kp"]
    _NOTES["coupling"] = {"kp": None if kp_now is None else kp_now[1], "kp_high": kp_high,
                          "latest_clock_angle": round(smooth[-1][1], 1) if smooth else None,
                          "readings": len(pts)}
    if not smooth:
        return [], {}                                       # no data: leave state unchanged
    run = []                                                # the latest unbroken stretch inside the range
    for t, v in reversed(smooth):
        if not lo <= v <= hi or (run and run[0][0] - t > timedelta(minutes=5)):
            break
        run.insert(0, (t, v))
    lasted = (run[-1][0] - run[0][0]).total_seconds() / 60 + 1 if run else 0
    inside_now = bool(run) and smooth[-1][0] == run[-1][0]
    still = kp_high and inside_now
    if not (still and lasted >= c["min_minutes"]):
        return [], {"coupling": still}
    entered = run[0][0]
    began = max(entered, kp_now[0])                         # when both conditions first held
    latest = run[-1][1]
    level = c["level"]
    g = min(5, max(1, int(kp_now[1] + 0.5) - 4))
    return [Alert(
        key=f"coupling:{iso(began)}", category="Geomagnetic storm", level=level,
        title=f"{LEVEL_LABEL[level]}: Earth is coupling with the solar wind during a strong storm "
              f"(Kp {kp_now[1]:g}, magnetic field pointing close to south)",
        description=f"Geomagnetic activity is at Kp {kp_now[1]:g}, a {g_text(g)} storm, and the magnetic field carried by "
                    f"the solar wind has turned to point close to due south: its clock angle entered the {lo:g} to "
                    f"{hi:g} degree range at {entered:%Y-%m-%d %H:%M} UTC and is now {latest:.0f} degrees. "
                    f"(The clock angle is the direction of that field across the Earth-Sun line: 0 degrees is north, "
                    f"180 degrees is south.) A southward field links up with Earth's own magnetic field, letting solar wind "
                    f"energy in, which can intensify storm conditions. Level: {LEVEL_LABEL[level]}. Effects reach Earth in "
                    f"roughly 30-60 minutes after the solar wind spacecraft sees them; watch for stronger aurora and "
                    f"for power grid, GPS and radio disturbances.",
        link=c["link"], event_time=began, group="coupling", cooldown_minutes=c["cooldown_minutes"])], \
        {"coupling": still}


CHECKS = [check_flares, check_filaments, check_coronal_hole, check_storm_forecast,
          check_kp, check_coupling, check_protons, check_solar_wind, check_shock, check_sunspots, check_gic]


# --------------------------------------------------------------------------- pipeline + feed

def announce_new_attributes(store, a, now):
    """For a region whose episode is already open: alert once about attributes not yet announced."""
    if not a.attrs or a.on_update is None:
        return None
    seen = store.reported(a.group)
    if not seen:                                   # episode began before this feature existed: seed quietly
        store.add_reported(a.group, a.attrs)
        return None
    fresh = sorted(x for x in a.attrs if x not in seen)
    if not fresh:
        return None
    store.add_reported(a.group, fresh)
    title, description, level = a.on_update(fresh, seen)
    upd = Alert(key=f"{a.key}+{'+'.join(fresh)}", category=a.category, title=title, description=description,
                link=a.link, event_time=a.event_time, level=level or "")
    if store.has_key(upd.key):
        return None
    store.add(upd, now)
    return upd


def process(store, alerts, conditions, now):
    new = []
    for a in sorted(alerts, key=lambda x: x.event_time):
        if a.group and a.attrs and store.group(a.group)[0]:       # open episode: only newly met attributes
            upd = announce_new_attributes(store, a, now)
            if upd:
                new.append(upd)
            continue
        if a.update_only:
            continue
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
            store.add_reported(a.group, a.attrs)
        store.add(a, now)
        new.append(a)
    for g, breached in conditions.items():
        store.set_active(g, breached)
        if not breached:
            store.clear_reported(g)                # episode over: the next one announces everything again
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
                *([f"<category>{escape(LEVEL_LABEL.get(it['level'], it['level'].title()))}</category>"]
                  if it.get("level") else []),
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
    alerts, conds, results = [], {}, []
    _NOTES.clear()
    _STATE["store"] = store
    _STATE["rtsw"] = {}
    for check in CHECKS:
        try:
            a, c = check(cfg, fetch, now)
            alerts += a
            conds.update(c)
            results.append({"check": check.__name__, "ok": True, "candidates": len(a)})
        except Exception as e:
            log.warning("%s failed: %s", check.__name__, e)
            results.append({"check": check.__name__, "ok": False, "error": f"{type(e).__name__}: {e}"})
    new = process(store, alerts, conds, now)
    store.prune(cfg["runtime"]["retention_days"], now)
    store.commit()
    items = store.recent(cfg["feed"]["max_age_days"], cfg["feed"]["max_items"], now)
    write_atomic(cfg["feed"]["output_path"], render_feed(cfg, items))
    for a in new:
        log.info("NEW ALERT: %s", a.title)
    health = {"generated": iso(now), "checks": results, "new_alerts": [a.title for a in new],
              "sources": getattr(fetch, "log", []), "notes": dict(_NOTES)}
    health_path = os.path.join(os.path.dirname(os.path.abspath(cfg["runtime"]["database"])), "health.json")
    write_atomic(health_path, json.dumps(health, indent=1, sort_keys=True, default=str) + "\n")
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
