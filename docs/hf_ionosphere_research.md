# Estimating ionospheric conditions from HF amateur-radio data: research notes

*Compiled 2026-11-11. Tags: **[verified]** means I read the primary source or tested the endpoint live today. **[secondary]** means it comes from search snippets or abstracts only. **[unverified]** means I could not confirm it.*

---

## TL;DR

- **No published method reliably inverts amateur spot data (distance + SNR, with no elevation angle or delay) into absolute foF2 or hmF2.** The peer-reviewed work (Frissell/HamSCI, Deacon et al.) uses spots *qualitatively*: blackouts, TID oscillations in skip distance, Es patch locations. It then validates against ionosondes, SuperDARN or GNSS TEC.
- **The most rigorous quantitative test I found is in KC2G's own planning log for its next-generation model.** KC2G is the project behind prop.kc2g.com. The log reports:
  - The naive proxy "highest band with spots on 1000–3000 km paths ≈ MUF" was **useless** (R² ≈ 0.01–0.04).
  - Per-band **activity anomalies** (spot count compared with that cell's usual count for the band and hour) correlate 0.27–0.38 with the ionosonde MUF(3000) anomaly on 18–28 MHz. FT8 data is stronger than WSPR.
  - One receiver's own spots gave about a 15% foF2 nowcast improvement over an indices-only model, within roughly 1500 km.

  [verified, [PLAN.md](https://raw.githubusercontent.com/arodland/prop/master/PLAN.md)]
- **Recommended approach:** pull real ionosonde values (KC2G JSON API, GIRO) as ground truth. Use our spots for **relative, geometric, and anomaly** products: skip-distance edges, Es detection, hop classification and station calibration. Do not present our own foF2 as if it were measured.

---

## 1. Established methods and projects

### 1.1 KC2G (prop.kc2g.com): MUF and foF2 maps

**Inputs.** The production maps come from **ionosonde measurements only**, distributed through GIRO and INGV. NOAA ionosonde distribution was shut down in 2024. The maps also use the IRI-2020 model as a background. The About page does not mention spots as an input. [verified, [About](https://prop.kc2g.com/about/)]

**Algorithm.**
- Each station gets a confidence score, and values are extrapolated over a short window to the map time.
- The production assimilation is a Gaussian process. The `george` library is listed, and PLAN.md refers to the "production GP".
- A new map is built every 15 minutes, from data that is 5–20 minutes old.
- An effective sunspot number (eSSN) is fitted to drive IRI.

**Code.** [github.com/arodland/prop](https://github.com/arodland/prop). Its directories include `assimilate`, `iri2020`, `iturhfprop`, `raytrace`, `essn` and `api`.
- **No LICENSE file was found** (raw `LICENSE` returns 404). Treat the code as all-rights-reserved and ask before reusing it.

**Spot research in PLAN.md** (working notes, not peer-reviewed). This is the most relevant prior art for us. [verified]
- WSPR and PSK Reporter spots were aggregated **hourly per 5° path-midpoint cell per band**.
  - Used only 1000–3000 km paths, to keep to a single F2 hop.
  - Paths over 3000 km were attributed to "**control points** 1500 km from each end", following ITU-R P.533.
- "Highest band with ≥3 spots" as an MUF proxy gave anomaly correlation 0.21–0.26 and R² of only 0.04. It is censored by band occupancy.
- Activity anomaly, defined as log(count) minus the median for that cell, band, UT hour and month:
  - WSPR: r = 0.23–0.30 on 14–28 MHz.
  - FT8: r = 0.27–0.38 on 18–28 MHz.
  - SNR anomalies: r = 0.21–0.25 on 14–21 MHz.
  - All bands combined: leave-one-month-out R² of 0.18 for FT8 and 0.09 for WSPR.
- Ranking of value as a model input: ionosondes ≫ GloTEC > FT8 activity > WSPR ≈ geomagnetic indices.
- Adding spots to the full neural model gave **no measurable gain where ionosondes exist**. They help only when the ionosonde feed is missing.
- Single-receiver test using the receiver's own baseline:
  - foF2 RMSE of about 1.15 MHz, compared with 1.38 MHz with no inputs, inside 1500 km.
  - No gain beyond 3000 km.

**Takeaway for us:** use **anomaly-versus-own-climatology** features rather than absolute "highest band heard" features.

### 1.2 HamSCI / Frissell et al.: spot networks as qualitative ionospheric sounders

| Paper | Inputs | Method | Result / limitations |
|---|---|---|---|
| Frissell et al. 2014, *Space Weather*, [doi:10.1002/2014SW001132](https://doi.org/10.1002/2014SW001132) ([PDF](https://hamsci.org/sites/default/files/publications/2014_SpaceWeather_Frissell_RBN.pdf)) | RBN spots, GOES X-ray | Path maps in 15-minute integrations | X2.9 flare: paths dropped to under 35% of the pre-flare count. The authors list biases: uneven station geography, operator behaviour (operators move to open bands), unknown antenna gain, and wrong locators. [verified] |
| Katz et al., CEDAR 2017 poster ([PDF](https://hamsci.org/sites/default/files/publications/2017_CEDAR_Katz_poster.pdf)) | RBN 7 MHz, paths under 4000 km (multi-hop removed) | Distance–SNR–time distributions compared with PHaRLAP ray tracing through IRI | "RBN observations reveal large-scale propagation signatures inconsistent with HF raytracing through the IRI". IRI ray tracing misses the large SNR variability. [verified] |
| Frissell et al. 2018, *GRL*, [doi:10.1029/2018GL077324](https://doi.org/10.1029/2018GL077324) | 2017 eclipse QSO party: RBN, WSPRNet and PSK Reporter, about 2.5M spots | PHaRLAP ray tracing through eclipsed SAMI3 to explain the observed spot distances | Model suggests 1.8–7 MHz refracted at h ≥ 125 km with elevation ≥ 22°, and 14 MHz at h < 125 km with elevation < 10°. **Layer heights came from the model, not from inverting spots.** [secondary] |
| Frissell et al. 2019, *Space Weather*, [doi:10.1029/2018SW002008](https://doi.org/10.1029/2018SW002008) | RBN and WSPRNet | **2D histograms of TX–RX distance vs time** (250 km × 10 min bins) per band, with a midpoint density map | Flare blackouts appear as gaps in the histogram. [verified via the review below] |
| Frissell et al. 2022, *GRL*, [doi:10.1029/2022GL097879](https://doi.org/10.1029/2022GL097879) (open access, CC BY-NC-ND) | RBN, WSPRNet and PSK Reporter on 14 MHz; SuperDARN; GNSS dTEC | Time series of TX–RX distance in 2 min × 25 km bins. The **skip-distance oscillation** is tracked and bandpass filtered (2–4 h). | LSTID: period about 2.5 h, wavelength about 1680 km, speed about 1200 km/h. Agreement with SuperDARN and TEC is described as "excellent". [verified abstract and figure caption] |
| Frissell et al., Heliophysics Decadal white paper ([PDF](https://par.nsf.gov/servlets/purl/10506688)) | Review | Covers Grape Doppler, TangerineSDR, and chirp-sounder-of-opportunity ([chirpsounder2](https://github.com/jvierine/chirpsounder2)) | Notes that **time-of-flight or Doppler measurements** would be needed to identify the propagation mode. FT8 and WSPR currently lack them. [verified] |

**Grape / Personal Space Weather Station (PSWS)** ([HardwareX 2022](https://doi.org/10.1016/J.OHX.2022.E00289); [ESSD 2023](https://essd.copernicus.org/articles/15/1403/2023/essd-15-1403-2023.html)):
- Measures Doppler shift of WWV/CHU carriers with a GPS-disciplined receiver.
- Collins et al. 2022 ([EGUsphere](https://doi.org/10.5194/egusphere-2022-327)) estimate layer height from Doppler plus time of flight.
- **We cannot use this.** Our data has no phase or timing information.

### 1.3 Sporadic-E from spots (Deacon, Mitchell and Watson 2022)

*Consolidated Amateur Radio Signal Reports as Indicators of Intense Sporadic E Layers*, *Atmosphere* 13(6):906, [doi:10.3390/atmos13060906](https://doi.org/10.3390/atmos13060906), CC BY.

- **Inputs:** RBN, PSK Reporter, WSPRNet and DX-cluster reports.
- **Method:**
  - Filter to paths ≤ 2500 km, so each path is a single Es hop.
  - Map the reflection point at the **TX–RX midpoint**.
  - Validate against ionosondes.
- The method assumes Es clouds of limited extent that persist for minutes to hours.

[secondary: abstract and search snippets. The MDPI full text returned 403, so I could not confirm their exact foEs estimation or height assumption.]

**Standard physics** (textbook-level, not paper-specific):
- foEs ≈ f_obs / sec(φ), assuming an Es height of about 100–110 km.
- The commonly quoted Es MUF is about 5.3 × foEs for a maximum-length single hop ([hamwaves](https://hamwaves.com/propagation/en/index.html), citing Pocock, QST). [secondary]

**Existing Es services:**
- [DXMaps](https://dxmaps.com/spots/manual.html) publishes an "MUF ES" map and "best estimated MUF above <grid>" from QSO and SWL reports. The algorithm is **not documented**. [verified absence of documentation]
- [DXLook](https://www.dxlook.com) also shows MUF per grid built from spots. The algorithm is **not documented**.

### 1.4 Propagation models: VOACAP and ITU-R P.533 comparisons

**ITU-R P.533-14 (08/2019)** is the current HF prediction method. [verified, [ITU](https://www.itu.int/rec/R-REC-P.533/en)]
- Basic MUF uses foF2 and M(3000)F2 at **control points**:
  - the path midpoint for paths up to 2000 km;
  - points 1000 km from each end for longer paths (×2 / ×4 control-point logic).
- [ITU-R P.1240](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1240-1-200702-S!!PDF-E.pdf) gives the basic MUF and operational MUF formulas.
- Reference software ITURHFProp is distributed by ITU. License not confirmed [unverified].
- An MIT-licensed Rust port exists: [im-alfa/p533-rs](https://github.com/im-alfa/p533-rs) [secondary].

**VOACAP compared with WSPR** (F6IRF blog, 2008: [part 2](https://f6irf.blogspot.com/2008/04/voacap-vs-wspr-reports-part2.html), [part 3](https://f6irf.blogspot.com/2008/11/voacap-reliability-end-of-myth-part-3.html)):
- **Bandwidth trap:** VOACAP SNR is per 1 Hz, while WSPR and FT8 report SNR in 2500 Hz. That is a 34 dB offset.
- Even after correcting for bandwidth, the author saw 16–20 dB residual differences on single paths. This is anecdotal.
- Published comparisons against WWV/CHU find VOACAP errors from a few dB up to 15 dB.
- **Implication:** absolute SNR-to-loss inversion through VOACAP is coarse. Use it only for relative or model-shape purposes.

**Recent paper:** Weber, Nowak, Berg and Behdad, [arXiv:2609.05811](https://arxiv.org/abs/2609.05811) (2026, IEEE OJAP). It corroborates modelled MUF using an **"FT8 operating-frequency ceiling"** from crowdsourced reports plus ionosonde MUF. Simulated MUF sat at or above the FT8 ceiling, as expected for a lower bound. [secondary: abstract only, method details not read]

### 1.5 Classical geometry: secant law, Martyn's theorem, Breit–Tuve, M-factor

These relations are the forward model behind any inversion.

**Secant law** (thin flat layer, virtual height h′, ground range D):

```
f_ob = f_v · sec φ,   sec φ = sqrt(1 + (D / 2h′)²)
```

With Earth curvature (radius R, elevation Δ):

```
D = 2R·[ (π/2 − Δ) − asin( R·cos Δ / (R + h′) ) ]
```

**Maximum single-hop range** (Δ = 0): D_max = 2R·acos(R / (R + h)).
- About 3840 km for h = 300 km (F2).
- About 2350 km for h = 110 km (Es/E).
- Practical limits with Δ ≥ 3° are about 3000–3500 km (F2) and about 2000 km (E/Es). This is where the common "≤ 2000–2500 km means single Es hop" and "1000–3000 km means single F2 hop" filters come from. *(My computation.)*

**Martyn's equivalence theorem and Breit–Tuve:**
- An oblique wave reflects at the same virtual height as the equivalent vertical frequency f_v = f_ob·cos φ.
- The oblique group path equals the equivalent triangular path.
- Both are only *testable* with group-delay measurements, which we lack. We can only *assume* h′.

**M-factor form used operationally:**
- MUF(D) = foF2 · M(D).
- Shimazaki: hmF2 ≈ 1490 / M(3000)F2 − 176 km.
- Bradley–Dudeney adds a ΔM correction depending on foF2/foE and sunspot number R12.

(See [P.1240](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1240-1-200702-S!!PDF-E.pdf) and the Shimazaki and hpF2 references found via search [secondary].)

### 1.6 Skip-distance to foF2 inversion

On a band where f > foF2, the **minimum distance** at which F-layer spots appear is roughly the skip distance d_s. Inverting:

```
foF2 ≈ f · cos φ(d_s, h′)     (equivalently  foF2 ≈ f / M(d_s))
```

**Worked example:** 14.074 MHz, d_s = 1000 km, h′ = 300 km gives sec φ ≈ 1.94 and foF2 ≈ 7.2 MHz.

**Precedent:**
- HamSCI 2018, Taft K1EHZ ([slides](https://hamsci.org/sites/default/files/publications/2018_HamSCI/20180223_030_HamSCI2018_Taft_K1EHZ_WSPRPropagation.pdf)): flat-Earth single-hop skip-distance curves compared against WSPR spot distances. [secondary]
- The LSTID paper above tracks d_s as a relative quantity.

**I found no peer-reviewed validation of absolute foF2 retrieved this way.**

**Error sources:**
- The h′ assumption: ±50 km in h′ moves foF2 by about 10–15% at 1000 km.
- Es, ground-wave and scatter spots inside the skip zone.
- Sparse sampling near d_s.
- Curved-layer and gradient effects.

### 1.7 Distance-distribution (hop-length histogram) methods

These are used throughout the Frissell papers: 2D distance × time histograms per band, and midpoint density maps.

**Features we can extract per band and time window:**
- the lower edge (skip distance);
- the modal distance;
- the upper edge;
- secondary peaks at about 2× the first-hop distance (2-hop modes).

Hop count is *inferred*, not measured:

```
n_min = ceil(D / D_max(h, Δ_min))
```

### 1.8 SNR-vs-distance and absorption

**Absorption:**
- P.533 and VOACAP model non-deviative D-region absorption as proportional to (f + f_H)^-2 and to cos(χ)^p, where χ is the solar zenith angle.
- Daytime SNR depression on low bands, relative to the same path at night, is a qualitative absorption indicator.
- Flare and PCA events show up as sudden loss of spots (Frissell 2014, 2019).
- SWPC publishes DRAP (D-Region Absorption Prediction) frequency maps [endpoint unverified, see §4].

**Constraints on FT8 SNR** ([DK3BA, March 2024](https://www.uni-due.de/imperia/md/content/hft/papers/ft8_reports_engl.pdf), [verified]):
- SNR is measured in 2500 Hz bandwidth.
- The WSJT-X detector **compresses strong signals**.
- Values are **censored at the decode floor** (about −21 to −24 dB). PSK Reporter and wsprnet only record decodes.
- Treat the data as **censored** (Tobit-type regression) or use quantiles, not plain means.

### 1.9 Data assimilation: IRI, IRTAM, GIRO, GloTEC

**IRTAM** ([Galkin et al.](https://dauntless.bc.edu/bss2016/Presentations/Session4B-DataAssimilationModeling/Galkin_AssimilationGIROinIRTAM_V3.pdf); [2021 validation](https://www.earth-prints.org/bitstream/2122/14796/1/2021_Atmosphere_IRTAM.pdf)):
- Adjusts IRI's CCIR coefficients to fit GIRO Digisonde profiles every 15 minutes.
- Validation shows it improves foF2 substantially and hmF2 only slightly. [secondary]
- Access was historically through GAMBIT at giro.uml.edu. Current status is [unverified].

**PyIRI** ([GitHub](https://github.com/victoriyaforsythe/PyIRI), **MIT** [verified LICENSE]) and **pyirtam** (MIT [verified]):
- Pure-Python IRI that returns foF2, hmF2 and foE on global grids.
- This is a pip-installable background model for QGIS.

**NOAA GloTEC** ([SWPC](https://www.swpc.noaa.gov/products/glotec)):
- Kalman-filter 3-D assimilation on a 2.5° × 5° grid, 10-minute cadence, with NetCDF/JSON download.
- Outputs TEC. PLAN.md indicates foF2/hmF2-like fields can be derived, but I could not confirm the published JSON fields [secondary]. NCEI archives it from 2025.

**ionocast** ([methodology PDF](https://ionocast.pages.dev/ionocast-methodology.pdf)): a third-party forecaster that uses wspr.live spots plus KC2G and GIRO data, with WSPR-based bias correction. [secondary, quality unknown]

---

## 2. What is realistic for our data

We have Maidenhead locators, distance, band, time, SNR and mode. We have no elevation angle, no delay, and unknown power and antennas.

| Target | Feasible? | Notes |
|---|---|---|
| **Hop count** | *Lower bound only* | n_min = ceil(D / D_max) with an assumed h (F2 ≈ 300 km, Es ≈ 110 km). We cannot tell 1-hop from 2-hop below D_max. A clustered distance histogram can flag 2-hop peaks. |
| **Layer height hmF2** | **Not invertible.** Use an external source. | Distance alone is consistent with many (h, elevation) pairs. Take hmF2 from KC2G/GIRO/IRI and use it as the *assumption* for the other products. |
| **E vs F2 vs Es** | Partially | Es: 10 m/6 m single-hop spots of 500–2300 km when the KC2G F2 MUF for that midpoint is well below the band frequency (that is, F2 cannot explain the spot). This is a strong, physically grounded classifier. |
| **foF2 / MUF** | Lower bound, plus a crude skip-distance estimate | Any F2 spot at distance D on frequency f implies MUF(D) ≥ f at the control point, a **one-sided (censored) constraint**. The skip-edge inversion gives an estimate within ±15–25% at best. Calibrate against nearby ionosondes. |
| **Skip distance** | **Yes** | Lower edge of the per-band distance distribution, for example the 5th percentile of spots more than 300 km away, over 15–30 min windows. This mirrors the LSTID method. |
| **Absorption** | Relative only | Day/night and time-series drops in spot counts or SNR on low bands. Flare blackouts are easy to detect. |
| **Openness / activity anomaly** | **Yes, the best-validated option** | KC2G: r ≈ 0.3–0.4 with the MUF anomaly on the upper HF bands. This requires a climatology baseline: weeks of our own data per cell, band and hour. |

### What reciprocal (two-way) SNR pairs add

Write the received SNR in dB as:

```
SNR_ij = T_i + R_j − L_ij(path) + ε
```

where:
- T_i = transmitter EIRP (power + antenna gain − losses);
- R_j = receiver figure of merit (antenna gain − noise floor N_j);
- L_ij = path loss;
- ε = fading and non-reciprocity.

**Difference.** For a reciprocal path (L_ij ≈ L_ji):

```
SNR_ij − SNR_ji = (T_i − R_i) − (T_j − R_j) = Δ_i − Δ_j
```

Path loss cancels exactly. Over a connected graph of many observed QSO pairs, a least-squares fit recovers the per-station offset Δ_i up to one global constant. This is DK3BA's "average Rcvd − Sent" idea generalised to a network.

**What Δ means:**
- Stations with high Δ are loud or high-power, have quiet receive sites, or both.
- QRP or urban-noise stations have low Δ.
- Single-pair differences are dominated by fading and polarization non-reciprocity (DK3BA saw up to 30 dB). DK3BA recommends averaging about 100 or more QSOs.

**Sum.**

```
SNR_ij + SNR_ji = (T_i + R_i) + (T_j + R_j) − 2·L_ij
```

The sum still mixes station quality with path loss.

**Adding our own receptions** (SNR_iM, where M is our station) and PSK Reporter third-party reports turns this into a bilinear fixed-effects problem:

```
SNR = T_i + R_j − L(d, f, t, region; θ)
```

- L is a smooth parametric function, such as a spline in distance per band and hour.
- It becomes identifiable once the graph is well connected.
- This gives a **station-normalised path-loss curve** per band and time window. That is the realistic route to absorption and propagation-quality metrics.

**Caveats:**
- The pair's two directions are not simultaneous (15 s FT8 slots).
- Real non-reciprocity exists, for example O/X mode polarization (DK3BA).
- Censoring at the decode floor biases weak paths.
- Locator errors from portable or misconfigured stations.

---

## 3. Public real-time data for calibration

| Source | Endpoint | Content | Terms |
|---|---|---|---|
| **KC2G station JSON** [verified live] | `https://prop.kc2g.com/api/stations.json` | Latest value per ionosonde: `fof2, hmf2, mufd, md, foe, foes, fbes, tec, yf2, scalef2, cs` (confidence 0–100), `time`, `source` (giro…), station `{name, code, latitude, longitude (0–360)}` | No license stated. Credit KC2G, GIRO and INGV, and contact KC2G before heavy polling (the maps refresh every 15 min). |
| **KC2G contour GeoJSON** [verified live] | `https://prop.kc2g.com/renders/current/mufd-normal-now.geojson`, `…/fof2-normal-now.geojson` (`.svg` also available) | MUF(3000) and foF2 contour polygons. **Drops directly into QGIS as a vector layer.** | Same as above |
| **KC2G eSSN** [verified live] | `https://prop.kc2g.com/api/essn.json` | Effective SSN and SFI time series (24h/6h) | Same as above |
| **GIRO DIDBase** [partly verified] | Web form `https://giro.uml.edu/didbase/scaled.php` (POST: `date_start`, `date_end`, `chosenchars[]` for foF2, foEs, MUF(D), M(D), h′Es, hmF2, …). Station list: `https://lgdc.uml.edu/common/DIDBFastStationList` [verified]. | Scaled ionogram characteristics per station | The legacy `lgdc.uml.edu/common/DIDBGetValues` GET API now **returns 404**. Its replacement is unknown. "Rules of the Road" at `ulcar.uml.edu/DIDBase/RulesOfTheRoadForDIDBase.htm` require acknowledgement; contents [unverified] because fetching failed. |
| **NOAA SWPC** [partly verified] | Directory `https://services.swpc.noaa.gov/json/` (listing confirmed live, including `f107_cm_flux.json` and `boulder_k_index_1m.json`). GOES X-ray, planetary Kp, DRAP and GloTEC paths timed out from here [unverified]. | Flare, geomagnetic, absorption and TEC context | US government data, generally public domain [general knowledge]. SWPC ionosonde distribution ended in 2024 (per KC2G). |
| **wspr.live** (ClickHouse) [verified docs] | `https://db1.wspr.live/?query=SELECT … FORMAT JSON` | Full WSPR spot archive, useful for building climatologies | Non-commercial; results must be free and publicly accessible; 20 requests/min. |
| **PSK Reporter** [verified docs] | `https://retrieve.pskreporter.info/query?…` (XML) | Last 100 reports per callsign, at most 6 h | Poll no more than once every 5 minutes; operator may block heavy users. |
| **PyIRI** | `pip install PyIRI` | Background foF2/hmF2/foE anywhere | MIT |

---

## 4. Ranked feature recommendations

### 1. Ionosonde/MUF overlay plus "spot vs ionosphere" consistency check

**Reliability:** high. It uses measured data, and our spots only provide context.

**Method:**
1. Load the KC2G MUF and foF2 GeoJSON and `stations.json` as QGIS layers, refreshed every 15 minutes.
2. Use `hmf2` from the nearest ionosonde, or PyIRI as a fallback, as h.
3. For each spot:
   - compute the control point (the midpoint if D ≤ 2000 km, otherwise 1000 km from each end);
   - compute the required MUF(D) via the M-factor or secant geometry;
   - flag spots with f > MUF_F2(D) as **"not F2: Es, scatter or other"**.

**Data:** KC2G API, spots, PyIRI.

### 2. Sporadic-E detector and map (10 m / 6 m, and 12/15 m in summer)

**Reliability:** medium-high in Es season. Height and foEs are assumption-based, roughly ±20%.

**Method:**
1. Keep spots with 500 ≤ D ≤ 2300 km that are inconsistent with the F2 MUF from feature 1.
2. Cluster the midpoints in space and time (DBSCAN over 15 min windows) to form "Es cloud" polygons.
3. Estimate foEs ≈ f · cos φ(D, h = 105 km) and track cloud drift.

**Data:** PSK Reporter, live decodes, KC2G `foes` where an ionosonde is nearby for validation.

**Basis:** Deacon et al. 2022.

### 3. Per-band skip-distance and hop-structure time series

**Reliability:** high for *relative* changes (TIDs, terminator, flares); low-medium for absolute foF2.

**Method:**
1. Build a distance-vs-time histogram per band (25–100 km × 5–15 min), following Frissell 2019 and 2022.
2. Extract the lower-edge skip distance, the modal distance and secondary peaks; derive n_min.
3. Optionally invert the skip distance to foF2_est = f / M(d_s), using ionosonde hmF2, and show it **side by side with the nearest ionosonde foF2** as a residual, never as truth.

**Data:** our spots plus PSK Reporter, grouped by path-midpoint region.

### 4. Reciprocal-SNR station calibration and normalised path-loss curves

**Reliability:** medium. It needs about 100 or more pairs per station to converge, and is noisy per QSO.

**Method:**
1. Solve the network least-squares problem for Δ_i from the two-way pairs.
2. Extend to SNR = T_i + R_j − L(d, band, hour), using censored (Tobit) regression on our receptions plus PSK Reporter.
3. Display the resulting L-curves per band and time window. Daytime excess loss on low bands is an absorption index; changes from the station's climatology indicate degradation.

**Data:** reconstructed WSJT-X exchanges, our own SNRs, PSK Reporter.

### 5. Band-activity anomaly nowcast

**Reliability:** medium, validated by KC2G at r ≈ 0.3–0.4 against the MUF anomaly. It needs 2–4 weeks of baseline data before it is useful.

**Method:**
1. For each 5° midpoint cell (or Maidenhead field), band and UT hour, compute log(spot count) and median SNR minus a rolling 28-day median.
2. Show this as a "bands hotter or colder than usual" grid.
3. Flag sudden region-wide drops that coincide with GOES X-ray flares (SWPC) as absorption events.

**Data:** PSK Reporter history (or a local archive), plus the SWPC JSON feeds.

---

## Items I could not verify

- The exact foEs and height method in Deacon et al. 2022 (full text blocked).
- The details of the FT8 ceiling method in arXiv:2609.05811.
- The current GIRO programmatic API (the old DIDBGetValues endpoint is gone) and the full DIDBase Rules of the Road.
- The KC2G data license.
- The specific SWPC GOES, Kp, DRAP and GloTEC JSON paths (network timeouts).
- The license of the ITU ITURHFProp software.
- The current status of IRTAM/GAMBIT access.
