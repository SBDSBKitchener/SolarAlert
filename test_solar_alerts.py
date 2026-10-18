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
        cfg["flares"]["impulsive"]["warning_min_class"] = "Q9"
        cfg["solar_wind"]["bz"]["direction"] = "sideways"
        del cfg["geomagnetic"]["kp"]["min_kp"]
        with self.assertRaises(sa.ConfigError) as cm:
            sa.validate_config(cfg)
        msg = str(cm.exception)
        self.assertIn("flares.impulsive.warning_min_class", msg)
        self.assertIn("solar_wind.bz.direction", msg)
        self.assertIn("geomagnetic.kp.min_kp", msg)

    def test_goes_class(self):
        self.assertGreater(sa.goes_flux("X1.0"), sa.goes_flux("M9.9"))
        self.assertAlmostEqual(sa.goes_flux("m5.0"), 5e-5)
        self.assertIsNone(sa.goes_flux("nonsense"))


class SolarEvents(Base):
    def ev(self, cls, lon, lat=10, minutes_ago=30, frm="SWPC", kb="a", typ="FL", duration=None, ar=None):
        e = {"event_type": typ, "event_starttime": ts(minutes_ago + 10, "T"), "event_peaktime": ts(minutes_ago, "T"),
             "hgs_x": lon, "hgs_y": lat, "frm_name": frm, "kb_archivid": kb}
        if duration is not None:                                    # reported end = start + duration minutes
            e["event_endtime"] = ts(minutes_ago + 10 - duration, "T")
        if ar:
            e["ar_noaanum"] = ar
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

    def test_swpc_placeholder_position_is_unknown_not_disk_centre(self):
        # SWPC lists flares with hgs 0,0 when it has no position (seen live on 15 Oct 2026)
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M7.1", 0, lat=0, kb="p", frm="SWPC")]}
        self.assertEqual(self.cycle(), [])
        # the same flare from another source with a real (limb) position is still not Earth-facing
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M7.1", 0, lat=0, kb="p", frm="SWPC"),
                                                          self.ev("M7.1", 80, lat=10, kb="q", frm="SSW")]}
        self.assertEqual(len(self.cycle()), 1)                              # partial (near-limb) only
        # and one with a real central position alerts once, not twice
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M9.0", 0, lat=0, kb="r", frm="SWPC", minutes_ago=200),
                                                          self.ev("M9.0", 1, lat=-12, kb="s", frm="SSW", minutes_ago=200)]}
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("Earth-facing", new[0].title)

    def test_threshold_is_configurable(self):
        self.f.data[self.cfg["hek"]["url"]] = {"result": [self.ev("M2.0", 10, kb="4")]}
        self.assertEqual(self.cycle(), [])
        self.cfg["flares"]["impulsive"]["warning_min_class"] = "M1.0"
        self.assertEqual(len(self.cycle()), 1)              # pending type: the more sensitive threshold applies

    # ---- levels and impulsive / long-duration typing ----------------------------------------------------
    def flare(self, *events):
        self.f.data[self.cfg["hek"]["url"]] = {"result": list(events)}
        return self.cycle()

    def test_x_class_is_an_alert_and_m_class_a_warning(self):
        new = self.flare(self.ev("X2.1", 10, kb="x", duration=20), self.ev("M6.0", 12, kb="m", minutes_ago=200, duration=20))
        by = {a.title.split(":")[0]: a for a in new}
        self.assertEqual(set(by), {"ALERT", "WARNING"})
        self.assertIn("X2.1", by["ALERT"].title)
        self.assertEqual(by["ALERT"].level, "alert")
        self.assertIn("impulsive", by["ALERT"].title)

    def test_duration_decides_the_type(self):
        new = self.flare(self.ev("M6.0", 10, kb="a", duration=20), self.ev("M6.0", 12, kb="b", minutes_ago=300, duration=90))
        titles = " | ".join(self.titles(new))
        self.assertIn("impulsive solar flare", titles)
        self.assertIn("long-duration solar flare", titles)

    def test_thresholds_per_type(self):
        self.cfg["flares"]["long_duration"]["warning_min_class"] = "X1.0"       # long-duration M flares no longer warn
        self.cfg["flares"]["long_duration"]["alert_min_class"] = "X5.0"
        new = self.flare(self.ev("M6.0", 10, kb="a", duration=20), self.ev("M6.0", 12, kb="b", minutes_ago=300, duration=90),
                         self.ev("X2.0", 14, kb="c", minutes_ago=400, duration=90))
        self.assertEqual(len(new), 2, self.titles(new))
        levels = {("impulsive" in a.title): a.level for a in new}
        self.assertEqual(levels[True], "warning")                                  # impulsive M6.0 still warns
        self.assertTrue(any("long-duration" in a.title and a.level == "warning" for a in new))   # long X2.0 = warning only

    def test_off_disables_a_level(self):
        self.cfg["flares"]["impulsive"]["alert_min_class"] = "off"
        new = self.flare(self.ev("X3.0", 10, kb="a", duration=20))
        self.assertEqual(new[0].level, "warning")                                  # still above the warning class

    def test_pending_flare_gets_an_update_when_its_type_is_known(self):
        first = self.flare(self.ev("X1.5", 10, kb="p"))                            # no end time yet
        self.assertEqual(len(first), 1)
        self.assertIn("duration type pending", first[0].title)
        self.assertEqual(first[0].level, "alert")
        self.assertEqual(self.flare(self.ev("X1.5", 10, kb="p")), [])               # nothing new
        upd = self.flare(self.ev("X1.5", 10, kb="p", duration=25))                 # end reported: impulsive
        self.assertEqual(len(upd), 1, self.titles(upd))
        self.assertIn("impulsive", upd[0].title)
        self.assertIn("update", upd[0].title)
        self.assertEqual(self.flare(self.ev("X1.5", 10, kb="p", duration=25)), [])

    def test_unfinished_flare_past_the_limit_is_long_duration(self):
        new = self.flare(self.ev("M6.0", 10, kb="q", minutes_ago=5))
        self.assertIn("pending", new[0].title)
        e = self.ev("M7.0", 10, kb="r", minutes_ago=60)
        e["event_starttime"] = ts(100, "T")                                         # started 100 min ago, no end yet
        new = self.flare(e)
        self.assertIn("long-duration", new[-1].title)

    def test_missing_end_and_region_are_filled_from_another_source(self):
        new = self.flare(self.ev("M8.0", 10, kb="s1", frm="SWPC"),
                         self.ev("M8.0", 11, kb="s2", frm="SSW", duration=90, ar=4551))
        self.assertEqual(len(new), 1)
        self.assertIn("long-duration", new[0].title)
        self.assertIn("AR4551", new[0].title)

    def test_flare_threshold_ordering_validated(self):
        cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
        cfg["flares"]["impulsive"]["warning_min_class"] = "X2.0"
        with self.assertRaises(sa.ConfigError):
            sa.validate_config(cfg)

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
                            "influence Earth on 14-15 Oct, reaching G3 (Strong) storm levels. No solar flares are expected.")
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
    """Fixtures mirror NOAA's solar_regions.json records (AR4551: S15E07, BGD, X-flare chance 10% on 14 Oct 2026)."""

    def rec(self, region=4551, days_ago=1, loc="S15E07", mag="BGD", lon=None, base=NOW, **kw):
        d = (base - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        r = {"observed_date": d, "region": region, "location": loc, "mag_class": mag, "spot_class": "Eki",
             "area": 500, "number_spots": 23, "m_flare_probability": 25, "x_flare_probability": 5,
             "m_xray_events": 0, "x_xray_events": 0}
        if lon is not None:
            r["longitude"] = lon
        r.update(kw)
        return r

    def feed(self, *records):
        self.f.data[self.cfg["sunspots"]["url"]] = list(records)

    def flagged(self, *records, now=NOW):
        self.feed(*records)
        return self.cycle(now)

    # ---- the real AR4551 history ------------------------------------------------------------------
    AR4551 = [  # (observed, location, class, area, spots, X-flare %, M-flare %)  from NOAA, 9-15 Oct 2026
        ("2026-10-09", "S13E71", "B", 170, 2, None, None), ("2026-10-10", "S14E61", "B", 360, 12, None, None),
        ("2026-10-11", "S15E47", "BG", 400, 14, None, None), ("2026-10-12", "S15E34", "B", 400, 13, None, None),
        ("2026-10-13", "S15E21", "BG", 480, 9, None, None), ("2026-10-14", "S15E07", "BGD", 500, 23, 10, 25),
        ("2026-10-15", "S15W05", "BGD", 620, 32, 10, 25)]

    def replay(self):
        records, first = [], None
        for obs, loc, cls, area, spots, xp, mp in self.AR4551:
            records.append({"observed_date": obs, "region": 4551, "location": loc, "mag_class": cls, "area": area,
                            "number_spots": spots, "x_flare_probability": xp, "m_flare_probability": mp,
                            "m_xray_events": 0, "x_xray_events": 0, "spot_class": "Eki"})
            day = datetime.strptime(obs, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
            new = self.flagged(*records, now=day)
            if new and first is None:
                first = (obs, new[0])
            elif new:
                self.fail(f"unexpected second alert on {obs}: {new[0].title}")
        self.assertIsNotNone(first)
        return first

    def test_ar4551_replay(self):
        obs, alert = self.replay()
        self.assertEqual(obs, "2026-10-14")          # E71/E61 are outside the 60 deg cone; 11-13 Oct met nothing
        for part in ("AR4551", "beta-gamma-delta", "X-flare chance 10%", "Earth-facing", "S15E07"):
            self.assertIn(part, alert.title)
        # Eki is in the default McIntosh list, so its growth counts; X-flare chance 10% makes it a WARNING
        self.assertIn("rapid growth", alert.title)
        self.assertTrue(alert.title.startswith("WARNING: "))
        self.assertEqual(alert.level, "warning")
        self.assertIn("McIntosh class Eki", alert.description)

    def test_ar4551_replay_without_its_class_in_the_list(self):
        self.cfg["sunspots"]["mcintosh_classes"] = ["Fkc", "Ekc", "Fki"]
        obs, alert = self.replay()
        self.assertEqual(obs, "2026-10-14")
        self.assertNotIn("rapid growth", alert.title)

    # ---- individual attributes: each can flag a region on its own --------------------------------
    def test_class_alone_flags_without_any_flare_history(self):
        new = self.flagged(self.rec())
        self.assertEqual(len(new), 1, self.titles(new))
        self.assertIn("beta-gamma-delta", new[0].title)
        self.assertEqual(self.cycle(), [])                      # not repeated on the next poll

    def test_x_probability_threshold_and_m_probability_ignored(self):
        self.assertEqual(len(self.flagged(self.rec(mag="B", x_flare_probability=10))), 1)
        self.assertEqual(self.flagged(self.rec(region=2, mag="B", x_flare_probability=9, m_flare_probability=95)), [])
        self.assertEqual(self.flagged(self.rec(region=3, mag="B", x_flare_probability=None, m_flare_probability=99)), [])

    def test_flare_history_flags_but_absence_does_not_disqualify(self):
        self.assertEqual(len(self.flagged(self.rec(mag="B", m_xray_events=1))), 1)
        self.assertEqual(len(self.flagged(self.rec(region=2, mag="B", x_xray_events=1))), 1)
        self.assertEqual(self.flagged(self.rec(region=3, mag="B")), [])           # nothing met: not flagged
        self.assertEqual(len(self.flagged(self.rec(region=4, mag="BGD", m_xray_events=0))), 1)   # BGD, no flares

    def test_rapid_growth(self):
        def grow(prev_area, prev_spots, area, spots, gap=1, region=1):
            return self.flagged(self.rec(region=region, mag="B", days_ago=1 + gap, area=prev_area, number_spots=prev_spots,
                                         spot_class="Fkc"),
                                self.rec(region=region, mag="B", days_ago=1, area=area, number_spots=spots,
                                         spot_class="Fkc"))
        self.assertEqual(len(grow(400, 10, 500, 11)), 1)                          # area +100
        self.assertEqual(grow(400, 10, 499, 19, region=2), [])                    # +99 area, +9 spots: not enough
        self.assertEqual(len(grow(400, 10, 450, 20, region=3)), 1)                # spots +10
        self.assertEqual(grow(400, 10, 550, 10, gap=2, region=4), [])             # +150 over 2 days = 75/day
        self.assertEqual(len(grow(400, 10, 650, 10, gap=2, region=5)), 1)         # +250 over 2 days = 125/day
        self.assertEqual(grow(400, 10, 900, 40, gap=3, region=6), [])             # previous record too old to compare
        new = grow(100, 3, 350, 12, region=7)
        self.assertIn("area up 250 millionths", new[0].description)

    def test_region_with_no_history_cannot_show_growth(self):
        self.assertEqual(self.flagged(self.rec(mag="B", area=900, number_spots=40)), [])

    # ---- combining ---------------------------------------------------------------------------------
    def test_description_lists_every_reason(self):
        new = self.flagged(self.rec(x_flare_probability=12, m_xray_events=2))
        d = new[0].description
        for part in ("BGD (beta-gamma-delta) magnetic classification", "X-class flare probability 12%",
                     "2 M/X-class flares already reported", "Earth-facing at S15E07"):
            self.assertIn(part, d)
        self.assertNotIn("M-class flare probability", d)

    def test_levels_per_attribute(self):
        sp = self.cfg["sunspots"]
        a = self.flagged(self.rec(region=1))                                        # BGD only
        self.assertEqual((a[0].level, a[0].title[:7]), ("watch", "WATCH: "))
        b = self.flagged(self.rec(region=2, mag="B", x_flare_probability=12))       # X chance only
        self.assertEqual((b[0].level, b[0].title[:9]), ("warning", "WARNING: "))
        c = self.flagged(self.rec(region=3, x_flare_probability=12))                # both: highest wins
        self.assertEqual(c[0].level, "warning")
        sp["levels"]["xprob"] = "alert"                                             # admin can re-level an attribute
        d = self.flagged(self.rec(region=4, mag="B", x_flare_probability=12))
        self.assertEqual(d[0].level, "alert")
        sp["levels"]["class"] = "off"                                               # "off" ignores the attribute
        self.assertEqual(self.flagged(self.rec(region=5)), [])

    def test_update_reports_a_raised_level(self):
        first = self.poll(0, mag="BGD")                                             # WATCH
        self.assertEqual(first[0].level, "watch")
        upd = self.poll(1, mag="BGD", x_flare_probability=12)                       # now a WARNING
        self.assertEqual(upd[0].level, "warning")
        self.assertTrue(upd[0].title.startswith("WARNING: "))
        self.assertIn("raised from WATCH to WARNING", upd[0].description)

    def test_attributes_can_be_turned_off(self):
        sp = self.cfg["sunspots"]
        everything = [self.rec(region=1, days_ago=2, area=300, number_spots=5),
                      self.rec(region=1, x_flare_probability=40, m_xray_events=3, area=700, number_spots=30)]
        sp.update(dangerous_classes=[], growth_area_min_increase=0, growth_spots_min_increase=0,
                  x_flare_probability_min=0, flare_history_min_events=0)
        self.assertEqual(self.flagged(*everything), [])
        sp["x_flare_probability_min"] = 10
        self.assertEqual(len(self.flagged(*everything)), 1)


    # ---- McIntosh gate on rapid growth ---------------------------------------------------------------
    def grown(self, spot_class, region=1, **kw):
        return self.flagged(self.rec(region=region, mag="B", days_ago=2, area=300, number_spots=5, spot_class=spot_class),
                            self.rec(region=region, mag="B", days_ago=1, area=600, number_spots=25, spot_class=spot_class, **kw))

    def test_growth_counts_only_for_listed_mcintosh_classes(self):
        self.assertEqual(self.grown("Dai", region=2), [])
        for i, cls in enumerate(("Fkc", "Ekc", "Eki", "Fki"), start=3):
            self.assertEqual(len(self.grown(cls, region=i)), 1, cls)
        self.assertEqual(len(self.grown("fKC", region=9)), 1)                   # case-insensitive

    def test_mcintosh_list_is_configurable_and_empty_turns_growth_off(self):
        self.cfg["sunspots"]["mcintosh_classes"] = ["Eki"]
        self.assertEqual(len(self.grown("Eki", region=1)), 1)
        self.assertEqual(self.grown("Fkc", region=2), [])
        self.cfg["sunspots"]["mcintosh_classes"] = []
        self.assertEqual(self.grown("Eki", region=3), [])

    def test_mcintosh_gate_does_not_affect_other_attributes(self):
        new = self.flagged(self.rec(mag="BGD", spot_class="Axx"), self.rec(region=2, mag="B", spot_class="Axx",
                                                                           x_flare_probability=20))
        self.assertEqual(len(new), 2)

    def test_mcintosh_class_validation(self):
        for bad in (["Fkcc"], ["Zkc"], ["Fxx1"], ["Fk"], "Fkc"):
            cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
            cfg["sunspots"]["mcintosh_classes"] = bad
            with self.assertRaises(sa.ConfigError, msg=str(bad)):
                sa.validate_config(cfg)

    # ---- announcing newly met attributes -------------------------------------------------------------
    def day(self, n):
        return NOW + timedelta(days=n)

    def poll(self, n, **kw):
        """One daily poll n days after NOW with a single region record dated that day."""
        d = self.day(n)
        return self.flagged(self.rec(days_ago=0, base=d, **kw), now=d)

    def test_newly_met_attribute_is_announced_once(self):
        first = self.poll(0, mag="B", x_flare_probability=12)                       # flagged by X chance only
        self.assertEqual(len(first), 1)
        self.assertIn("X-flare chance 12%", first[0].title)
        upd = self.poll(1, mag="BGD", x_flare_probability=12)                       # class newly met
        self.assertEqual(len(upd), 1, self.titles(upd))
        self.assertIn("update", upd[0].title)
        self.assertIn("beta-gamma-delta magnetic class", upd[0].title)
        self.assertNotIn("X-flare chance", upd[0].title)                              # already announced
        self.assertIn("already flagged", upd[0].description)
        self.assertIn("X-class flare probability 12%", upd[0].description)           # full picture is in the body
        self.assertEqual(self.poll(2, mag="BGD", x_flare_probability=12), [])         # nothing new
        self.assertEqual(self.cycle(self.day(2)), [])                                 # same poll again: still nothing

    def test_several_attributes_in_one_update_and_sticky_within_episode(self):
        self.poll(0, mag="B", x_flare_probability=12)
        upd = self.poll(1, mag="BGD", x_flare_probability=12, m_xray_events=2)
        self.assertEqual(len(upd), 1)
        self.assertIn("beta-gamma-delta magnetic class", upd[0].title)
        self.assertIn("M/X flares already", upd[0].title)
        # flares attribute drops out and returns later in the same episode: not announced again
        self.assertEqual(self.poll(2, mag="BGD", x_flare_probability=12, m_xray_events=0), [])
        self.assertEqual(self.poll(3, mag="BGD", x_flare_probability=12, m_xray_events=3), [])

    def test_growth_announced_when_it_newly_appears(self):
        self.cfg["sunspots"]["mcintosh_classes"] = ["Fkc", "Ekc", "Fki"]
        d0, d1 = self.day(0), self.day(1)
        self.flagged(self.rec(days_ago=0, base=d0, mag="BGD", spot_class="Fkc", area=300, number_spots=5), now=d0)
        upd = self.flagged(self.rec(days_ago=1, base=d1, mag="BGD", spot_class="Fkc", area=300, number_spots=5),
                           self.rec(days_ago=0, base=d1, mag="BGD", spot_class="Fkc", area=520, number_spots=9), now=d1)
        self.assertEqual(len(upd), 1)
        self.assertIn("rapid growth", upd[0].title)
        self.assertIn("McIntosh class Fkc", upd[0].description)

    def test_new_episode_announces_everything_again(self):
        self.poll(0, mag="BGD", x_flare_probability=12)
        self.assertEqual(self.poll(1, mag="B", x_flare_probability=0), [])          # clears
        again = self.poll(3, mag="BGD", x_flare_probability=12)                     # cooldown (24 h) elapsed
        self.assertEqual(len(again), 1)
        self.assertNotIn("update", again[0].title)
        self.assertIn("beta-gamma-delta magnetic class", again[0].title)
        self.assertIn("X-flare chance 12%", again[0].title)

    def test_update_respects_earth_facing(self):
        first = self.poll(0, mag="BGD", x_flare_probability=12)
        self.assertEqual(len(first), 1)
        self.assertNotIn("update", first[0].title)
        self.assertEqual(self.poll(1, mag="BGD", x_flare_probability=12), [])
        self.assertEqual(self.poll(2, mag="BGD", x_flare_probability=12, m_xray_events=1, loc="S15E80"), [])  # limb

    def test_episode_started_before_this_feature_is_seeded_quietly(self):
        # simulate the deployed state: region already flagged and active, nothing recorded as announced
        self.store.set_active("sunspot_4551", True)
        self.store.commit()
        self.assertEqual(self.poll(0, mag="BGD", x_flare_probability=12), [])        # no spurious "update"
        upd = self.poll(1, mag="BGD", x_flare_probability=12, m_xray_events=1)       # a genuinely new attribute
        self.assertEqual(len(upd), 1)
        self.assertIn("M/X flares already", upd[0].title)

    # ---- geometry, age, other classes ---------------------------------------------------------------
    def test_must_be_earth_facing(self):
        everything = dict(x_flare_probability=40, m_xray_events=3)
        self.assertEqual(self.flagged(self.rec(loc="S15E75", **everything)), [])
        self.assertEqual(len(self.flagged(self.rec(region=2, loc="S15E60", **everything))), 1)
        self.assertEqual(len(self.flagged(self.rec(region=3, loc="N10W60", **everything))), 1)
        self.assertEqual(self.flagged(self.rec(region=4, loc="N09W61", **everything)), [])
        self.cfg["sunspots"]["max_longitude"] = 80                                           # admin widens the cone
        self.assertEqual(len(self.flagged(self.rec(region=5, loc="N09W75"), self.rec(region=6, loc="S01E79"))), 2)

    def test_older_bgd_record_superseded(self):
        self.assertEqual(self.flagged(self.rec(days_ago=2, mag="BGD"), self.rec(days_ago=1, mag="BG")), [])

    def test_other_classes_configurable(self):
        recs = [self.rec(mag="BD", region=1), self.rec(mag="GD", region=2), self.rec(mag="BG", region=3)]
        self.assertEqual(self.flagged(*recs), [])
        self.cfg["sunspots"]["dangerous_classes"] = ["bgd", "GD", "BD"]                      # case-insensitive
        self.assertEqual(len(self.cycle()), 2)

    def test_location_fallback_to_longitude_field(self):
        new = self.flagged(self.rec(region=1, loc="", lon=7), self.rec(region=2, loc="", lon=-67),
                           self.rec(region=3, loc="", lon=None))
        self.assertEqual(len(new), 1)
        self.assertIn("AR1", new[0].title)

    def test_stale_record_ignored(self):
        self.assertEqual(self.flagged(self.rec(days_ago=6)), [])

    # ---- episodes -----------------------------------------------------------------------------------
    def test_no_repeat_next_day_then_realert_after_clearing_and_cooldown(self):
        self.feed(self.rec(days_ago=1))
        self.assertEqual(len(self.cycle(NOW)), 1)
        day2 = NOW + timedelta(days=1)
        self.feed(self.rec(days_ago=1), self.rec(days_ago=0, base=day2))        # still flagged today
        self.assertEqual(self.cycle(day2), [])                                  # same episode
        day3 = NOW + timedelta(days=2)
        self.feed(self.rec(days_ago=0, mag="BG", base=day3))                    # nothing met any more
        self.assertEqual(self.cycle(day3), [])
        early = NOW + timedelta(days=2, hours=1)
        self.feed(self.rec(days_ago=0, base=early))
        self.cfg["sunspots"]["cooldown_minutes"] = 10 * 24 * 60                 # long cooldown blocks re-alert
        self.assertEqual(self.cycle(early), [])
        self.cfg["sunspots"]["cooldown_minutes"] = 1440
        self.feed(self.rec(days_ago=0, mag="BG", base=early))
        self.cycle(early)                                                       # clears again
        day4 = NOW + timedelta(days=3)
        self.feed(self.rec(days_ago=0, base=day4))                              # flagged again, cooldown elapsed
        self.assertEqual(len(self.cycle(day4)), 1)

    def test_disabled_and_failed_source(self):
        self.feed(self.rec())
        self.cfg["sunspots"]["enabled"] = False
        self.assertEqual(self.cycle(), [])
        self.cfg["sunspots"]["enabled"] = True
        self.f.data[self.cfg["sunspots"]["url"]] = OSError("down")
        self.assertEqual(self.cycle(), [])                                      # no crash, feed still written

    def test_health_notes_show_why(self):
        self.feed(self.rec(region=1, x_flare_probability=12), self.rec(region=2, loc="S10E80"))
        self.cycle()
        notes = {n["region"]: n for n in sa._NOTES["sunspots"]}
        self.assertTrue(notes[1]["flagged"])
        self.assertIn("X-flare chance 12%", notes[1]["reasons"])
        self.assertFalse(notes[2]["earth_facing"])

    def test_bad_sunspot_config_rejected(self):
        for bad in ({"dangerous_classes": "BGD"}, {"dangerous_classes": [""]}, {"max_longitude": 200},
                    {"x_flare_probability_min": -1}, {"growth_window_days": 0}):
            cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
            cfg["sunspots"].update(bad)
            with self.assertRaises(sa.ConfigError, msg=str(bad)):
                sa.validate_config(cfg)
        cfg = sa.load_config(os.path.join(HERE, "test_config.toml"))
        cfg["sunspots"]["dangerous_classes"] = []                               # allowed: turns the class test off
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
    "A recurrent positive polarity CH HSS is expected to arrive on 18 Oct. Storm levels up to G3 (Strong) are possible.",
    "Active conditions reaching G3 (Strong) levels are likely on 19-20 Oct due to a positive polarity coronal hole high speed stream.",
    "Solar wind speeds are expected to increase to near 600 km/s late on 17 Oct as a positive polarity CH HSS "
    "rotates into a geoeffective position, with G4 (Severe) storming.",
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

    def test_forecast_strength_must_reach_min_g_level(self):
        weak = ("Active conditions late on 19 Oct are expected to increase to G1 (Minor) geomagnetic storm levels "
                "on 20 Oct due to anticipated effects from a positive polarity CH HSS.")
        g2 = weak.replace("G1 (Minor)", "G2 (Moderate)")
        g3 = weak.replace("G1 (Minor)", "G3 (Strong)")
        nolevel = "A positive polarity CH HSS is expected to arrive on 19 Oct."
        later = nolevel + " Storm levels to G3 (Strong) are likely."
        for body, expect in ((weak, 0), (g2, 0), (g3, 1), (nolevel, 0), (later, 1)):
            alerts, conds = self.direct(body)
            self.assertEqual(len(alerts), expect, body)
            self.assertEqual(conds["coronal_hole"], bool(expect), body)
        self.assertIn("G3", self.direct(g3)[0][0].title)
        self.cfg["geomagnetic"]["coronal_hole"]["min_g_level"] = 1               # admin lowers the bar
        self.assertEqual(len(self.direct(weak)[0]), 1)
        self.assertEqual(self.direct(nolevel)[0], [])                            # no G-level named at all

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
        self.assertEqual(notes[-1]["forecast_g_level"], 3)


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


class RealtimeSolarWind(Base):
    """SWPC rtsw one-minute files: dict rows from several spacecraft, one flagged active."""

    def setUp(self):
        super().setUp()
        sw = self.cfg["solar_wind"]
        for name, field in (("speed", "proton_speed"), ("density", "proton_density"), ("bt", "bt"), ("bz", "bz_gsm")):
            sw[name]["field"], sw[name]["lookback_minutes"] = field, 480
        self.plasma, self.mag = sw["plasma_url"], sw["mag_url"]

    def wind(self, *rows):      # (minutes_ago, source, active, speed, density)
        self.f.data[self.plasma] = [{"time_tag": ts(m, "T"), "source": src, "active": act,
                                     "proton_speed": v, "proton_density": d} for m, src, act, v, d in rows]

    def magnet(self, *rows):    # (minutes_ago, source, active, bt, bz)
        self.f.data[self.mag] = [{"time_tag": ts(m, "T"), "source": src, "active": act,
                                  "bt": bt, "bz_gsm": bz} for m, src, act, bt, bz in rows]

    def test_inactive_spacecraft_ignored(self):
        self.wind((5, "IMAP", False, 900.0, 3.0), (5, "ACE", False, 950.0, 3.0), (5, "SOLAR1", True, 500.0, 3.0))
        self.assertEqual(self.cycle(), [])

    def test_active_spacecraft_alerts_once(self):
        self.wind((60, "SOLAR1", True, 850.0, 3.0), (60, "IMAP", False, 400.0, 3.0), (5, "SOLAR1", True, 500.0, 3.0))
        new = self.cycle()
        self.assertEqual(len(new), 1)
        self.assertIn("850", new[0].title)
        self.assertEqual(self.cycle(NOW + timedelta(hours=1)), [])

    def test_magnetic_field_rules(self):
        self.magnet((10, "SOLAR1", True, 45.0, -42.0), (10, "IMAP", False, 20.0, -90.0), (3, "SOLAR1", True, 50.0, -44.0))
        titles = self.titles(self.cycle())
        self.assertEqual(len(titles), 2, titles)
        self.assertTrue(any("Bt" in t for t in titles) and any("Bz" in t for t in titles))

    def test_null_readings_ignored(self):
        self.wind((5, "SOLAR1", True, None, None))
        self.assertEqual(self.cycle(), [])

    def test_falls_back_to_all_rows_if_nothing_active(self):
        self.wind((5, "IMAP", False, 850.0, 3.0))
        self.assertEqual(len(self.cycle()), 1)

    def test_only_active_can_be_turned_off(self):
        self.cfg["solar_wind"]["only_active"] = False
        self.wind((5, "IMAP", False, 900.0, 3.0), (5, "SOLAR1", True, 500.0, 3.0))
        self.assertEqual(len(self.cycle()), 1)


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
