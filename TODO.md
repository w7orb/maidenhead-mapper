# To do

**Effort**: S = under one working session · M = about one session · L = several sessions.
**Impact**: how much it adds for you and for other hams using the plugin.

| # | Item | Effort | Impact | Needs | Why |
|---|---|---|---|---|---|
| 1 | ~~Import WSJT-X ALL.TXT~~ **done** | S | **High** | — | The exchange tracker and an ALL.TXT reader already exist. Maps all your past logs and unlocks 6 and 7. |
| 2 | ~~Save live sessions automatically~~ **done** | S–M | Medium | — | Protects data; the work is appending to a GeoPackage safely and picking up again after a restart. |
| 3 | ~~Timeline for live layers~~ **done** | M | Medium | — | The live model keeps per-station totals, not each decode, so it needs a decode history and a different rebuild that works with fading. |
| 4 | ~~Hop geometry and takeoff angle~~ **done** | S–M | Medium (High with 5) | — | `hops`/`takeoff_deg` everywhere. *Check paths* adds hops at the measured height, MUF and reflection points. |
| 5 | ~~Ionosonde / model enrichment~~ **done** | M–L | **High** | — | KC2G for current paths; a pure-Python CCIR (ITU-R P.1239) monthly-median model for any date, checked against PyIRI. PyIRI itself was rejected: it needs scipy ≥ 1.15, while QGIS 3.44 has 1.13. |
| 6 | ~~Antenna A/B comparison~~ **done** | S (after 1) | **High** | 1 (4 for angles) | Already prototyped on your data. Practical and unusual: most hams can't measure this today. |
| 7 | ~~Station balance from exchanges~~ **done** | M | Medium | 1 helps | Least-squares fit plus confidence. Needs about 100+ exchanges per station, so value grows with data. |
| 8 | ~~MUF check per path~~ **done** | S (after 5) | Medium–High | 5 | A simple comparison once MUF values exist; good at separating F2 paths from sporadic-E and scatter. |
| 9 | ~~Sporadic-E detector (10m/6m)~~ **done** | M | Medium (High for 6m, in summer) | 4, 5 help | Heuristic clustering; seasonal. |
| 10 | ~~Skip distance and hop structure over time~~ **done** | M | Medium | 3 for live data | Stats plus a chart in the timeline; shows changes, not absolute values. |
| 11 | Band hotter/colder than usual | M | Low–Medium | 2, regular fetches | Needs weeks of collected data before it says anything; modest accuracy (r ≈ 0.3). |
| 12 | ~~Menu: separate sections, ionosphere on its own~~ **done** | S | Medium | — | A flat list of 8 entries with 8 identical toolbar icons; group by purpose so the ionosphere tools aren't mistaken for part of WSJT-X. |
| 13 | Propagation model from triangle data | S (dataset) + M–L (model) | **High** (research value) | 1, 2, 5; weeks of sessions | Separates station effects from path effects; physics features (f/MUF, hops, day/night) plus per-station terms, censoring-aware; must beat distance-only and MUF-only baselines on held-out days. |
| 14 | Antenna pattern from neighbouring receivers | M | **High** | a PSK Reporter grid query (unverified) | Your RX/TX pattern from ordinary operating, using nearby spotters (or common receivers) as the shared-path reference. |
| 15 | Elevation pattern from A/B data: mixture fit over hop counts | M–L (research-grade) | **High** (research value) | 5, 6; days of two-receiver data across day and night | Layer height swings day/night, so one distance is heard at several angles; a fit that treats each decode as a mix of hop counts can separate gain vs elevation from distance. |
| 16 | Reference-antenna mode for A/B | S–M | Medium–High | 6 | Load a NEC-modelled pattern (CSV of gain vs azimuth/elevation) for antenna B; A's results then come out as an absolute pattern in dBi. |
| 17 | ~~Import ADIF logs~~ **done** | S | Medium–High | — | Maps your whole logging history (WSJT-X, LoTW, any logger), merges confirmations, and feeds Check paths / Station balance. |
| 18 | Basic vs operational MUF in Check paths | S | Medium | 5, 8 | Decision pending. P.533's operational MUF is a bit higher than the basic MUF Check paths uses, so fewer long 10m paths would show as "above". Option: add the operational factors, or rename the status "above basic MUF". |
| 19 | Live hop estimates from the ionosphere | M | Medium | 5 | The live map fills `hops_iono`/`takeoff_iono` as each exchange arrives, from the cached KC2G data (refreshed every 15 min), instead of only the fixed-300 km estimate. |
| 20 | ~~Fix: Station balance help text cut off~~ **done** | S | Low | — | The "<" in "< −3 dB" is read as formatting by QGIS, which hides the rest of the line after "good ears (". |
| 21 | ~~Fix: Skip distance distances unrounded~~ **done** | S | Low | — | `median_km`/`p90_km` show many decimals in hours with too few contacts for a skip edge. |
| 22 | ~~Fix: Maidenhead grid on non-Greenwich projections~~ **done** | S | Medium | — | Fields are drawn from their 4 corners only, so with an Americas-centred projection the 60°E edge lands on the wrong side and outer fields stop short of the map edge. Add points along each edge. |
| 23 | ~~Fix: Station balance `std_err_db` too optimistic~~ **done** | S | Medium | — | Stations seen once fit exactly (residual 0), so the scatter estimate drops to its 1 dB floor; real scatter is ~6.5–8 dB. Errors come out ~6× too small (±0.18 dB for 56 exchanges instead of ~±1.1), so "uncertain" rarely triggers. Estimate scatter from well-connected stations or correct for degrees of freedom. |
| 24 | ~~Summarize exchanges by grid pair and by station~~ **done** | M | High | — | Corridors per cell pair and band (`summarize_exchanges`); station facts added to Station balance. Not yet in the Activity timeline (drag to re-summarize). |
| 25 | ~~Propagation over time with the time slider~~ **done** | M | High | — | Index ALL.TXT, pick dates, group by hour/day, time-enabled corridor and cell layers for the Temporal Controller. Possible next: the same from saved live sessions and PSK Reporter pulls; include my own decodes (their area → mine). |

**Done**: 1–10, 12, 17, 20–23. **Open**: 11 (needs weeks of data), 13 (propagation model: dataset export first), 14 (antenna pattern from neighbouring receivers: check the PSK Reporter grid query first), 15 (elevation pattern: needs days of A/B data), 16 (reference-antenna mode), 18 (decision pending: basic vs operational MUF), 19 (live hop estimates).

## Details

1. **Import WSJT-X ALL.TXT**: a Processing tool that runs a folder of `ALL.TXT` files through the same exchange tracker as the live map. Output is an exchanges line layer and a station point layer for past sessions, filterable by band and time.
2. **Save live sessions automatically**: write live stations and exchanges to a GeoPackage as they arrive, so a session survives closing QGIS. Today it only survives if you use *Make Permanent* by hand.
3. **Activity timeline for the live layers**: reports per hour and window selection for *WSJT-X live* and *WSJT-X exchanges*, not only PSK Reporter layers.
4. **Hop geometry and takeoff angle**: for each path (PSK Reporter and exchanges), use distance and an assumed or measured layer height to list the possible hop counts. Rule some out using the MUF and a realistic takeoff-angle range (about 2–35°). Add fields `hops_likely`, `takeoff_deg`, and `reflection_lat`/`reflection_lon` at each hop's midpoint, plus a reflection-point layer. Keep the geometry in pure Python with tests. *Done:* `core/hops.py` (with `reflection_points()`), plus `hops` and `takeoff_deg` on every layer. *Left:* reflection lat/lon fields, the reflection-point layer, and the MUF rule-out.
5. **Ionosonde / model enrichment**: fetch measured layer height (hmF2), critical frequency (foF2) and the propagation factor M(3000)F2 for each path's reflection points and time. Sources, checked 2026-11-11:
   - KC2G `https://prop.kc2g.com/api/stations.json`: foF2, hmF2, MUF(3000) and foEs per ionosonde, with a confidence score.
   - KC2G `https://prop.kc2g.com/renders/current/mufd-normal-now.geojson` and `fof2-normal-now.geojson`: contour maps that load straight into QGIS.
   - PyIRI (MIT licence) as a modelled fallback, for historical data.
   - GIRO's old GET API now returns 404; only the web form works, so historical measured data has no API yet.
   - KC2G states no data licence: ask before redistributing.
   Feeds item 4 so it uses measured heights instead of assumed ones. Optionally add space-weather context from NOAA SWPC (solar flux, Kp, X-ray flares).
6. **Antenna A/B comparison**: match the same message in the same slot from two receivers. Sources: two ALL.TXT files (reuses item 1) or two live WSJT-X instances (the listener already tells clients apart by id). Output: a per-station SNR-difference layer, plus breakdowns by direction, distance and (with item 4) takeoff angle. Count decodes only one antenna got, since the −24 dB decode floor hides weak-antenna losses. Your 2022-12-13 DXCommander/loop recordings give 3,923 matched decodes. They show the vertical +5 dB to the NW, +2 to +5 dB beyond 6,000 km, and −1 dB under 2,000 km.
7. **Station balance from two-way exchanges**: the difference between the two reports cancels path loss and both antennas. That leaves each station's transmit power plus receive noise floor ("loud but deaf" vs "good ears"). Least-squares fit across all exchanges, up to one shared constant; needs about 100+ exchanges per station, with confidence shown. The same offsets give station-corrected path-loss curves by distance, band and hour (research report idea 4).
8. **MUF check per path**: overlay KC2G MUF/foF2 contours. Flag spots and exchanges whose frequency is above the F2 MUF at the reflection point as "not F2" (sporadic-E, scatter, or a model error). High reliability. Needs item 5.
9. **Sporadic-E detector (10m/6m)**: clusters of 500–2,300 km paths that F2 can't explain, mapped at path midpoints, with foEs estimated assuming a ~105 km layer. Medium-high reliability in season.
10. **Skip distance and hop structure over time**: per band and hour in the timeline: skip-zone edge, first-hop edge, and distance histogram. Reliable for showing changes. Show any derived foF2 next to the nearest ionosonde value, not as a measurement.
11. **"Band hotter/colder than usual"**: compare each band's spot activity with its rolling usual level for that region and hour. KC2G found modest correlation with ionosonde MUF changes (r ≈ 0.3, best with FT8). Needs 2–4 weeks of baseline data.
12. **Menu organisation**: replace the flat *Maidenhead Mapper* menu with submenus:
    - **Grid**: Create Maidenhead grid.
    - **PSK Reporter**: Fetch, Import.
    - **WSJT-X**: Import ALL.TXT; WSJT-X live map (panel).
    - **Ionosphere**: *Fetch ionosphere (KC2G)* and *Check paths against the ionosphere (MUF)*, as their own menu items. Later ionosphere features (history for 5, sporadic-E 9) go here too.
    - **Activity timeline** stays at the top level, since it works with everything.

    Design rule: the ionosphere tools stay independent of the WSJT-X live panel. They take any station or exchange layer as input, and the live panel gets no ionosphere options of its own. Also give the toolbar one drop-down button (or distinct icons) instead of 8 identical ones. The Processing Toolbox groups already match: Stations / Ionosphere.
13. **Propagation model from triangle data**: each overheard exchange gives A→B, B→A, A→me and B→me. The reciprocal pair cancels the path; the same transmitter at two receivers cancels the transmitter. Over many exchanges this separates station effects from path loss, which spot data alone can't.
    - Step 1 (S): export a modelling dataset, one row per observed SNR (station ids, band, time, distance, hops, f/MUF, day/night at control points, Es flag, censored, decoded or not). Repeats and stalled exchanges are evidence of non-decodes.
    - Step 2 (M–L): fit SNR = T_tx + R_rx − L(features) with censoring (Tobit) by alternating least squares, pure Python. Later: per-direction station terms for very active stations (other stations' antennas).
    - Step 3: validate on held-out days against distance-only and P.533-MUF baselines before calling it better.
14. **Antenna pattern from neighbouring receivers**: antenna gain cancels in two-way differences, so antennas need a shared path with different antennas.
    - RX pattern: your SNR minus the SNR of receivers within ~200 km that decoded the same transmission in the same minute, binned by direction, distance and estimated takeoff.
    - TX pattern: distant receivers that heard both you and a nearby transmitter in the same minute.
    - Needs PSK Reporter reports for receivers and senders near you. Check whether pskquery supports a grid-square query (callsign=CN85 with modify=grid?) and its rate limits before building.
15. **Elevation pattern from A/B data**: takeoff is never observed, only inferred from distance, layer height and hop count. Three things make it estimable over many decodes: hmF2 swings (~230–350 km day to night) so one distance is heard at several angles; the P.533 MUF rules out some hop counts; and different distances constrain overlapping angle ranges. Model each matched decode's A − B as a weighted mix over plausible hop counts (weights from MUF and hop loss), each hop count's angle meeting a smooth gain-difference curve G(elevation), fitted by EM or least squares, per band. Handle Es (~105 km) and short/NVIS paths separately. Needs ~10,000+ matched decodes per band spread over day and night for ±1 dB in 2–3° bins. Step 1 is done: A/B bins by the ionosphere-informed angle per decode time (v0.8.1).
16. **Reference-antenna mode**: let antenna B be a simple, well-modelled antenna (e.g. a dipole at known height) with a NEC pattern (4nec2/EZNEC export as CSV: azimuth, elevation, gain dBi). A's pattern = B's modelled gain + the measured A − B, giving absolute dBi including ground effects. Works with item 6 now and item 15 later.
