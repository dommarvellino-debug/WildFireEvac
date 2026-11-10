# Wildfire Global Awareness Web App — Architecture & Roadmap

Status: draft plan, no code written yet. Written 2026-11-10 against the
current repository state; product decisions locked 2026-11-10 (Section 10).

## 1. Objective

Grow the existing single-purpose hotspot map into one integrated web app that
shows, for the whole world:

1. **Live detections** — where satellites are seeing fire activity right now.
2. **Forecast context** — fire-weather conditions and, later, threat
   probabilities from the project's survival models.
3. **News** — worldwide wildfire news coverage.
4. **Reports** — official multi-hazard wildfire event reports with severity.

The app stays a **situational-awareness tool**, not an emergency-decision
tool. Every panel keeps the existing disclaimers: satellite hotspots are not
verified perimeters, evacuation boundaries, or incident reports.

## 2. Current state inventory

| Capability | Status | Location |
|---|---|---|
| FIRMS hotspot fetch (validation, key sanitization, geo-filtering) | Done | `src/wildfire_evacuation_risk/firms.py` |
| Live pydeck world map with tooltips and records table | Done | `app.py` |
| Unit tests with mocked `urlopen`, secret-safe errors | Done | `tests/test_firms.py` |
| Survival models (Cox / RSF / GBS), CV, metrics, labels | Done, offline | `modeling.py`, `cross_validation.py`, `metrics.py`, `labels.py`, `scripts/` |
| Auto-refresh | Missing | — |
| News feed | Missing | — |
| Official reports feed | Missing | — |
| Forecast panel in the UI | Missing | — |
| Model predictions wired to live data | Missing | — |

Constraints inherited from the current design that the plan must respect:

- API keys live only in backend environment variables, never in browser code,
  Git, logs, or error text (`firms.py` already enforces this pattern).
- Coverage, feed, and look-back (1–5 days, FIRMS cap) are sidebar controls.
- Streamlit >= 1.40 is already required, so `st.fragment(run_every=...)`
  auto-refresh is available without dependency changes.

## 3. Target experience (UI blueprint)

```
┌────────────────────────────────────────────────────────────────────┐
│ WildFireEvac · Global Wildfire Awareness        ● Live · 12:03 UTC   │
├───────────────┬────────────────────────────────────────────────────┤
│ SIDEBAR       │  Tabs:  [Reports] [Live Map] [Forecast] [News]      │
│               │                                                    │
│ Coverage    ▼ │  ── tab content (see below) ──                     │
│ Feed        ▼ │                                                    │
│ Look-back   ▾ │                                                    │
│ Auto-refresh ▾│                                                    │
│ [Refresh]     │                                                    │
├───────────────┴────────────────────────────────────────────────────┤
│ Sources & attribution · not for emergency decisions · last fetch ×  │
└────────────────────────────────────────────────────────────────────┘
```

`Reports` is the default landing tab. Opening a report card reveals its
related news inline (see "News-to-report matching rules" below).

### Tab: Live Map (extends what exists today)
- Existing pydeck hotspot layer, metrics row, and records expander.
- New: per-country/region detection counts and a daily-detections trend chart
  over the look-back window; "stale data" badge when the last fetch failed.

### Tab: Forecast
Three capability tiers, delivered in phases (Section 5):
- **Tier 1 — detection analytics**: detection counts by day, FRP distribution,
  confidence mix. Descriptive, available immediately from existing data.
- **Tier 2 — fire-weather context**: for the top active hotspot clusters,
  hourly temperature / humidity / wind / gusts / precipitation for the next
  72 h from a weather forecast API.
- **Tier 3 — model threat probabilities**: the survival models' `prob_12h /
  24h / 48h / 72h` for tracked incidents. Gated on a feature pipeline.

### Tab: Reports (default landing tab)
- Official GDACS wildfire events with alert levels (Green / Orange / Red),
  event dates, country, description snippet, and link to the official report
  page.
- Each report card carries a **Related news** expansion. Opening it runs a
  GDELT query built from the report's event/place names, country, and start
  date (English only, `ArtList` mode) and shows matching headlines with
  publisher and seen time. The report is the anchor; news for that event
  surfaces on demand.
- Region summary cards: detections, change vs. previous day, max FRP, report
  count.

### Tab: News
- Global wildfire news feed, independent of reports: title, publisher, seen
  time, outbound link (opens on the publisher's site).
- Filters: keyword; English only for now.
- Auto-refreshed; each row keeps its source visible.

### News-to-report matching rules
- Query built from report fields: strip the leading "Wildfire / Forest fires
  in" prefix, quote the place name, add the first GDACS country if it is not
  already in the place, then `sourcelang:english`; `timespan` from report start
  to now; `mode=ArtList`; `maxrecords=25`.
- GDELT DOC has no coordinate search, so geographic relevance comes from
  names and country keywords. Results are labeled "related headlines
  (keyword match, not verification)".
- Cache each report's result for ~15 minutes; a failed or empty query shows
  "no related news found" rather than an error.

## 4. Data sources

Verified characteristics as of 2026-11. Re-verify terms before launch.

| Source | Provides | Auth | Limits / latency | Notes |
|---|---|---|---|---|
| NASA FIRMS (existing) | VIIRS/MODIS NRT hotspot detections, CSV | Free `MAP_KEY` env var | Query window 1–5 days; NRT data typically available ~3 h after satellite observation | Already integrated and tested |
| GDELT DOC 2.0 | Global news articles in 65 languages, JSON `ArtList` (title, URL, domain, language, seendate) | None | ≤ 250 records per query; index refreshes every ~15 min; pace requests ~1 per 5 s (HTTP 429 otherwise); rolling ~3-month window | Primary news source, used for both report-linked queries and the global feed. English-filtered for now. Errors can arrive as HTTP 200 with plain-text body — parser must handle that |
| GDACS web API | Multi-hazard event reports incl. wildfire (`WF`), GeoJSON, alert level + severity | None | ≤ 100 records per page (paginate); ordered by `todate` desc | Reports tab. `fromdate`/`todate` filter by *start* date, so the client fetches recent pages and applies the look-back window to each event's last report date. `iscurrent` is a string; alert levels are Green/Orange/Red. Terms state automatic alerts are not for decision-making without official confirmation |
| Open-Meteo forecast API | Hourly weather (temp, RH, wind, gusts, precipitation, soil moisture), up to 16 days, multiple coordinates per request | None | Non-commercial free tier: 10,000 calls/day, 5,000/hour, 600/minute; CC BY 4.0 attribution required | Fire-weather context for cluster centroids. Bulk multi-location requests keep call counts low |
| Google News RSS | Keyword-filtered news, XML | None | Unofficial feed, no SLA; 100-item feed | Implemented as an automatic fallback when GDELT fails or rate limits |
| WorldFires API / OpenWeatherMap FWI | Precomputed Fire Weather Index per point | None / key + access request | 2 req/s, 15-min caching (WorldFires) | Optional FWI display; requires visible attribution. Only if local FWI computation is not built |
| InciWeb (US) / ReliefWeb | US incident reports / humanitarian reports | None | US-only / global humanitarian scope | Later additions; verify terms first |

Selection rationale: GDELT + GDACS + Open-Meteo need **no API keys**, so the
only credential in the app remains the existing `FIRMS_MAP_KEY`.

## 5. Forecast strategy (the honest part)

**Tier 1 (detection analytics)** — safe to ship immediately. No new claims:
it shows what satellites detected, aggregated.

**Tier 2 (fire-weather context)** — shows *weather that influences fire
behavior*, clearly labeled as weather, not fire prediction. Straightforward:
pick the N most active hotspot clusters, fetch their forecast grid cells
(Open-Meteo supports comma-separated coordinate lists in one call), render
72-hour panels.

**Tier 3 (survival-model threat probabilities)** — the project's actual
forecast capability, but currently offline-only. Wiring it live requires:

1. **Feature parity.** The models train on the 221-fire dataset's feature
   columns. A live pipeline must derive the same columns for tracked
   incidents from FIRMS + weather + static geography. Any column that cannot
   be derived live blocks this tier until a proxy or imputation strategy is
   validated.
2. **Incident construction.** Hotpoints must be clustered into "incidents"
   (DBSCAN-style spatial-temporal clustering), because the models predict per
   fire, not per detection.
3. **Model serving.** Load a fitted model from `artifacts/` and call
   `predict_hit_probabilities` on live feature rows.
4. **Displayed caveats.** The README already documents that 72 h has no
   known-safe labels in training and survival tails are flat extrapolations.
   The UI must show probabilities with these limitations, not as certainty.

Tier 3 is a research experiment, not a UI task and not a competition
deliverable. The training data is treated as test data; schedule Tier 3 only
after Tiers 1–2 are stable.

## 6. Real-time update strategy

Poll cadence aligned to each source's actual freshness:

| Panel | Poll interval | Why |
|---|---|---|
| Hotspots (FIRMS) | 10–15 min | NRT feed updates ~3-hourly; faster polling adds nothing |
| News (GDELT) | 10–15 min | Index refreshes ~15 min |
| Reports (GDACS) | 15–30 min | Server-side cache, slow-changing events |
| Weather (Open-Meteo) | 3–6 h | Model runs update every 1–6 h depending on provider |

Mechanics:

- Wrap each tab/section in `st.fragment(run_every=...)` so panels re-render
  independently without full-page reloads.
- Cache with `st.cache_data` TTLs matching the table above; fragments read
  cache and only hit the network when stale.
- On fetch failure: keep the last good cache, show a stale-data badge with
  the last-successful time, and back off (do not hammer an erroring API).
- GDELT rate limit (~1 request / 5 s) is enforced client-side by pacing; a
  10-minute cycle is far below it.

True push (WebSockets/SSE) is deliberately out of scope: no source here
updates faster than 15 minutes, so push would add architecture without adding
freshness. Revisit only under the migration triggers in Section 8.

## 7. Architecture

```
Streamlit app (app.py + tab modules)
        │
        ├── wildfire_evacuation_risk.firms     (existing)
        ├── wildfire_evacuation_risk.news      (new: GDELT client)
        ├── wildfire_evacuation_risk.google_news (new: Google News RSS fallback)
        ├── wildfire_evacuation_risk.gdacs     (new: GDACS WF client)
        ├── wildfire_evacuation_risk.weather   (new: Open-Meteo client)
        ├── wildfire_evacuation_risk.clusters  (new: hotspot → incident clustering)
        └── wildfire_evacuation_risk.modeling  (existing, used by Tier 3)
```

- `app.py` stays the entry point; tab rendering moves into small modules
  (e.g. `src/wildfire_evacuation_risk/ui/` or Streamlit `pages/`) once it
  grows past one screen.
- Every new fetcher follows the `firms.py` pattern: validate inputs, parse
  defensively, raise sanitized `RuntimeError`/`ValueError`, return a typed
  DataFrame. Keys never appear in error text.
- Every fetcher gets `tests/test_<module>.py` using the existing
  mocked-`urlopen`, fixture-bytes style. GDELT tests must include the
  HTTP-200-plain-text error case.
- Clustering output schema is the seam between map analytics and Tier 3:
  design it once (`cluster_id`, centroid, first/last seen, detection count,
  mean FRP, bounding box).

Deployment: **local-first (decided 2026-11-10).** For a single-user
situational-awareness tool this is the right default:

- No secrets store to manage — `FIRMS_MAP_KEY` stays a local env var.
- No public exposure, no abuse or rate-limit risk, no redistribution
  questions for third-party data.
- The repo can stay private.
- Trade-off: the app only runs while this machine is on and the venv exists;
  no sharing with other people and no phone access.

Keep the app stateless and configuration-driven so a later move is
configuration, not a rewrite. If remote access is ever wanted, options in
order of effort: a private tunnel to the local app, then Streamlit Community
Cloud (free, non-commercial, matching all chosen sources; `FIRMS_MAP_KEY` in
the app's secrets store; the repo would need linking — verify no secrets in
Git history first).

### Others running it locally
Distributing the app so other people run their own copy is a separate concern
from hosting one shared instance; it is planned in Section 11.

## 8. Phased roadmap

### Phase 1 — Reports-first shell + report-linked news (S–M)
- Restructure `app.py` into the tab layout of Section 3, Reports as the
  default landing tab.
- Add `gdacs.py` + Reports tab (WF events, alert colors, outbound links).
- Add `news.py` (GDELT): the report-linked "Related news" expansion first,
  per the matching rules in Section 3.
- Add `st.fragment` auto-refresh with sidebar toggle and interval picker;
  stale-data badges and error backoff.
- Make it installable by others (Section 11): split core vs. optional
  `models` dependencies, let the UI run without `FIRMS_MAP_KEY`, add a
  cross-platform quickstart.
- Acceptance: the app opens on Reports; opening a report shows fresh related
  English headlines without a full-page reload; all fetchers have passing
  mocked tests; no key material in any output; on a clean Python 3.10+
  machine the documented quickstart installs core dependencies only and the
  app launches without a FIRMS key (map shows a setup notice).

### Phase 2 — Global news feed + detection analytics (S–M)
- News tab: standalone global wildfire feed using the same GDELT client,
  keyword filter, English only.
- Live Map tab: per-region counts and daily detection trend chart (Tier 1).
- Region summary cards on Reports (detections, change, max FRP, report
  count).
- Acceptance: News tab refreshes on cadence; trend chart reflects the
  selected look-back window; summary cards update with the selected
  coverage.

### Phase 3 — Fire-weather context (M)
- Add `weather.py` + `clusters.py` (spatial-temporal clustering of hotspots).
- Forecast tab Tier 2: 72-hour weather panels for top active clusters,
  bulk-fetched in one Open-Meteo call; attribution line visible.
- Acceptance: selecting a cluster shows its weather panel; weather call
  count stays well under daily limits under continuous auto-refresh.

### Phase 4 — Model experiments (optional, L, low priority)
The training dataset is used as test data, not for competition scoring, so
this phase is optional and comes after the situational-awareness features.
- Feature pipeline mapping training features onto live incident rows.
- Model serving from `artifacts/` with `predict_hit_probabilities`.
- Forecast tab Tier 3: per-incident threat probabilities with explicit
  limitations panel.
- Acceptance: probabilities only displayed for incidents whose full feature
  vector is derivable; UI states the 72 h label limitation verbatim.

### Phase 5 — Optional migration (L, only if triggered)
Migrate to FastAPI + React/Next.js with SSE/WebSocket only if: the app needs
public multi-user deployment, sub-15-minute push becomes genuinely valuable,
or Streamlit's rerun model becomes the bottleneck. Until a trigger fires,
Streamlit remains the stack.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Overstating what the data means | Persistent footer disclaimer; hotspots labeled as detections; weather labeled as context; model output labeled as research estimates with caveats |
| GDELT returns errors as HTTP 200 plain text | Parser checks content type; tests cover this case |
| Rate limits (GDELT ~1/5 s; GDACS 100/page; Open-Meteo 10 k/day) | Cache TTLs, paced polls, backoff on failure |
| Key leakage | Keep the `firms.py` pattern: env var only, sanitized errors; new sources need no keys |
| FIRMS 5-day look-back cap | Document as a product limit; trend charts limited to the same window |
| GDACS terms: automatic alerts are not for decisions | Never present GDACS alerts as evacuation guidance |
| Third-party small FWI APIs disappearing | Treat FWI providers as optional; compute FWI locally from Open-Meteo variables if it becomes important |
| Model misuse | Tier 3 gated behind Phase 4 acceptance criteria |
| Install friction for other users | Core/optional dependency split; UI runs without the model stack; cross-platform quickstart in README |

## 10. Decisions (locked 2026-11-10)

1. **Coverage**: keep **global** as the default, with Indonesia and Kalimantan
   as existing regional presets.
2. **News language**: **English only** for now. GDELT translates 65 languages,
   so English queries still surface foreign coverage.
3. **Deployment**: **local only** for now; no hosted deployment. See Section 7
   for the rationale and the escape hatches if that changes.
4. **Models**: competition scoring is ignored; the survival models and their
   training set are treated as a data experiment. Phase 4 is optional and
   deprioritized.
5. **Flow order**: **reports first**; related news surfaces from each report.
   The global news feed is secondary.
6. **Distribution**: publish as a **public GitHub repository**, sequenced
   after the Phase 1 distribution fixes (Section 11.1).

Remaining open questions (non-blocking):

- Which GDACS fields to show by default vs. behind an expander (description
  length, alert score)?
- Keyword defaults for the global News tab (e.g. `wildfire OR "forest fire"
  OR "bushfire"`)?
- Whether to add more saved bounding-box presets beyond the current three.

## 11. Distribution: other people running it locally

Feasible: yes. "Run it locally" means each person installs Python, gets the
repo, and launches the app on their own machine — no server, no accounts, no
shared infrastructure. Three things must be fixed before handing it to others.

### 11.1 Access to the code
The GitHub repo is currently **private** (verified: the public URL returns
404). **Decided 2026-11-10: publish as a public repository.** The canonical
remote is `wildfireevac`
(`https://github.com/dommarvellino-debug/WildFireEvac`, default branch `main`);
the second remote, `firetrack`, does not exist on GitHub and is stale — remove
it. Prerequisites: add a `LICENSE` file and publish only after the Phase 1
distribution fixes below, so the first public version installs and runs on a
clean machine. Channels considered:

| Channel | Best for | Notes |
|---|---|---|
| Make the repo public | Developers who know Git | Simplest; confirm no secrets ever entered Git history first (`.gitignore` already excludes `.env`, `.streamlit/secrets.toml`, `artifacts/`) |
| Collaborators on the private repo | A small trusted group | Needs a GitHub account and an invite per person |
| Zip / release archive | Non-technical users | No Git required; exclude `.venv/` and `artifacts/` |

Publishing checklist (after Phase 1):

1. Add a `LICENSE` file (MIT or Apache-2.0 for the code; provider data terms
   stay separate).
2. Commit and push the docs and Phase 1 changes.
3. Remove the stale `firetrack` remote (`git remote remove firetrack`);
   keep `wildfireevac`.
4. Flip visibility (GitHub UI → Settings → General → Danger Zone, or
   `gh repo edit <owner>/<repo> --visibility public`).
5. Clone the public URL fresh into a temp directory and follow the quickstart
   end-to-end.

### 11.2 Each user needs their own FIRMS key
The FIRMS map key is personal and rate-limited; it must not be redistributed.
Registration is free. Fix: make the app degrade gracefully — without a key,
the Live Map tab shows a short "set FIRMS_MAP_KEY" setup notice while
Reports, News, and Forecast weather still work, since those sources need no
key. This replaces the current `st.stop()` in `app.py`.

### 11.3 Installation friction
`pyproject.toml` installs the full model stack for everyone. On this machine
`scikit-survival 0.25.0` installed fine, but it remains the hardest
dependency on other platforms. The app itself (Phases 1–3) needs only
`pandas`, `streamlit`, `pydeck`. Fix: split dependencies:

- core: `pandas`, `streamlit`, `pydeck`
- optional `models` extra: `scikit-learn`, `scikit-survival`, `matplotlib`

`pip install -e .` stays light; `pip install -e .[models]` adds the offline
model stack. Also add macOS/Linux steps to the README (currently
Windows-only) and a quickstart block at the top.

### 11.4 Target setup for a new user
```
git clone <repo-url>
cd wildfireevac
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
streamlit run app.py
```
Open http://localhost:8501; enter your own FIRMS key (after 11.2 lands).

### 11.5 Optional conveniences
- **Docker**: one command (`docker compose up`) for non-technical users, at
  the cost of Docker Desktop.
- **LAN sharing**: `streamlit run app.py --server.address 0.0.0.0`, then open
  `http://<host-ip>:8501` from another device on the same network.
- **Private tunnel**: exposes your running instance without hosting (e.g.
  Cloudflare Tunnel); different security and quota story from 11.1.

### 11.6 Terms and attribution
- Every user registers and owns their FIRMS key; do not bundle a key.
- Keep in-app attribution: NASA FIRMS, GDELT, GDACS, Open-Meteo (CC BY 4.0).
- Free tiers assume non-commercial use; users running it commercially must
  check each provider's terms.
- Keep the "not for emergency decisions" disclaimer visible for every user.
