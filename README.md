# Haulcast

Weather-aware truck routing: three routes scored with hourly forecasts at each checkpoint, and one recommended route.

Live: https://corridor-truck-routing.onrender.com

![Storm demo at 45,000 lb: Route B is recommended because the 35 to 44 mph wind on Route A is Severe for a heavy load](plan.png)

## Try it

1. Open the live app and click **Run the storm demo**. Live weather is often calm, so the demo uses recorded Dallas to Chicago routes and a scripted storm.
2. At 45,000 lb Route B wins: the 35 to 44 mph wind on Route A counts as Severe, and B has no Severe miles. Switch to 20,000 lb and the same wind is only High, so Route A wins.
3. Drag the forecast slider. The heatmap and the truck markers move with no network request.
4. Plan two US cities with live TomTom routes and Open-Meteo weather.

The app runs on Render's free plan. The first load after a quiet period can take up to a minute.

## Run locally

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/) and Node 22. `backend/.python-version`, `.nvmrc` and `frontend/package.json` declare both.

```sh
make setup

# terminal one
make dev-backend

# terminal two
make dev-frontend
```

`make dev-backend` serves recorded routes and the scripted storm, so it needs no keys. For live routes, put `TOMTOM_API_KEY=...` in `backend/.env` and run `make dev-live` instead. Open-Meteo needs no key.

## Tests and deploys

```sh
make check   # ruff, ty, Django checks, frontend build
make test    # Playwright against the real UI
```

The Playwright suite drives the real UI against recorded routes. CI runs lint, types, the production build, those tests and a Docker image smoke test. Render deploys `main` only after CI passes.

## Decisions

- **One entry point.** The root `Makefile` wraps both halves of the repo, and each target mirrors a CI job, so a local pass means what a green CI means.
- **One owner for the rules.** Thresholds and load rules live in `backend/trips/rules.py`. The API sends levels as numbers; `frontend/src/lib/levels.ts` maps them to names and colours.
- **One request per plan.** The response carries the routes, checkpoints and all 49 forecast hours, so the slider only repaints the map.
- **No database.** A plan is computed from live data; weather is cached per cell for 30 minutes in the one Gunicorn process.
- **Small state, small surface.** One container serves the API and the built front end, with the key only on the server.
- **End-to-end tests.** They fail the way a user would notice: a wrong band edge, ranking or mile count.

`AGENTS.md` has the rules, pipeline, API contract and trade-offs.

## Stack

Django 5.2, Pydantic, React 18, TypeScript, Vite, MapLibre GL, TomTom Routing and Search, Open-Meteo, Render.
