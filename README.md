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

* **GitHub's built-in scheduler is slow.** Measured on this repo, the "every 5 minutes" schedule actually
  runs every 2-6 hours (average about 4). To make up for it, every rule looks back far enough to cover the gap
  (8 hours for solar wind and protons, 12 for flares and Kp, 24 for filaments), and a spike is reported once, when
  next seen. Alerts can still be hours late. For near-real-time alerts use the external trigger below.
* Public-repo scheduled workflows are disabled after 60 days without repository activity. The workflow adds
  an empty "keepalive" commit to `main` after 45 days; this is best effort (it fails under branch protection
  and I could not verify that GitHub counts bot commits as activity). If the schedule ever stops, re-enable it
  in the Actions tab, or push any commit.
* The repo, the `state` branch and the feed are public.

## External trigger (recommended: runs the workflow every 5 minutes)

A free timer service can start the workflow on time instead of waiting for GitHub's scheduler. Both schedules
can stay on: the workflow never runs two at once, and duplicates are ignored.

1. GitHub > Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token.
   Repository access: **only this repository**. Permissions: **Actions: Read and write** (nothing else).
   Choose an expiry (e.g. 1 year) and note the date; the trigger stops working when it expires.
2. On a service such as [cron-job.org](https://cron-job.org) (free) create a job that runs every 5 minutes:
   * URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/solar-alerts.yml/dispatches`
   * Method: `POST`, body: `{"ref":"main"}`
   * Headers: `Authorization: Bearer YOUR_TOKEN`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
   * A successful call returns `204`.
3. Keep the token secret: it lives only in that service, never in this repository.

Each dispatch creates one workflow run (about 30 seconds, free for public repositories).

## Health report

Every run saves `health.json` to the `state` branch (not the public website). It lists, for each data source,
whether it loaded, its size, row count, oldest and newest timestamp and a sample row, plus the result of each
check and the sentences the coronal hole rule considered and why. If an alert never appears, look here first:
`https://github.com/OWNER/REPO/blob/state/health.json`.

## Administering thresholds

Everything is in `config.toml` and is re-read every cycle; run `--check-config` after editing.
Every rule has `enabled`; threshold rules also have `threshold`, `direction`
(`above` = value > threshold, `below` = value < threshold), `lookback_minutes`, `cooldown_minutes`.

| Alert | Setting |
|---|---|
| Flares | `[flares.impulsive]` and `[flares.long_duration]`: `warning_min_class` (default M5.0) and `alert_min_class` (default X1.0); `"off"` disables a level. `long_duration_min_minutes` (default 60) splits the two types |
| Earth-facing limits | `full_facing_max_longitude`, `partial_facing_max_longitude` (degrees from disk centre) |
| Filament eruption | `[filaments]` (same facing settings) |
| Dangerous sunspot | `[sunspots]`: Earth-facing (`max_longitude`, default 60) and at least one of: `dangerous_classes` (default `["BGD"]`), rapid growth (`growth_area_min_increase`, `growth_spots_min_increase`, per day; counted only for McIntosh classes in `mcintosh_classes`, default `["Fkc", "Ekc", "Eki", "Fki"]`), `x_flare_probability_min` (default 10 %), `flare_history_min_events`. `[sunspots.levels]` sets the level of each attribute (`watch`/`warning`/`alert`/`off`). 0 or `[]` turns an attribute off |
| Coronal hole stream | `[geomagnetic.coronal_hole] include_regex / exclude_regex` (arrival wording only; ongoing or fading streams are excluded) and `min_g_level` (default 3: the forecast must name a storm level of G3 or more) |
| Forecast storm | `[geomagnetic.storm_forecast] min_g_level` |
| Actual Kp | `[geomagnetic.kp] min_kp` (>=) |
| Protons | `[protons.ge10mev]` (100 pfu), `[protons.ge100mev]` (1 pfu) |
| Solar wind | `[solar_wind.speed / density / bt / bz]` (Bz uses `direction = "below"`) |

## Alert levels

Every alert title starts with its level, and the feed item carries it as a second category (WATCH, WARNING, ALERT).

| Level | Meaning | Default triggers |
|---|---|---|
| WATCH | conditions are favourable | fast-growing Earth-facing sunspot (also BGD class, earlier M/X flaring) |
| WARNING | elevated risk | NOAA X-flare probability >= 10 % on an Earth-facing region; M5.0+ flare |
| ALERT | the event has happened | X-class flare (Earth-facing) |

A region's level is the highest among the attributes it meets, and an update alert says when the level was raised.

## Impulsive and long-duration flares

Flares are typed from the HEK start and end times: `long_duration_min_minutes` or longer is long-duration, shorter is impulsive.
While the end has not been reported the type is *pending*: the flare is judged by the more sensitive of the two sets of
thresholds, and an update alert follows when the type is known (it also reports a changed level). A flare still going after
`long_duration_min_minutes` is long-duration straight away. Thresholds are set separately per type, so for example
`[flares.long_duration] warning_min_class = "X1.0"` stops long-duration M flares from warning. Flare records from different
HEK sources are merged so a missing end time, position or active region is taken from another source.
Real HEK timing fields are listed under `flares` in `health.json`.

## How duplicates are avoided

* Discrete events (flares, filaments, Kp, forecasts) are de-duplicated by source ID; flares/filaments
  reported by several HEK sources are merged if their times fall within 15/30 minutes.
* Continuous conditions (solar wind, protons) alert once per episode: no new alert until the value
  has dropped back below the threshold **and** `cooldown_minutes` has passed.

## Data sources and known limitations (please read)

| Rule | Source |
|---|---|
| Flares, filament eruptions | HEK `FL` / `FE` events (position from `hgs_x`/`hgs_y`) |
| Dangerous sunspots | NOAA `solar_regions.json` (Solar Region Summary; latest record per region) |
| Coronal hole stream | Keyword match on SWPC forecast discussion text |
| G-storm forecast | SWPC `alerts.json` (WATCH "Category Gx Predicted", WARNING "K-index of N expected") |
| Kp | `noaa-planetary-k-index.json` |
| Protons | GOES `integral-protons-1-day.json` |
| Solar wind | SWPC `plasma-1-day.json`, `mag-1-day.json` (the `-5-minute` files hold only one reading) |

* **Written without live network access.** The logic is tested against fake data in the same formats;
  before going public, run one cycle with `-v` against the real endpoints and compare a few items with
  swpc.noaa.gov. The endpoint URLs and the HEK query are the most likely things to need tweaking.
* **Earth-facing is an approximation** from reported longitude. Events with no position yet are ignored by
  default (`unknown_location = "ignore"`); the position usually appears in HEK within minutes and is
  caught on a later poll.
* **Sunspot alerts lag by up to a day, and use any-of logic.** NOAA's region summary is one record per region per day. A region is flagged when it is Earth-facing and meets at least one enabled attribute (BGD class, rapid growth, X-class flare probability >= 10 %, or M/X flares already reported; growth and class give a WATCH, X-flare chance a WARNING); no attribute is required, so a region that has not flared yet is still flagged. NOAA's M-class probability is deliberately ignored. Rapid growth compares a region with its previous daily record and counts only if its McIntosh class (NOAA's `spot_class`) is in `mcintosh_classes`; an empty list turns growth off, and a class missing from the list (e.g. Dai) never triggers it. Each alert lists its reasons. While a region stays flagged you get one extra "update: now also ..." alert whenever an attribute is met that has not been announced yet (each attribute at most once per episode; an attribute that drops out and returns is not announced again). After it stops being flagged and `cooldown_minutes` has passed, the next episode announces everything again. Spot counts are noisy, so tune the growth thresholds from experience.
* **Filament eruptions** depend on HEK having detections (automated + human). There is no official NOAA
  "filament eruption" alert.
* **Coronal hole detection is a text heuristic.** It alerts only when the forecast names a storm level of at least `min_g_level` (default G3) in that sentence or the next, and only on arrival or enhancement wording and ignores streams that are already under way or fading. It was tuned on SWPC text from 13-15 Oct 2026; if it misses or over-reports, the sentences it saw are in `health.json` so the two regexes can be adjusted.
* HEK/SWPC are public services: keep polling at 5 minutes or slower and set a real contact in `user_agent`.
