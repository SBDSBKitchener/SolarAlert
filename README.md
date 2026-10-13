# Solar & Geomagnetic Alerts RSS

Polls NOAA SWPC and HEK, applies thresholds from `config.toml`, and writes a static `feed.xml`
that anyone can subscribe to. Python 3.11+, no third-party packages.

## Run

```bash
python3 solar_alerts.py --check-config      # validate your edits
python3 solar_alerts.py                      # one cycle
python3 -m unittest -v                       # offline tests (no network)
```

Schedule it every 5 minutes, e.g. cron:

```
*/5 * * * * cd /opt/solar-alerts && /usr/bin/python3 solar_alerts.py >> run.log 2>&1
```

The feed is written to `site/feed.xml` (`feed.output_path`); point your web server at that directory, and set
`feed.self_url` to the public URL. Subscribers just add that URL to their reader.

## Free hosting on GitHub (Pages + Actions, public repo)

Layout expected in the repository root:

```
solar_alerts.py  config.toml  test_solar_alerts.py  README.md  .gitignore
site_static/index.html
.github/workflows/solar-alerts.yml
```

One-time setup:

1. Create a **public** repository and push these files to `main`.
2. In `config.toml`, replace `YOUR-USER` / `YOUR-REPO` in `feed.link` and `feed.self_url`
   (feed URL will be `https://YOUR-USER.github.io/YOUR-REPO/feed.xml`) and set a real contact in
   `runtime.user_agent`.
3. **Settings > Pages > Build and deployment > Source = "GitHub Actions".**
4. **Settings > Actions > General > Workflow permissions**: allow read and write (org policies may restrict this).
5. **Actions > Solar alerts feed > Run workflow** once. Check the run log, then open the feed URL.

How it works:

* Every 5 minutes (offset to :03, :08, ... to dodge the busy top of the hour) the workflow restores the
  de-duplication database from an orphan `state` branch, runs the script, and writes `site/feed.xml`.
* It deploys to Pages **only when the feed changed**, and saves state **only after a successful deploy**.
  If a deploy fails, the same alerts are detected again on the next run instead of being lost.
* The `state` branch holds a single commit that is force-pushed, so it never grows. It contains only the
  database and the current feed.
* Editing `config.toml` (or the code) and pushing to `main` runs the tests and republishes immediately.
  Thresholds are therefore changed by committing to `config.toml`.

Things to know:

* GitHub's scheduler is best effort: runs can be 10-30+ minutes late under load, and a delayed run
  still catches events because every rule looks back over a window (30 minutes for wind/protons,
  6 hours for flares and Kp). For tighter timing, trigger the workflow from an external cron service
  through the `workflow_dispatch` REST endpoint.
* Public-repo scheduled workflows are disabled after 60 days without repository activity. The workflow adds
  an empty "keepalive" commit to `main` after 45 days; this is best effort (it fails under branch protection
  and I could not verify that GitHub counts bot commits as activity). If the schedule ever stops, re-enable it
  in the Actions tab, or push any commit.
* The repo, the `state` branch and the feed are public.

## Administering thresholds

Everything is in `config.toml` and is re-read every cycle; run `--check-config` after editing.
Every rule has `enabled`; threshold rules also have `threshold`, `direction`
(`above` = value > threshold, `below` = value < threshold), `lookback_minutes`, `cooldown_minutes`.

| Alert | Setting |
|---|---|
| Flare class | `[flares] min_class` (flares are alerted at >= this class) |
| Earth-facing limits | `full_facing_max_longitude`, `partial_facing_max_longitude` (degrees from disk centre) |
| Filament eruption | `[filaments]` (same facing settings) |
| Coronal hole stream | `[geomagnetic.coronal_hole] include_regex / exclude_regex` |
| Forecast storm | `[geomagnetic.storm_forecast] min_g_level` |
| Actual Kp | `[geomagnetic.kp] min_kp` (>=) |
| Protons | `[protons.ge10mev]` (100 pfu), `[protons.ge100mev]` (1 pfu) |
| Solar wind | `[solar_wind.speed / density / bt / bz]` (Bz uses `direction = "below"`) |

## How duplicates are avoided

* Discrete events (flares, filaments, Kp, forecasts) are de-duplicated by source ID; flares/filaments
  reported by several HEK sources are merged if their times fall within 15/30 minutes.
* Continuous conditions (solar wind, protons) alert once per episode: no new alert until the value
  has dropped back below the threshold **and** `cooldown_minutes` has passed.

## Data sources and known limitations (please read)

| Rule | Source |
|---|---|
| Flares, filament eruptions | HEK `FL` / `FE` events (position from `hgs_x`/`hgs_y`) |
| Coronal hole stream | Keyword match on SWPC forecast discussion text |
| G-storm forecast | SWPC `alerts.json` (WATCH "Category Gx Predicted", WARNING "K-index of N expected") |
| Kp | `noaa-planetary-k-index.json` |
| Protons | GOES `integral-protons-1-day.json` |
| Solar wind | DSCOVR/ACE `plasma-5-minute.json`, `mag-5-minute.json` |

* **Written without live network access.** The logic is tested against fake data in the same formats;
  before going public, run one cycle with `-v` against the real endpoints and compare a few items with
  swpc.noaa.gov. The endpoint URLs and the HEK query are the most likely things to need tweaking.
* **Earth-facing is an approximation** from reported longitude. Events with no position yet are ignored by
  default (`unknown_location = "ignore"`); the position usually appears in HEK within minutes and is
  caught on a later poll.
* **Filament eruptions** depend on HEK having detections (automated + human). There is no official NOAA
  "filament eruption" alert.
* **Coronal hole detection is a text heuristic** and may need regex tuning after you see real output.
* HEK/SWPC are public services: keep polling at 5 minutes or slower and set a real contact in `user_agent`.
