"""Offline tests: python3 -m unittest -v   (no network needed)"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import solar_alerts as sa

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def ts(minutes_ago, sep=" "):
    return (NOW - timedelta(minutes=minutes_ago)).strftime(f"%Y-%m-%d{sep}%H:%M:%S")


class Fake:
    def __init__(self, cfg):
        self.cfg, self.data = cfg, {}

    def json(self, url):
        for k, v in self.data.items():
            if url.startswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        raise KeyError(url)

    text = json


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))   # pinned, independent of admin edits
        self.cfg["feed"]["output_path"] = os.path.join(self.tmp.name, "site", "feed.xml")   # dirs created on demand
        self.cfg["runtime"]["database"] = os.path.join(self.tmp.name, "state", "t.db")   # health.json lands beside it
        self.store = sa.Store(self.cfg["runtime"]["database"])
        c = self.cfg
        self.f = Fake(c)
        # every source empty by default
        self.f.data = {
            c["hek"]["url"]: {"result": []},
            c["geomagnetic"]["coronal_hole"]["url"]: ":Issued: 2026 Oct 13 0030 UTC\nNothing here.",
            c["geomagnetic"]["storm_forecast"]["alerts_url"]: [],
            c["geomagnetic"]["kp"]["url"]: [],
            c["sunspots"]["url"]: [],
            c["protons"]["ge10mev"]["url"]: [],
            c["solar_wind"]["plasma_url"]: [["time_tag", "density", "speed", "temperature"]],
            c["solar_wind"]["mag_url"]: [["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "lon_gsm", "lat_gsm", "bt"]],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, now=NOW):
        return sa.run_cycle(self.cfg, self.f, self.store, now)

    def titles(self, new):
        return [a.title for a in new]


class Config(unittest.TestCase):
    def test_shipped_config_valid(self):
        sa.load_config(os.path.join(HERE, "config.toml"))

    def test_bad_values_rejected(self):
        cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
        cfg["flares"]["min_class"] = "Q9"
        cfg["solar_wind"]["bz"]["direction"] = "sideways"
        del cfg["geomagnetic"]["kp"]["min_kp"]
        with self.assertRaises(sa.ConfigError) as cm:
            sa.validate_config(cfg)
        msg = str(cm.exception)
        self.assertIn("flares.min_class", msg)
        self.assertIn("solar_wind.bz.direction", msg)
        self.assertIn("geomagnetic.kp.min_kp", msg)

    def test_goes_class(self):
        self.assertGreater(sa.goes_flux("X1.0"), sa.goes_flux("M9.9"))
        self.assertAlmostEqual(sa.goes_flux("m5.0"), 5e-5)
        self.assertIsNone(sa.goes_flux("nonsense"))


class SolarEvents(Base):
    def ev(self, cls, lon, lat=10, minutes_ago=30, frm="SWPC", kb="a", typ="FL"):
        e = {"event_type": typ, "event_starttime": ts(minutes_ago + 10, "T"), "event_peaktime": ts(minutes_ago, "T"),
             "hgs_x": lon, "hgs_y": lat, "frm_name": frm, "kb_archivid": kb}
        if cls:
            e["fl_goescls"] = cls
        return e

    def test_flare_rules(self):
        self.f.data[self.cfg["hek"]["url"]] = {"result": [
            self.ev("M7.1", 20, kb="1"),                    # full  -> alert
            self.ev("X1.0", -75, kb="2", minutes_ago=90),   # partial -> alert
            self.ev("X2.0", 120, kb="3", minutes_ago=120),  # far side -> ignore
            self.ev("M2.0", 10, kb="4", minutes_ago=150),   # too weak -> ignore
            self.ev("M9.0", None, kb="5", minutes_ago=200), # unknown location -> ignore
            self.ev("M7.1", 21, kb="6", frm="Other"),       # duplicate of #1 from another FRM
        ]}
        new = self.cycle()
        self.assertEqual(len(new), 2, self.titles(new))
        self.assertTrue(any("M7.1" in t and "Earth-facing" in t for t in self.titles(new)))
        self.assertTrue(any("X1.0" in t and "partially" in t for t in self.titles(new)))
        self.assertEqual(self.cycle(), [])                  # nothing repeated next poll

    def test_threshold_is_configurable(self):
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M2.0", 10, kb="4")]}
        self.assertEqual(self.cycle(), [])
        self.cfg["flares"]["min_class"] = "M1.0"
        self.assertEqual(len(self.cycle()), 1)

    def test_unknown_location_alert_mode(self):
        self.cfg["flares"]["unknown_location"] = "alert"
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M9.0", None, kb="5")]}
        self.assertEqual(len(self.cycle()), 1)

    def test_filament(self):
        self.f.data[self.cfg["hek"]["url"]] = {"result": [
            self.ev(None, 15, kb="f1", typ="FE"), self.ev(None, 130, kb="f2", typ="FE", minutes_ago=300)]}
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("Filament eruption", new[0].title)


class Geomagnetic(Base):
    def test_kp(self):
        self.f.data[self.cfg["geomagnetic"]["kp"]["url"]] = [
            {"time_tag": ts(60, "T"), "Kp": 7.67}, {"time_tag": ts(240, "T"), "Kp": 6.67}]
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("7.67", new[0].title)
        self.assertIn("G4", new[0].title)

    def test_kp_list_format(self):
        self.f.data[self.cfg["geomagnetic"]["kp"]["url"]] = [
            ["time_tag", "Kp", "a_running", "station_count"], [ts(60, "T"), "8.00", "207", "8"]]
        self.assertEqual(len(self.cycle()), 1)

    def test_storm_forecast(self):
        url = self.cfg["geomagnetic"]["storm_forecast"]["alerts_url"]
        self.f.data[url] = [
            {"issue_datetime": ts(60), "message": "WATCH: Geomagnetic Storm Category G3 Predicted"},
            {"issue_datetime": ts(30), "message": "Space Weather Message\nWATCH: Geomagnetic Storm Category G4 Predicted\nNOAA"},
            {"issue_datetime": ts(5000), "message": "WATCH: Geomagnetic Storm Category G5 Predicted"}]   # too old
        new = self.cycle()
        self.assertEqual(self.titles(new), ["G4 geomagnetic storm forecast"])

    def test_storm_forecast_k_warning(self):
        url = self.cfg["geomagnetic"]["storm_forecast"]["alerts_url"]
        self.f.data[url] = [{"issue_datetime": ts(30), "message": "WARNING: Geomagnetic K-index of 8 expected"}]
        self.assertEqual(self.titles(self.cycle()), ["G4 geomagnetic storm forecast"])

    def test_coronal_hole(self):
        url = self.cfg["geomagnetic"]["coronal_hole"]["url"]
        self.f.data[url] = (":Issued: 2026 Oct 13 0030 UTC\n"
                            "A recurrent coronal hole high speed stream (CH HSS) is expected to\n"
                            "influence Earth on 14-15 Oct. No solar flares are expected.")
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("Coronal hole", new[0].title)

    def test_coronal_hole_negative_ignored(self):
        url = self.cfg["geomagnetic"]["coronal_hole"]["url"]
        self.f.data[url] = ":Issued: 2026 Oct 13 0030 UTC\nNo coronal hole influence is expected."
        self.assertEqual(self.cycle(), [])


class ProtonsAndWind(Base):
    def test_protons(self):
        url = self.cfg["protons"]["ge10mev"]["url"]
        self.f.data[url] = [
            {"time_tag": ts(5, "T"), "satellite": 19, "flux": 150.0, "energy": ">=10 MeV"},
            {"time_tag": ts(5, "T"), "satellite": 19, "flux": 0.4, "energy": ">=100 MeV"},
            {"time_tag": ts(4, "T"), "satellite": 19, "flux": 2.5, "energy": ">=100 MeV"}]
        new = self.cycle()
        self.assertEqual(len(new), 2)
        self.assertEqual(self.cycle(), [])

    def test_protons_below_threshold(self):
        url = self.cfg["protons"]["ge10mev"]["url"]
        self.f.data[url] = [{"time_tag": ts(5, "T"), "flux": 100.0, "energy": ">=10 MeV"}]  # equal != exceeds
        self.assertEqual(self.cycle(), [])

    def wind(self, speed, dens, bt, bz, minutes_ago=5):
        c = self.cfg["solar_wind"]
        self.f.data[c["plasma_url"]] = [["time_tag", "density", "speed", "temperature"],
                                        [ts(minutes_ago), str(dens), str(speed), "100000"]]
        self.f.data[c["mag_url"]] = [["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "lon_gsm", "lat_gsm", "bt"],
                                     [ts(minutes_ago), "1", "2", str(bz), "0", "0", str(bt)]]

    def test_wind_all_rules(self):
        self.wind(speed=850, dens=12, bt=45, bz=-42)
        self.assertEqual(len(self.cycle()), 4)

    def test_bz_direction(self):
        self.wind(speed=400, dens=3, bt=20, bz=-39)          # not past -40
        self.assertEqual(self.cycle(), [])
        self.wind(speed=400, dens=3, bt=20, bz=45)           # strongly northward must NOT alert
        self.assertEqual(self.cycle(), [])
        self.wind(speed=400, dens=3, bt=20, bz=-41)
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("Bz", new[0].title)

    def test_hysteresis_and_cooldown(self):
        self.wind(850, 3, 20, 0)
        self.assertEqual(len(self.cycle(NOW)), 1)
        self.wind(860, 3, 20, 0, minutes_ago=0)
        self.assertEqual(self.cycle(NOW + timedelta(minutes=5)), [])      # same episode
        self.f.data[self.cfg["solar_wind"]["plasma_url"]] = [["time_tag", "density", "speed", "temperature"]] \
            + [[(NOW + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"), "3", "500", "1"]]
        self.assertEqual(self.cycle(NOW + timedelta(minutes=45)), [])      # clears
        self.f.data[self.cfg["solar_wind"]["plasma_url"]] = [["time_tag", "density", "speed", "temperature"]] \
            + [[(NOW + timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S"), "3", "900", "1"]]
        self.assertEqual(self.cycle(NOW + timedelta(minutes=50)), [])      # re-breach inside 60 min cooldown
        self.f.data[self.cfg["solar_wind"]["plasma_url"]] = [["time_tag", "density", "speed", "temperature"]] \
            + [[(NOW + timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M:%S"), "3", "500", "1"]]
        self.cycle(NOW + timedelta(minutes=70))
        self.f.data[self.cfg["solar_wind"]["plasma_url"]] = [["time_tag", "density", "speed", "temperature"]] \
            + [[(NOW + timedelta(minutes=75)).strftime("%Y-%m-%d %H:%M:%S"), "3", "900", "1"]]
        self.assertEqual(len(self.cycle(NOW + timedelta(minutes=75))), 1)  # cooldown over

    def test_disabled_rule(self):
        self.cfg["solar_wind"]["speed"]["enabled"] = False
        self.wind(speed=900, dens=3, bt=20, bz=0)
        self.assertEqual(self.cycle(), [])

    def test_failed_source_does_not_block_others(self):
        self.wind(speed=900, dens=3, bt=45, bz=0)
        self.f.data[self.cfg["solar_wind"]["plasma_url"]] = OSError("down")
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("Bt", new[0].title)


class Sunspots(Base):
    """Fixtures mirror NOAA's real solar_regions.json records for AR4551 (S15E07, BGD on 14 Oct 2026)."""

    def rec(self, region=4551, days_ago=1, loc="S15E07", mag="BGD", lon=None, base=NOW, **kw):
        d = (base - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        r = {"observed_date": d, "region": region, "location": loc, "mag_class": mag, "spot_class": "Eki",
             "area": 500, "number_spots": 23, "m_flare_probability": 25, "x_flare_probability": 10}
        if lon is not None:
            r["longitude"] = lon
        r.update(kw)
        return r

    def feed(self, *records):
        self.f.data[self.cfg["sunspots"]["url"]] = list(records)

    def test_ar4551_alerts(self):
        # real history: B, BG, B, BG on earlier days, BGD on the latest day
        self.feed(self.rec(days_ago=5, loc="S13E71", mag="B"), self.rec(days_ago=4, loc="S14E61", mag="B"),
                  self.rec(days_ago=3, loc="S15E47", mag="BG"), self.rec(days_ago=2, loc="S15E21", mag="BG"),
                  self.rec(days_ago=1))
        new = self.cycle()
        self.assertEqual(len(new), 1, self.titles(new))
        t = new[0].title
        for part in ("AR4551", "beta-gamma-delta", "S15E07", "Earth-facing"):
            self.assertIn(part, t)
        self.assertIn("M-class 25%", new[0].description)
        self.assertEqual(self.cycle(), [])                      # not repeated on the next poll

    def test_older_bgd_record_superseded(self):
        self.feed(self.rec(days_ago=2, mag="BGD"), self.rec(days_ago=1, mag="BG"))   # no longer BGD
        self.assertEqual(self.cycle(), [])

    def test_other_classes_ignored_by_default_but_configurable(self):
        self.feed(self.rec(mag="BD", region=1), self.rec(mag="GD", region=2), self.rec(mag="BG", region=3))
        self.assertEqual(self.cycle(), [])
        self.cfg["sunspots"]["dangerous_classes"] = ["bgd", "GD", "BD"]       # case-insensitive
        self.assertEqual(len(self.cycle()), 2)

    def test_earth_facing_boundary_and_sign(self):
        self.feed(self.rec(region=1, loc="S15E60"), self.rec(region=2, loc="N10W60"),
                  self.rec(region=3, loc="S15E61"), self.rec(region=4, loc="N09W75"))
        new = self.cycle()
        self.assertEqual(sorted(a.title.split(":")[0] for a in new),
                         ["Dangerous sunspot AR1", "Dangerous sunspot AR2"])
        self.cfg["sunspots"]["max_longitude"] = 80                              # admin widens the cone
        self.assertEqual(len(self.cycle()), 2)                                  # AR3 + AR4 now qualify

    def test_location_fallback_to_longitude_field(self):
        self.feed(self.rec(region=1, loc="", lon=7), self.rec(region=2, loc="", lon=-67),
                  self.rec(region=3, loc="", lon=None))
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("AR1", new[0].title)

    def test_stale_record_ignored(self):
        self.feed(self.rec(days_ago=6))
        self.assertEqual(self.cycle(), [])

    def test_no_repeat_next_day_then_realert_after_clearing_and_cooldown(self):
        self.feed(self.rec(days_ago=1))
        self.assertEqual(len(self.cycle(NOW)), 1)
        day2 = NOW + timedelta(days=1)
        self.feed(self.rec(days_ago=1), self.rec(days_ago=0, base=day2))        # still BGD today
        self.assertEqual(self.cycle(day2), [])                                  # same episode
        day3 = NOW + timedelta(days=2)
        self.feed(self.rec(days_ago=0, mag="BG", base=day3))                    # complexity dropped
        self.assertEqual(self.cycle(day3), [])
        early = NOW + timedelta(days=2, hours=1)
        self.feed(self.rec(days_ago=0, base=early))                             # BGD again, but see below
        self.cfg["sunspots"]["cooldown_minutes"] = 10 * 24 * 60                 # long cooldown blocks re-alert
        self.assertEqual(self.cycle(early), [])
        self.cfg["sunspots"]["cooldown_minutes"] = 1440
        self.feed(self.rec(days_ago=0, mag="BG", base=early))
        self.cycle(early)                                                       # clears again
        day4 = NOW + timedelta(days=3)
        self.feed(self.rec(days_ago=0, base=day4))                              # BGD again, cooldown elapsed
        self.assertEqual(len(self.cycle(day4)), 1)

    def test_disabled_and_failed_source(self):
        self.feed(self.rec())
        self.cfg["sunspots"]["enabled"] = False
        self.assertEqual(self.cycle(), [])
        self.cfg["sunspots"]["enabled"] = True
        self.f.data[self.cfg["sunspots"]["url"]] = OSError("down")
        self.assertEqual(self.cycle(), [])                                      # no crash, feed still written

    def test_bad_sunspot_config_rejected(self):
        for bad in ({"dangerous_classes": []}, {"dangerous_classes": "BGD"}, {"max_longitude": 200}):
            cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
            cfg["sunspots"].update(bad)
            with self.assertRaises(sa.ConfigError, msg=str(bad)):
                sa.validate_config(cfg)


# Real sentences from SWPC forecast discussions issued 13-15 Oct 2026. All describe a stream that was
# already under way and fading; none of them should produce a "stream forecast to impact Earth" alert.
REAL_ONGOING = {
    "13 Oct 1230": "Solar wind parameters reflected likely negative polarity CH HSS influences. By 15 Oct, quiet "
                   "to unsettled levels are expected to return as CH HSS/CME influence tapers off.",
    "14 Oct 0030": "The greater than 2 MeV electron flux is expected to reach high levels on 14-15 Oct following "
                   "negative polarity coronal hole high-speed stream (-CH HSS) influences, before returning to "
                   "normal to moderate levels on 16 Oct. Solar wind parameters reflected negative polarity coronal "
                   "hole high-speed stream (-CH HSS) influences, along with potential flanking CME influences early "
                   "in the period. Negative polarity CH HSS effects are anticipated to persist through 14 Oct "
                   "before subsiding early on 15 Oct.",
    "14 Oct 1230": "The greater than 2 MeV electron flux is expected to reach high levels on 15 and 16 Oct due to "
                   "negative polarity CH HSS influences. Negative polarity CH HSS effects are anticipated to "
                   "persist through 16 Oct, although, slowing diminishing over the course of 15 and 16 Oct.",
    "15 Oct 1230": "Solar wind parameters began elevated under persistent HSS activity. Solar wind conditions are "
                   "anticipated to return to near-background/ambient levels by 17 Oct as influences from a negative "
                   "polarity CH HSS slowly wane. Unsettled conditions are expected to persist through 16 Oct, as "
                   "influences from the negative polarity CH HSS wane.",
}
ARRIVALS = [
    "A recurrent positive polarity CH HSS is expected to arrive on 18 Oct.",
    "Active conditions are likely on 19-20 Oct due to a positive polarity coronal hole high speed stream.",
    "Solar wind speeds are expected to increase to near 600 km/s late on 17 Oct as a positive polarity CH HSS "
    "rotates into a geoeffective position.",
]


class CoronalHole(Base):
    def put(self, issued, body):
        self.f.data[self.cfg["geomagnetic"]["coronal_hole"]["url"]] = f":Issued: {issued} UTC\n\n{body}\n"

    def direct(self, body, issued="2026 Oct 13 1230"):
        self.put(issued, body)
        return sa.check_coronal_hole(self.cfg, self.f, NOW)

    def test_real_ongoing_discussions_do_not_alert(self):
        for when, body in REAL_ONGOING.items():
            alerts, conds = self.direct(body)
            self.assertEqual(alerts, [], when)
            self.assertEqual(conds, {"coronal_hole": False}, when)

    def test_arrival_wording_alerts(self):
        for body in ARRIVALS:
            alerts, conds = self.direct("Solar Wind\n" + body)
            self.assertEqual(len(alerts), 1, body)
            self.assertTrue(conds["coronal_hole"])

    def test_new_stream_alerts_even_while_old_one_wanes(self):
        old = REAL_ONGOING["15 Oct 1230"]
        alerts, _ = self.direct(old + " " + ARRIVALS[0])
        self.assertEqual(len(alerts), 1)
        self.assertIn("expected to arrive on 18 Oct", alerts[0].description)
        self.assertNotIn("wane", alerts[0].description)

    def test_episode_alerts_once_and_rearms(self):
        def poll(issued, body, when):
            self.put(issued, body)
            return self.cycle(when)
        self.assertEqual(len(poll("2026 Oct 16 0030", ARRIVALS[0], NOW)), 1)
        # 12 h later the discussion repeats the arrival: same stream, no new alert
        self.assertEqual(poll("2026 Oct 16 1230", ARRIVALS[0], NOW + timedelta(hours=12)), [])
        # stream arrives and is fading: the episode closes
        self.assertEqual(poll("2026 Oct 18 0030", REAL_ONGOING["15 Oct 1230"], NOW + timedelta(days=2)), [])
        # a different stream is announced later: alerts again
        self.assertEqual(len(poll("2026 Oct 24 0030", ARRIVALS[1], NOW + timedelta(days=8))), 1)

    def test_debug_notes_explain_decisions(self):
        self.put("2026 Oct 13 1230", REAL_ONGOING["13 Oct 1230"] + " " + ARRIVALS[0])
        self.cycle()
        notes = sa._NOTES["coronal_hole"]["stream_sentences"]
        self.assertEqual([n["alert"] for n in notes], [False, False, True])


class LongLookback(ProtonsAndWind):
    """The workflow may run only every few hours, so spikes between runs must still be found, once."""

    def rows(self, spec):
        hdr = ["time_tag", "density", "speed", "temperature"]
        return [hdr] + [[ts(m), "3", str(v), "100000"] for m, v in spec]

    def setUp(self):
        super().setUp()
        self.cfg["solar_wind"]["speed"]["lookback_minutes"] = 480
        self.url = self.cfg["solar_wind"]["plasma_url"]

    def test_spike_between_runs_is_found_once(self):
        self.f.data[self.url] = self.rows([(300, 500), (200, 850), (199, 870), (198, 840), (100, 600), (5, 500)])
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("870", new[0].title)
        self.assertEqual(self.cycle(NOW + timedelta(hours=2)), [])      # same data, later run: no repeat

    def test_bigger_earlier_spike_does_not_hide_a_later_one(self):
        self.f.data[self.url] = self.rows([(400, 950), (300, 500), (90, 820), (5, 500)])
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("820", new[0].title)                               # the most recent spike

    def test_episode_stays_open_only_while_beyond_threshold(self):
        self.f.data[self.url] = self.rows([(200, 850), (5, 880)])         # still high now
        self.assertEqual(len(self.cycle()), 1)
        self.f.data[self.url] = self.rows([(200, 850), (5, 880), (2, 890)])
        self.assertEqual(self.cycle(NOW + timedelta(minutes=3)), [])     # same episode


class Health(Base):
    def test_health_report_written(self):
        import json as _json
        self.cycle()
        path = os.path.join(self.tmp.name, "state", "health.json")
        h = _json.load(open(path))
        self.assertEqual(h["generated"], sa.iso(NOW))
        self.assertTrue(all(c["ok"] for c in h["checks"]), h["checks"])
        self.assertEqual({c["check"] for c in h["checks"]}, {f.__name__ for f in sa.CHECKS})

    def test_failed_check_is_reported_not_fatal(self):
        import json as _json
        self.f.data[self.cfg["sunspots"]["url"]] = OSError("down")
        self.cycle()
        h = _json.load(open(os.path.join(self.tmp.name, "state", "health.json")))
        bad = [c for c in h["checks"] if not c["ok"]]
        self.assertEqual([c["check"] for c in bad], ["check_sunspots"])

    def test_summarize_table_and_dict(self):
        out = sa.summarize([["time_tag", "bt"], ["2026-10-15 10:00:00", "5"], ["2026-10-15 11:00:00", "6"]])
        self.assertEqual((out["rows"], out["oldest"], out["newest"]), (2, "2026-10-15T10:00:00Z", "2026-10-15T11:00:00Z"))
        self.assertEqual(out["columns"], ["time_tag", "bt"])
        self.assertEqual(sa.summarize({"result": [{"event_starttime": "2026-10-15T10:00:00"}]})["rows"], 1)
        self.assertEqual(sa.summarize([{"time_tag": "2026-10-15T10:00:00Z", "energy": ">=10 MeV"}])["energies"], [">=10 MeV"])


class Feed(Base):
    def test_feed_is_valid_rss(self):
        self.f.data[self.cfg["geomagnetic"]["kp"]["url"]] = [{"time_tag": ts(60, "T"), "Kp": 8.0}]
        self.cycle()
        root = ET.parse(self.cfg["feed"]["output_path"]).getroot()
        self.assertEqual(root.tag, "rss")
        items = root.findall("./channel/item")
        self.assertEqual(len(items), 1)
        for tag in ("title", "link", "guid", "pubDate", "description", "category"):
            self.assertIsNotNone(items[0].find(tag), tag)

    def test_empty_feed_valid(self):
        self.cycle()
        root = ET.parse(self.cfg["feed"]["output_path"]).getroot()
        self.assertEqual(root.findall("./channel/item"), [])


if __name__ == "__main__":
    unittest.main()
