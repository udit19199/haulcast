# Haulcast: design and working notes

Weather-aware truck routing: Django API (`backend/`) plus React + MapLibre UI (`frontend/`). This file is the source of truth for the risk rules, the ranking, the API contract and the module map. Read it before changing logic or the contract.

## 1. What it does

A driver enters an origin, a destination, a departure time and a cargo weight. The server asks TomTom for up to three truck routes, places checkpoints every 10, 25 or 50 miles, reads Open-Meteo for the hour the truck reaches each checkpoint, scores every checkpoint with the risk and load rules below, ranks the routes and returns one recommendation with a reason. The response also carries a heatmap for the whole corridor, hour by hour, from departure to 48 hours later.

The driver is the user: on a phone, with little time. The verdict and its reason come first; the per-checkpoint numbers are there for anyone who wants to check them. A dispatcher can use the same screen.

Out of scope for this version: accounts, saved trips and history, live re-routing while a truck is moving, and driver-entered planned stops.

## 2. Rules

`Level` is ordered: `LOW=0, MODERATE=1, HIGH=2, SEVERE=3, NO_TRAVEL=4`. The number is also the score used for average risk.

| Condition | Low | Moderate | High | Severe | No Travel |
|---|---|---|---|---|---|
| Wind (mph, sustained) | < 25 | 25 to < 35 | 35 to < 45 | 45 to < 55 | ≥ 55 |
| Rain (in/hr, rain + showers) | < 0.10 | 0.10 to < 0.25 | 0.25 to < 0.50 | 0.50 to ≤ 1.00 | > 1.00 |
| Snow (in/hr, snowfall) | < 0.5 | 0.5 to < 1.0 | 1.0 to < 2.0 | 2.0 to ≤ 3.0 | > 3.0 |

A value on a shared edge goes to the higher band; 1.00 in/hr of rain and 3.0 in/hr of snow stay Severe. Values are rounded before classification, so the number on screen matches its level.

| Wind | Cargo | Level becomes |
|---|---|---|
| ≥ 55 mph | any | No Travel |
| 45 to < 55 mph | > 30,000 lb | No Travel |
| 35 to < 45 mph | > 40,000 lb | Severe |

"Over" is strict: 40,000 lb with 40 mph wind stays High, 40,001 lb is Severe.

A checkpoint takes the worst of wind, rain and snow. Its cause names the condition that set it, with ties going to wind, then snow, then rain.

Checkpoints sit at mile 0, N, 2N, ... and at the destination. Each covers the miles halfway to its neighbours, so the covered miles add up to the route. A checkpoint exactly 1 mile from the destination is kept.

Routes sort by this key, smallest first, and the first is recommended:

1. No Travel miles
2. Severe miles
3. High miles
4. Average risk = Σ(level × covered miles) / route miles, rounded to 0.01
5. Travel time

The ranking starts at Severe; No Travel is worse, so it goes first. Miles are rounded to 0.1 before comparing. The sort is stable, so a full tie keeps TomTom's order.

The reason compares the winner with each other route on the first key where it wins, lists the most decisive comparison first, and states the time cost when the safer route is slower. If the winner still has Severe or No Travel miles, it says so. At 45,000 lb in the demo:

> Route B: fewer Severe miles than Route A (0 vs 75), 11 min slower; fewer High miles than Route C (175 vs 400).

## 3. Pipeline

1. TomTom truck routing, three routes, vehicle weight = cargo + 35,000 lb tare, `traffic=false` (live traffic made TomTom drop alternatives; historical speeds still shape ETAs).
2. Cumulative miles along each track; checkpoints at the chosen interval plus the destination; driving time interpolated from TomTom's travel times, then a 10-hour break after each 11 hours of driving. A point reached at exactly 11 hours is before the break. Track positions stay still during breaks.
3. Weather cells within 15 miles of any track, plus any checkpoint cell. The grid is 0.25°, moved to 0.5° or 1° only when 0.25° would exceed 250 cells.
4. Open-Meteo hourly forecast for the needed hours, batched 100 cells per request, up to four requests in parallel, cached per cell for 30 minutes as one series, sliced when it covers the window.
5. Classify every checkpoint, summarise the route by level, rank, and write the reason.
6. Heatmap: the same classifier for every cell and each of the 49 hours.

Measured: 85 ms for the demo plan; a live Dallas to Chicago plan takes 10.4 s cold, 7.9 s warm, most of it TomTom. With a 25 mile interval the response is 216 KB raw, 52 KB gzip. Two cold plans that overlap fetch their own cells; the cache covers repeat and nearby plans.

## 4. API

Times are ISO 8601 UTC. Coordinates are `[lon, lat]`. Errors are `{"error": "<sentence>"}`.

| Endpoint | Success | Errors |
|---|---|---|
| `GET /api/health` | `200 {"ok": true}` | none |
| `GET /api/geocode?q=dal` | `200 [{label, lon, lat}]`, up to 5, US only; `[]` under 2 characters | 429, 502 |
| `POST /api/plan` | `200 PlanResponse` | 400 validation, 413 body over 2.5 MB, 422 no route, 429 throttle, 502 provider |
| other `/api/...` | none | 404 |

Validation: body is a JSON object; places have a label and valid coordinates and are at least 1 mile apart; departure has a time zone and is between 1 hour ago and 12 days ahead; cargo is a whole number from 1 to 45,000 lb; interval is 10, 25 or 50; `demo` is a boolean when present.

```ts
type Level = 0 | 1 | 2 | 3 | 4;

interface PlanRequest {
  origin: Place; destination: Place;   // Place = { label: string; lon: number; lat: number }
  departure: string; load_lb: number; interval_mi: 10 | 25 | 50;
  demo?: boolean;                       // recorded Dallas to Chicago routes and the scripted storm
}

interface PlanResponse {
  departure: string;                    // UTC
  load_lb: number; interval_mi: 10 | 25 | 50;
  recommended_id: string; reason: string;
  routes: Route[];                      // ranking order
  heatmap: Heatmap;
}

interface Route {
  id: string; name: string; distance_mi: number;
  duration_s: number; arrival: string; // total elapsed trip time and UTC arrival, including breaks
  track: [number, number, number][];    // [lon, lat, elapsed seconds after departure], stationary during breaks
  checkpoints: Checkpoint[];
  summary: { miles_by_level: [number, number, number, number, number]; avg_risk: number; worst: Level };
}

interface Checkpoint {
  mile: number; lon: number; lat: number; eta: string; covers_mi: number;
  weather: { wind_mph: number; gust_mph: number; rain_in_hr: number; snow_in_hr: number };
  levels: { wind: Level; rain: Level; snow: Level };
  level: Level; cause: string;
}

interface Heatmap {
  cell_deg: number;                     // 0.25, 0.5 or 1
  start: string;                        // departure floored to the UTC hour
  cells: { lon: number; lat: number; levels: Level[] }[];   // levels[h] = start + h hours, 0..48
}
```

## 5. Decisions

- **TomTom for routes, Open-Meteo for weather.** TomTom has a real truck profile, so weight changes the route and the times. Open-Meteo takes 100 locations per request, so a corridor costs a few calls. Both have free tiers.
- **One request per plan.** The response carries all 49 heatmap hours. A slider move sets one feature state per cell and replaces a three-point truck source; it never rebuilds the GeoJSON or touches the style. With a 4x CPU throttle the p95 frame time was 32.6 ms, with no source reload.
- **No database.** A plan is computed fresh. One Gunicorn worker owns the in-memory weather and geocode cache; threads carry the I/O. Redis is the first step past one instance.
- **Adaptive cells.** Open-Meteo's 600 locations per minute is the binding limit. Cells stay at 0.25° where that fits, grow on long trips, and live plans throttle to six a minute per client (demo plans call no provider, so they skip it).
- **Demo data.** Recorded Dallas to Chicago routes and a scripted storm make the Playwright suite repeatable without keys, and show the load rule change the pick on a calm day. The UI labels it as not live weather.
- **Keys and errors stay on the server.** `TOMTOM_API_KEY` never reaches the browser, logs or tracebacks. Provider failures are logged as service, status and elapsed time, then mapped to 502 with a plain sentence. Input is validated before any provider call.
- **Failure behaviour.** A replan that fails leaves the previous results with a banner. A map that cannot load leaves the routes and checkpoints in the panel. A provider that returns unreadable data is a 502, not a crash.

## 6. Limits and next

ETAs apply the default US hours-of-service rule: after 11 hours of driving, a 10-hour break, repeating. Checkpoint weather, arrival, total trip time and the truck track all include these breaks; arrival exactly at a driving boundary needs no further break. Driver-entered planned stops remain next.

After that: times in the route's own time zone, scoring gusts for empty trailers, moving the cache to Redis, and a paid instance so the app never sleeps.

## 7. Working on it

### Commands

The root `Makefile` is the one entry point; CI runs the same steps.

| Task | Command |
|---|---|
| Install both halves | `make setup` |
| Backend dev server, no keys | `make dev-backend` |
| Backend dev server, live providers | `make dev-live` (reads `TOMTOM_API_KEY` from `backend/.env`) |
| Frontend dev server | `make dev-frontend` (proxies `/api` to port 8000) |
| Ruff, format check, ty, Django checks, frontend build | `make check` |
| End-to-end tests | `make test` (installs nothing; builds the frontend, starts Django with `DEMO_DATA=true`) |
| Workflow lint | `uvx --from actionlint-py==1.7.12.25 actionlint && uvx zizmor==1.32.0 .github/` |

### Verification by change type

| Change | Run before calling it done |
|---|---|
| `backend/trips/rules.py`, `wording.py`, `planner.py`, `geometry.py`, `providers.py` | `make check`, `make test` |
| `backend/trips/tomtom.py`, `weather.py`, `remote.py`, `demo.py` | `make check`, one live request against the real API, `make test` |
| `frontend/src/**` | `make check`, `make test`, and look at the page in a browser |
| API contract | Update section 4 and `frontend/src/api.ts` in the same change, then `make test` |
| `frontend/tests/**` | `make test` passes twice in a row |

### Working rules

- CI (`.github/workflows/ci.yml`) runs every check in the table above plus a Docker build and smoke test. Render deploys `main` only when CI passes, so never push to `main` with a failing check. Pin new actions to a commit SHA with the version in a comment.

- Toolchain versions are pinned in `.nvmrc`, `frontend/package.json` (`engines`), `backend/.python-version`, `Dockerfile` and `.github/workflows/ci.yml`. Change them in all five places together, and never let CI float. `backend/.python-version` must stay next to `pyproject.toml`, because uv stops looking for it at the workspace root.
- Risk thresholds and load rules live only in `backend/trips/rules.py`. The API sends levels as numbers; the front end maps them to names and colours only in `frontend/src/lib/levels.ts`. Never copy thresholds into the front end.
- Never put an API key in code, demo data, logs or the browser bundle. Keys come from env vars; `backend/.env` is ignored by git.
- Never commit local notes or working files.
- Never add unit tests. Coverage comes from the Playwright suite in `frontend/tests/`, which runs against the recorded demo data.
- The heatmap slider must not trigger a network request, rebuild the heat GeoJSON or change the style. A move sets one feature state per heat cell and replaces the three-point truck source (see section 5). Keep it that way.
- Do not run React Doctor (`react-doctor` 0.9.22) locally. Its process keeps growing after it prints results (tens of GB, even with `--no-parallel`). Check React code with `tsc` and the Playwright suite.
- Demo data in `backend/trips/demo_routes.json` is recorded TomTom output. Re-record it with a script; do not hand-edit coordinates.
