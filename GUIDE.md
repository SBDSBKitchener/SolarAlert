# Solar & Geomagnetic Alerts: A Plain-Language Guide

Last updated 1 November 2026. This guide is for readers with only a basic knowledge of solar activity and geomagnetic storms. For setup and configuration, see [README.md](README.md).

## What this system is, and why it exists

This system is a free public news feed that tells you, in plain language, when the Sun does something that could affect Earth. It watches official space weather data around the clock and posts a short message to an RSS feed (a feed you can follow in any news reader or app) only when something crosses a threshold worth your attention.

The Sun is not a steady lamp. It throws out bursts of radiation (solar flares), huge clouds of charged gas (coronal mass ejections, or CMEs), and a constant stream of particles called the solar wind. When these reach Earth they can disturb Earth's magnetic field. Such a disturbance is called a geomagnetic storm. Most are harmless and produce nothing more than northern lights. The strongest can disrupt radio, GPS, satellites and, in rare cases, electrical power grids. The 1989 storm that blacked out Quebec's power system for about nine hours is the best-known example.

Official agencies such as NOAA's Space Weather Prediction Center publish a flood of data and bulletins, written mostly for specialists. This system reads that data, picks out the events that are unusual enough to matter, and rewrites them in everyday words with a short explanation of why they matter. It is meant for curious people, aurora watchers, amateur radio operators, and anyone who wants an early heads-up without watching charts.

Two design choices shape everything that follows:

- **It stays quiet on purpose.** Thresholds are set high, so most days there is nothing in the feed. An empty feed is good news.
- **Every threshold can be changed.** The person running the system can raise or lower any limit in one settings file (`config.toml`). The Appendix lists the current values.

## How it works, and how to read an alert

A small program checks public data every few minutes, decides whether anything has crossed a threshold, and writes new messages to the feed. You subscribe once in a news reader and new alerts appear like headlines.

**Where the data comes from.** NOAA's Space Weather Prediction Center (solar wind, Kp, forecasts, proton counts, sunspots), the Heliophysics Event Knowledgebase (flares and filament eruptions), and ground magnetic observatories run by the US Geological Survey and the INTERMAGNET network (power-grid risk).

**Three levels.** Some alerts carry a level at the start of the title:

- **WATCH**: something worth keeping an eye on.
- **WARNING**: a significant event is likely or under way.
- **ALERT**: an extreme event.

Alerts without a level (for example a solar wind reading or an observed Kp) are simply announced when their limit is crossed.

**Times are in UTC.** UTC is the world time standard. Eastern Standard Time is UTC minus 5 hours, and Eastern Daylight Time is UTC minus 4. A time of 12:11 UTC is 7:11 in the morning in Toronto in winter.

**Alerts are not repeated.** Once an event has been announced, the system stays quiet about it until it has ended. If something gets worse while it is under way (a sunspot becomes more dangerous, or a warning becomes an alert), you get one update message.

**Lead time varies.** An eruption on the Sun takes one to three days to reach Earth. The solar wind readings used here come from a spacecraft about 1.5 million km sunward of Earth and give roughly 30 to 60 minutes of notice.

**Every message has the same parts.** A title with the headline, a category label, a time, and a longer description that explains what happened and why it matters. The example messages below show the title and description as you would see them.

## Solar flares

**What it is.** A solar flare is a sudden flash of radiation from the Sun's surface, usually above a sunspot. Flares are graded by their X-ray brightness: A, B, C, M and X, each ten times stronger than the one before. Within a class a number gives the strength, so X2.0 is twice as strong as X1.0, and X10 is possible.

**Why it matters.** The flash itself reaches Earth in about eight minutes. It ionizes the upper atmosphere on the sunlit side, which can black out high-frequency radio (used by aviation, ships and amateur radio). M-class flares can cause brief radio blackouts near the poles; X-class flares can affect the whole sunlit side of Earth. Big flares are also often, though not always, followed by a coronal mass ejection, a huge cloud of gas that can cause a geomagnetic storm a day or two later.

**Two kinds of flare.** A short, sharp flare is called *impulsive*. One that lasts an hour or more is called *long-duration*, and these are more often linked to CMEs. Because a long-duration flare can be more consequential at a lower brightness, the system uses a lower threshold for it. It also waits: a flare is treated as impulsive until it has ended or lasted an hour, and only then judged by the long-duration limits, so you may get a second message for the same flare.

**Only flares that face Earth count.** A flare near the Sun's centre points its CME toward us. Flares within 60 degrees of the centre of the Sun's disc are "Earth-facing"; those between 60 and 90 degrees are "partially Earth-facing". Flares beyond 90 degrees, on the far side of the Sun, are ignored.

**When you get an alert.**

- Short flare: WARNING at X3.0, ALERT at X5.0.
- Long-duration flare: WARNING at X1.5, ALERT at X3.0.

**Example message**

```text
Title: WARNING: X2.1 long-duration solar flare from AR4551 (S12W10, Earth-facing)
Category: Solar flare

An X2.1 solar flare from AR4551 (a burst of radiation from the Sun) peaked at 2026-10-13 11:30 UTC.
Where on the Sun: S12W10, Earth-facing. X is the strongest class of solar flare; flares this size can
black out high-frequency radio across the whole sunlit side of Earth. It lasted 90 minutes, so it counts
as a long-duration flare, a type more often followed by a coronal mass ejection (a huge cloud of solar
plasma). Start 10:50, peak 11:30, end 12:20 UTC. Level: WARNING. Reported by: SWPC.
```

## Filament eruptions

**What it is.** A solar filament is a long strand of cooler, denser gas held above the Sun's surface by magnetic fields. When the magnetic hold gives way, the strand lifts off in an eruption. Seen against the Sun's face it looks like a dark thread; at the Sun's edge it looks like a glowing arch.

**Why it matters.** A filament eruption is one of the most common ways the Sun launches a coronal mass ejection. Unlike a flare, which is a flash of light, an eruption can send a physical cloud of gas and magnetic field toward Earth. It can happen with no flare at all, so a flare-only watch would miss it.

**What the alert does and does not tell you.** The alert says an eruption started on the Earth-facing side of the Sun. It cannot say whether a cloud was actually launched, or whether it is headed our way; that is confirmed from satellite images over the following hours. No WATCH, WARNING or ALERT level is attached.

**When you get an alert.** Any filament eruption reported within 60 degrees of the centre of the Sun's disc (Earth-facing) or between 60 and 90 degrees (partially Earth-facing), in the last 24 hours. Eruptions with no reported position are ignored.

**Example message**

```text
Title: Solar filament eruption (N10W15, Earth-facing); a coronal mass ejection may follow
Category: Filament eruption

A solar filament (a long strand of cooler gas held above the Sun's surface) began erupting at
2026-10-13 01:50 UTC. Where on the Sun: N10W15, Earth-facing. Eruptions like this can throw out a
coronal mass ejection (a huge cloud of solar plasma). Whether one was launched, and whether it is aimed
at Earth, is confirmed from satellite images over the next hours. No alert level applies yet.
Reported by: SWPC.
```

## Forecasts: coronal hole streams and predicted storms

Two alert types look ahead instead of reporting something that has already happened. Both are about geomagnetic storms, which NOAA grades on a five-step G scale: G1 Minor, G2 Moderate, G3 Strong, G4 Severe, G5 Extreme.

### Coronal hole streams

**What it is.** A coronal hole is a dark patch in the Sun's outer atmosphere where the magnetic field opens out into space. Fast solar wind pours through it. When a hole faces Earth, a stream of fast wind washes over us for several days. Holes last for months and come around again every 27 days as the Sun rotates.

**Why it matters.** These streams usually cause modest storms, but strong ones can reach G3 or more. They build slowly and are predictable, so they make good planning information for aurora watchers and satellite operators.

**How it works.** The system reads NOAA's twice-daily forecast discussion and looks for wording that announces the arrival of a stream, ignoring sentences that say a stream is fading or continuing. It alerts only when the forecast reaches G3 (Strong) or higher. This level is changeable.

**Example message**

```text
Title: Fast solar wind stream from a coronal hole forecast to reach Earth: Strong (G3) geomagnetic storm
Category: Geomagnetic forecast

NOAA forecasts a stream of fast solar wind from a coronal hole (a dark opening in the Sun's outer
atmosphere) to reach Earth, and says it could drive geomagnetic storms up to Strong (G3). Aurora may be
seen at about 50 degrees geomagnetic latitude; satellites, GPS and radio may be affected and power systems
may need voltage corrections. These storms are usually slower to build than those from eruptions.
NOAA's wording (forecast discussion issued 2026-10-13 00:30 UTC): A recurrent coronal hole high speed
stream (CH HSS) is expected to influence Earth on 14-15 Oct, reaching G3 (Strong) storm levels.
```

### Predicted severe storms

**What it is.** When NOAA expects a major storm, usually because a CME has been seen heading for Earth, it issues a formal WATCH or WARNING bulletin. This system passes along those bulletins when they predict G4 (Severe) or G5 (Extreme).

**Why it matters.** This is the earliest and most reliable notice that something big is coming, often one to three days ahead of time. It gives time to prepare, whether that means setting up aurora photography or protecting sensitive equipment.

**Example message**

```text
Title: Severe (G4) geomagnetic storm forecast
Category: Geomagnetic forecast

NOAA's Space Weather Prediction Center expects a Severe (G4) geomagnetic storm (a disturbance of Earth's
magnetic field caused by the Sun). Aurora may be seen as far south as about 45 degrees geomagnetic
latitude; power grids, satellite navigation (GPS) and radio can be disrupted. NOAA's message, issued
2026-10-13 11:30 UTC: WATCH: Geomagnetic Storm Category G4 Predicted. Highest Storm Level Predicted by
Day: Oct 14: G4 (Severe) NOAA Scale: G4 - Severe
```

## Storms under way: the Kp index

**What it is.** Kp is the standard 0 to 9 scale for how disturbed Earth's magnetic field is, worked out every three hours from magnetometers around the world. Kp 5 is a minor storm (G1), Kp 6 is G2, Kp 7 is G3 (Strong), Kp 8 is G4 (Severe) and Kp 9 is G5 (Extreme).

**Why it matters.** Unlike the forecasts, this alert says a storm is actually happening. At Kp 7 and above, aurora is visible far south of its usual range, GPS and radio can degrade, satellites can experience extra drag, and power grid operators take precautions. It is the confirmation that a forecast came true, and the most useful alert for anyone deciding whether to go out and look at the sky tonight.

**When you get an alert.** Whenever Kp reaches 7 or higher. The limit is changeable; many people lower it to 5 or 6 for aurora watching.

**Example message**

```text
Title: Geomagnetic storm under way: Kp 8.33, Severe (G4) level
Category: Geomagnetic storm

Kp, the standard 0-9 scale of how disturbed Earth's magnetic field is, reached 8.33 for the 3-hour
period starting 2026-10-13 11:00 UTC. That is a Severe (G4) geomagnetic storm. Aurora may be seen as far
south as about 45 degrees geomagnetic latitude; power grids, satellite navigation (GPS) and radio can be
disrupted. (Alert threshold: Kp 7.)
```

## Magnetic coupling during a strong storm

**What it is.** The solar wind carries its own magnetic field. Seen from the Sun, that field points in some direction across the Earth-Sun line, and the angle is called the *clock angle*: 0 degrees is north, 90 degrees is east or west, and 180 degrees is due south. Earth's own field points north at the point where it meets the solar wind. When the solar wind's field points south, the two fields link up, like opposite poles of two magnets, and energy flows in.

**Why it matters.** This is the main switch that turns a disturbed Earth into an intensely disturbed one. A strong storm with a field that turns and stays southward can get worse, with brighter and lower aurora and stronger effects on power grids, GPS and radio. This alert flags the moment that link-up is in place while a strong storm is already under way.

**When you get an alert.** Both of these must be true at the same time: Kp is 7 or higher, and the clock angle is between 150 and 210 degrees (pointing within 30 degrees of due south) for at least five minutes. Either condition may come first. The alert is a WARNING and ends when either condition stops. Readings are smoothed over five minutes so a brief flicker does not trigger it.

**Example message**

```text
Title: WARNING: Earth is coupling with the solar wind during a strong storm (Kp 8, magnetic field pointing close to south)
Category: Geomagnetic storm

Geomagnetic activity is at Kp 8, a Severe (G4) storm, and the magnetic field carried by the solar wind
has turned to point close to due south: its clock angle entered the 150 to 210 degree range at
2026-10-13 11:40 UTC and is now 184 degrees. (The clock angle is the direction of that field across the
Earth-Sun line: 0 degrees is north, 180 degrees is south.) A southward field links up with Earth's own
magnetic field, letting solar wind energy in, which can intensify storm conditions. Level: WARNING.
Effects reach Earth in roughly 30-60 minutes after the solar wind spacecraft sees them; watch for
stronger aurora and for power grid, GPS and radio disturbances.
```

## Solar radiation storms

**What it is.** Big flares and CMEs can accelerate protons to a large fraction of the speed of light. A surge of these energetic protons near Earth is called a solar radiation storm. NOAA's GOES satellites count them in particle flux units (pfu), meaning particles passing through a square centimetre each second. Two energy bands are watched: 10 MeV and above, and the much rarer and more penetrating 100 MeV and above.

**Why it matters.** The protons arrive within minutes to hours, far faster than a CME. They can damage satellite electronics and solar panels, are a radiation concern for astronauts and for passengers and crew on polar flights, and can black out high-frequency radio over the polar caps. NOAA grades these events on an S scale: 10 pfu (10 MeV and above) is S1 Minor, 100 is S2, 1,000 is S3, 10,000 is S4 and 100,000 is S5.

**When you get an alert.**

- Proton flux at 10 MeV and above rises above 200 pfu (a moderate-to-strong event, between S2 and S3).
- Proton flux at 100 MeV and above rises above 2 pfu.

After an alert, the system stays quiet for six hours for the same band unless the flux has dropped back below the limit and risen again.

**Example message**

```text
Title: Solar radiation storm: high-energy protons (>=10 MeV): 350 pfu
Category: Solar radiation storm

Proton flux >=10 MeV reached 350 pfu at 2026-10-13 11:55 UTC (it first went above the alert level of
200 pfu at 11:55 UTC). Latest reading: 350 pfu. pfu means particles per square centimetre per second per
steradian, the unit NOAA uses to count solar protons passing the GOES satellites. Raised levels can harm
satellite electronics and are a radiation concern for astronauts and for polar flights, and can disturb
high-frequency radio at the poles. On NOAA's S scale, 10 pfu is S1 (Minor), 100 is S2, 1,000 is S3,
10,000 is S4 and 100,000 is S5.
```

## Extreme solar wind readings

**What it is.** A NOAA spacecraft parked about 1.5 million km sunward of Earth measures the solar wind continuously. Four readings are watched for extreme values:

- **Speed.** Normally 300 to 500 km/s. Above 1,000 km/s is exceptional and usually means a very fast CME.
- **Density.** Normally under about 10 particles per cubic centimetre. Above 20 means a dense wind that presses harder on Earth's magnetic field.
- **Field strength (Bt).** The total strength of the magnetic field carried by the wind, normally about 5 nanotesla (nT). Above 50 nT is extreme.
- **Bz.** The north-south part of that field. When it points strongly south (negative), it links up with Earth's field and is the single most important driver of geomagnetic storms. An alert fires at less than minus 50 nT.

**Why it matters.** These readings arrive roughly 30 to 60 minutes before the wind reaches Earth, which makes them the shortest-notice warning available. Several extreme readings together are the signature of the most severe storms.

**Protection against bad data.** The spacecraft occasionally reports single-minute spikes that are instrument glitches. Readings are therefore smoothed (the median of five minutes), and values beyond a plausibility limit are thrown away. A quick spike never triggers an alert; a sustained excursion does.

**When you get an alert.**

- Speed above 1,000 km/s.
- Density above 20 particles/cm³.
- Total field above 50 nT.
- Bz below minus 50 nT.

Each rule alerts once per excursion and then waits at least an hour before it can fire again.

**Example message**

```text
Title: Solar wind magnetic field pointing strongly south (Bz): -55 nT
Category: Solar wind

IMF Bz (GSM) reached -55 nT at 2026-10-13 11:55 UTC (it first went below the alert level of -50 nT at
11:55 UTC). Latest reading: -55 nT. Bz is the north-south part of the magnetic field carried by the solar
wind. When it points south (negative) it links up with Earth's magnetic field and lets solar wind energy
in, which is the main driver of geomagnetic storms; the more negative, the stronger the effect. These
readings come from a NOAA spacecraft about 1.5 million km from Earth, so effects reach Earth in roughly
30-60 minutes.
```

## Shock waves in the solar wind

**What it is.** A fast CME moving through the slower solar wind piles up the gas in front of it, the way a boat pushes up a bow wave. When that wave passes the spacecraft, the solar wind suddenly gets faster, denser, hotter and more magnetic all at once. That sudden step is an interplanetary shock.

**Why it matters.** When the shock reaches Earth, a geomagnetic storm often begins within minutes to an hour. Earth's magnetic field receives a sudden jolt (a "sudden impulse"), which can show up as a sharp kick on magnetometers and, in rare strong cases, can set off a burst of auroral activity at once. The system also estimates when the shock will arrive at Earth by dividing the 1.5 million km distance by the new wind speed, accurate to roughly 15 minutes. A shock does not by itself say how strong the storm will be; that depends on the direction of the magnetic field behind it, which the Bz and coupling alerts cover.

**How a real shock is told from noise.** The speed must jump and stay up. In addition, the plasma must compress (density rises), and at least two of density, magnetic field and temperature must also jump. A speed step with no compression is not a shock and is ignored. The alert is issued about 10 minutes after the step, which is the time needed to confirm that it is lasting.

**How the thresholds were set.** They were tuned using 2024 and 2025 spacecraft records. The May 2024 superstorm shock sets the ALERT limit and the January 2025 storm shock sets the WARNING limit. The March 1989 storm could not be used because there was no solar wind monitor in space at that time.

**When you get an alert.**

- WARNING: speed up by at least 45 km/s, plus at least two of: density 1.8 times higher, field 1.3 times stronger, temperature 1.25 times higher.
- ALERT: speed up by at least 120 km/s, plus at least two of: density 1.5 times higher, field 1.5 times stronger, temperature 2 times higher.

**Example message**

```text
Title: ALERT: Shock wave in the solar wind heading for Earth, due about 12:11 UTC (wind speed jumped from 450 to 600 km/s)
Category: Solar wind shock

A shock wave (a sudden jump in the speed of the solar wind, usually driven by a coronal mass ejection,
a cloud thrown out by the Sun) passed the NOAA spacecraft about 1.5 million km sunward of Earth at about
2026-10-13 11:30 UTC. Solar wind speed rose from 450 to 600 km/s (+150) and stayed up; at the same time
its density became 2.3 times higher, magnetic field became 3.0 times stronger and its temperature became
4.0 times higher. Level: ALERT. It should reach Earth in roughly 42 minutes, at about 12:11 UTC (give or
take 15 minutes). When it arrives, Earth's magnetic field will be jolted suddenly (a 'sudden impulse').
Whether a geomagnetic storm follows depends on whether the magnetic field carried behind the shock
points south; watch for follow-up storm alerts.
```

## Potentially dangerous sunspots

**What it is.** Sunspots are dark patches where strong magnetic fields break through the Sun's surface. NOAA numbers each group ("AR4551", where AR means active region), and describes it by its magnetic layout, its size and its recent behaviour. Nearly all flares and eruptions come from sunspot groups, and the biggest come from the most tangled ones.

**Why it matters.** This alert looks ahead of the event. It tells you a sunspot group that now faces Earth looks capable of producing a big flare or eruption, before anything has happened. A group facing Earth is the one whose CMEs are aimed at us, so the watch is only applied to groups within 60 degrees of the centre of the Sun's disc.

**What makes a sunspot flagged.** Any one of these four conditions is enough. Each has its own level:

- **Complex magnetic layout** (the "beta-gamma-delta" class, the most tangled type): WATCH.
- **Rapid growth**: the group's area rising by 100 millionths of the Sun's visible half or more, or 10 or more new sunspots, per day, and only for large, complex group shapes (McIntosh classes Fkc, Ekc, Eki or Fki): WATCH.
- **High X-flare odds**: NOAA gives it a 20% or better chance of an X-class flare in the next 24 hours: WARNING.
- **Already flaring**: it has already produced at least one M or X class flare: WATCH.

The alert takes the highest level among the conditions met. If a flagged group later meets a new condition, you get one update message, and the level may go up. After an alert, the same region is not announced again for 24 hours.

**Data lag.** NOAA publishes the sunspot summary once a day, so the data can be up to a day old.

**Example message**

```text
Title: WARNING: Potentially dangerous sunspot AR4551 faces Earth, S15E07 (complex magnetic layout, beta-gamma-delta; rapid growth; 25% chance of an X-class flare)
Category: Sunspot

Level: WARNING. Sunspot region AR4551 (AR means active region; this is NOAA's number for it) is facing
Earth, at S15E07 on the Sun, and has: a complex magnetic layout, class BGD (beta-gamma-delta), the kind
most likely to produce major flares; rapid growth (14 more sunspots in 1 day; it is a large, complex
type of sunspot group, class Eki); NOAA gives it a 25% chance of producing an X-class (the strongest)
flare in the next 24 hours. Shape class Eki; covers 500 millionths of the Sun's visible half; 23 spots.
Based on NOAA's region summary for 2026-10-14 UTC (updated daily, so up to a day old). Sunspot regions
are where solar flares and eruptions come from; because this one faces Earth, anything it throws out
could be aimed our way.
```

## Power-grid risk (geomagnetically induced currents)

**What it is.** When Earth's magnetic field changes quickly, it induces electric currents in the ground, and those currents can flow up into long conductors such as power lines, pipelines and railway circuits. They are called geomagnetically induced currents, or GIC. Power transformers are designed for alternating current, and a steady extra current can push them into saturation, which makes them heat up, hum and create electrical noise that can trip protective equipment.

**Why it matters.** This is the way space weather causes blackouts. The March 1989 storm collapsed Hydro-Quebec's grid in about 90 seconds, leaving millions without power for about nine hours. A paper reviewing that event (Boteler, 2019, *Space Weather*) notes the blackout came early in the storm, not at its peak, and was triggered by a sudden, sharp change in the field. Whether a given grid is hurt depends on the grid design and the rock beneath it, so this alert is a risk signal, not a prediction of an outage.

**Two ways the system watches.**

- **How fast the field is changing, around the world.** Ground magnetic observatories report the field every minute. The alert measures how many nanotesla the field changes per minute (nT/min), in five regions: North America, Europe, Asia, Australasia and South America. Quiet days are usually a few nT/min or less. WARNING at 400 nT/min, ALERT at 800 nT/min. Each region is judged separately, so an alert names where the change was measured. To avoid false alarms from a faulty instrument, a second observatory in the same region must show a disturbance of at least 100 nT/min at about the same time, and the alert is only issued when geomagnetic activity has recently reached Kp 4 or higher.
- **The electric field in the ground, for the United States and Canada.** NOAA publishes a model of the ground electric field each minute, in millivolts per kilometre (mV/km). WARNING at 1,000 mV/km, ALERT at 1,500 mV/km. At least three grid points must reach the value, so one odd point cannot trigger it. The value must also hold for three minutes in a row, and the alert is only issued when geomagnetic activity has recently reached Kp 4 or higher, which keeps brief model glitches in quiet conditions from raising it.

When a WARNING is later raised to an ALERT during the same episode, you get one update message. After an alert, the same region is not announced again for an hour.

**Example message (North America)**

```text
Title: ALERT: Very rapid magnetic field change in North America: 900 nT/min measured at Boulder, Colorado, USA (BOU); risk of currents in power grids
Category: Geomagnetically induced currents

Very rapid magnetic field change in North America: 900 nT/min measured at Boulder, Colorado, USA (BOU);
risk of currents in power grids. Level: ALERT (the ALERT threshold is 800 nT/min). The peak was 900
nT/min at 2026-10-13 11:54 UTC; it began at 11:54 UTC. This is how fast the horizontal magnetic field
changed, in nanotesla per minute (nT/min), measured by ground observatories in North America; on quiet
days it is usually only a few nT/min or less. Why it matters: fast changes in Earth's magnetic field
drive electric currents through the ground and into long conductors such as power lines, pipelines and
railway circuits (geomagnetically induced currents). Strong ones can trip protection equipment, cause
voltage problems or overheat transformers; the Quebec blackout of March 1989 was caused this way.
Whether a particular grid is affected depends on the grid and the local rock beneath it.
```

**Example message (Europe)**

```text
Title: WARNING: Very rapid magnetic field change in Europe: 500 nT/min measured at Niemegk, Germany (NGK); risk of currents in power grids
Category: Geomagnetically induced currents

Very rapid magnetic field change in Europe: 500 nT/min measured at Niemegk, Germany (NGK); risk of
currents in power grids. Level: WARNING (the WARNING threshold is 400 nT/min). The peak was 500 nT/min
at 2026-10-13 11:54 UTC; it began at 11:54 UTC. (The same explanation of why it matters follows.)
```

## What to keep in mind

- **This is an information service, not an official warning.** It is a community tool built on NOAA and other public data. For decisions that matter, such as operating a power system, a satellite or an aircraft, use the official products of NOAA's Space Weather Prediction Center or your national agency.
- **An alert is a signal, not a guarantee.** Space weather is hard to forecast. A flagged sunspot may produce nothing, and a CME that is aimed at Earth may miss or fizzle.
- **Some alerts lag.** Sunspot data is up to a day old. Shock alerts arrive about 10 minutes after the shock passes the spacecraft. Real-time data is occasionally noisy, and the system filters it, which can delay an alert slightly.
- **Thresholds are judgement calls.** They were set to catch the major events and stay quiet otherwise. The shock limits were tuned to storms in 2024 and 2025, and the power-grid limits were chosen by the system's operator. After a few weeks of real use, some may need adjusting.
- **A quiet feed is normal.** Most weeks have no alerts at all.

## Appendix: alert types and their trigger thresholds

These are the values in use as of 1 November 2026. All can be changed in the system's settings file (`config.toml`). "Cooldown" is the minimum wait before the same alert type can fire again after one has ended.

| Alert type | Trigger | Level | Cooldown | Notes |
| --- | --- | --- | --- | --- |
| Short (impulsive) solar flare | Flare of class X3.0 or higher; X5.0 or higher | WARNING; ALERT | None (one alert per flare) | Earth-facing (within 60° of disc centre) or partially (60–90°) only. Flare is treated as short until it ends or lasts 60 minutes. Looks back 12 hours. |
| Long-duration solar flare (60 min or more) | Flare of class X1.5 or higher; X3.0 or higher | WARNING; ALERT | None | Same location rules. A second message follows if a flare first judged short turns out to be long, or the level changes. |
| Filament eruption | Any eruption on the Earth-facing side (within 90° of disc centre) | None | None | Eruptions with no reported position are ignored. Looks back 24 hours. |
| Coronal hole stream forecast | NOAA forecast discussion announces a stream arrival with storm level G3 or higher | None | 12 hours | Sentences saying a stream is fading or continuing are ignored. |
| Predicted severe storm | NOAA WATCH or WARNING bulletin for G4 or higher (K-index of 8 or more) | None | 6 hours | Bulletins older than 36 hours are ignored. |
| Observed geomagnetic storm | Kp of 7 or higher | None | One alert per 3-hour period | Kp 7 is G3 (Strong). Looks back 12 hours. |
| Magnetic coupling | Kp of 7 or higher and IMF clock angle between 150° and 210° for at least 5 minutes | WARNING | 3 hours | Either condition may come first. Field components are smoothed over 5 minutes. |
| Solar radiation storm, 10 MeV and above | Proton flux above 200 pfu | None | 6 hours | Between S2 (100 pfu) and S3 (1,000 pfu) on NOAA's scale. |
| Solar radiation storm, 100 MeV and above | Proton flux above 2 pfu | None | 6 hours | The rarer, more penetrating protons. |
| Solar wind speed | Above 1,000 km/s | None | 1 hour | Normal is 300–500 km/s. Glitch spikes are filtered out. |
| Solar wind density | Above 20 particles/cm³ | None | 1 hour | Normal is under about 10. Readings above 100 are discarded as errors. |
| Solar wind total magnetic field (Bt) | Above 50 nT | None | 1 hour | Normal is about 5 nT. Readings above 200 are discarded as errors. |
| Solar wind Bz (north-south field) | Below −50 nT | None | 1 hour | Negative means southward, the storm-driving direction. Readings beyond 200 nT either way are discarded as errors. |
| Solar wind shock, WARNING | Speed jump of at least 45 km/s, plus two of: density ×1.8, field ×1.3, temperature ×1.25 | WARNING | None | The jump must last and density must rise by at least 1.2×. Issued about 10 minutes after the shock passes. Includes an Earth-arrival time estimate. |
| Solar wind shock, ALERT | Speed jump of at least 120 km/s, plus two of: density ×1.5, field ×1.5, temperature ×2 | ALERT | None | Same confirmation rules. In the 2024–2025 calibration data this level was reached 3 times in 2024 and 2 in 2025. |
| Dangerous sunspot: complex magnetic layout | Region of class BGD, within 60° of disc centre | WATCH | 24 hours | Uses NOAA's daily summary, up to a day old. |
| Dangerous sunspot: rapid growth | Area up 100 millionths or more, or 10 or more new spots, per day, for classes Fkc, Ekc, Eki or Fki | WATCH | 24 hours | Compared over a window of up to 2 days. |
| Dangerous sunspot: high X-flare odds | NOAA X-class flare probability of 20% or more | WARNING | 24 hours | The highest level among the conditions met is used. Later new conditions trigger an update message. |
| Dangerous sunspot: already flaring | At least 1 M or X class flare already from the region | WATCH | 24 hours | Absence of past flares does not disqualify a region. |
| Power grid: field change rate | 400 nT/min or more; 800 nT/min or more | WARNING; ALERT | 1 hour | Smoothed over 5 minutes; a second station in the region must show 100 nT/min or more within 10 minutes; Kp must have been 4 or higher in the last 6 hours. Per region: North America (BOU, FRD), Europe (NUR, NGK, FUR, HRN, BEL), Asia (KAK, MMB, KNY), Australasia (CNB, CTA, KDU, EYR), South America (VSS). A station that has not reported for over an hour no longer keeps an alert open. |
| Power grid: ground electric field (US and Canada) | 1,000 mV/km or more; 1,500 mV/km or more | WARNING; ALERT | 1 hour | Value must be reached at 3 or more grid points, hold for 3 minutes, and Kp must have been 4 or higher in the last 6 hours. NOAA 1-D model. |
