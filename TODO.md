# To do

1. **Import WSJT-X ALL.TXT**: a Processing tool that runs a folder of `ALL.TXT` files through the same exchange tracker as the live map. Output is an exchanges line layer and a station point layer for past sessions, filterable by band and time.
2. **Save live sessions automatically**: write live stations and exchanges to a GeoPackage as they arrive, so a session survives closing QGIS. Today it only survives if you use *Make Permanent* by hand.
3. **Activity timeline for the live layers**: reports per hour and window selection for *WSJT-X live* and *WSJT-X exchanges*, not only PSK Reporter layers.
4. **Hop geometry and takeoff angle**: for each path (PSK Reporter and exchanges), use distance and an assumed or measured layer height to list the possible hop counts. Rule some out using the MUF and a realistic takeoff-angle range (about 2–35°). Add fields `hops_likely`, `takeoff_deg`, and `reflection_lat`/`reflection_lon` at each hop's midpoint, plus a reflection-point layer. Keep the geometry in pure Python with tests.
5. **Ionosonde / model enrichment**: fetch measured layer height (hmF2), critical frequency (foF2) and the propagation factor M(3000)F2 for each path's reflection points and time. Sources, checked 2026-11-11:
   - KC2G `https://prop.kc2g.com/api/stations.json`: foF2, hmF2, MUF(3000) and foEs per ionosonde, with a confidence score.
   - KC2G `https://prop.kc2g.com/renders/current/mufd-normal-now.geojson` and `fof2-normal-now.geojson`: contour maps that load straight into QGIS.
   - PyIRI (MIT licence) as a modelled fallback, for historical data.
   - GIRO's old GET API now returns 404; only the web form works, so historical measured data has no API yet.
   - KC2G states no data licence: ask before redistributing.
   Feeds item 4 so it uses measured heights instead of assumed ones. Optionally add space-weather context from NOAA SWPC (solar flux, Kp, X-ray flares).
