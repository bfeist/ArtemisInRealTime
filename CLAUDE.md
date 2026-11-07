# Artemis in Real Time

This repository combines a React/TypeScript website for exploring Artemis mission
media and timelines with a Python pipeline that prepares the underlying assets.
Read the relevant code and pipeline README before changing a data contract.

## Python: always use uv

Always run Python through `uv`. Use `uv run python` for scripts and inline commands, and `uv run` for Python-based tools. Do not invoke `python`, `python3`, or Python-based tools directly.

- The Python project lives in `src/server-batch/`, with `pyproject.toml`, `uv.lock`,
  and `.python-version`. Run batch commands from that directory so uv selects the
  correct project and environment.
- Use `uv sync` to install the locked environment. Manage dependency changes with
  uv and keep `pyproject.toml` and `uv.lock` together.
- Some older documentation invokes `.venv/Scripts/python.exe` directly. Translate
  those examples to `uv run python`; the uv rule takes precedence.

## Repository map

- `src/index.tsx`: frontend entry point and route definitions.
- `src/pages/`: page components and their colocated CSS Modules. Many routes are
  exploratory prototypes with names such as `comm-test` and `mission-events-test`.
- `src/data/`: asset loaders, release resolution, and media synchronization logic.
- `src/types/`: shared frontend data contracts.
- `src/hooks/`: shared React hooks, including the playback clock.
- `src/styles/global.css`: global styling; `src/public/`: static site assets.
- `src/server/serveAsset.ts`: local asset serving, including gzip and byte ranges.
- `src/tests/`: Vitest and React Testing Library tests and setup.
- `src/server-batch/`: numbered ingestion/build stages, Python tests, and shared
  helpers. `config.py` defines mission settings and storage paths.
- `docs/`: frontend plans; `src/server-batch/docs/`: pipeline design and operations.
- `.local/`: ignored local/build output. Vite emits to `.local/vite/dist/`.

## Frontend commands

Run these from the repository root. Use npm and retain `package-lock.json`.

| Command | Purpose |
| --- | --- |
| `npm ci` | Install locked dependencies |
| `npm run dev` | Start Vite on port 8000 |
| `npm run lint` | Run ESLint and Stylelint |
| `npm run tsc` | Type-check without emitting files |
| `npm run build` | Build the site |
| `npm test` | Run Vitest once |
| `npm test -- src/tests/missionTimeline.test.ts` | Run a focused test file |
| `npm run test:all` | Run lint, type-check, build, and frontend tests |

For substantive frontend changes, run `npm run test:all` before handing off.
For documentation-only changes, review the diff and run `git diff --check`.
Report any checks that could not run and why.

## Code conventions

- Follow nearby code and the existing ESLint, Stylelint, and Prettier settings.
  Prettier uses two spaces, a 100-character print width, and LF line endings.
- Use TypeScript for frontend changes, explicit data types, and typed module
  boundaries. ESLint disallows explicit `any`.
- Use colocated `*.module.css` files for page styling, with camelCase class names;
  global CSS classes use kebab-case. Keep CSS Module references and selectors in sync.
- Reuse the existing loaders, shared helpers, and mission configuration instead of
  duplicating fetch logic, path construction, or mission constants in pages.
- Add or update focused regression tests when changing behavior. Frontend tests use
  Vitest with jsdom; batch tests use Python's `unittest`.
- Keep edits scoped to the task and preserve unrelated working-tree changes.

## Batch workflow and tests

From `src/server-batch/`:

```powershell
uv sync
uv run python run_all.py --mission artemis-ii
uv run python refresh_assets.py --mission artemis-ii --stage status
uv run python -m unittest test_web_timeline -v
uv run python -m unittest discover -p "test_*.py"
```

The default `run_all.py` group is `routine-build` (`1c`, `1i`): it prepares private
comm inputs and stages a release without downloads, transcription, HGX calls, or
uploads. Select the mission and stages explicitly for other work. Read
`src/server-batch/README.md` for step groups and stage prerequisites.

Use `refresh_assets.py` for routine staged updates. Metadata, photo details,
downloads, builds, status, and publication are separate operations. Run only the
stages needed for the task; model-backed enrichment and bulk retrieval can be
expensive. Prefer cached inputs and targeted tests when investigating a regression.

## Assets and configuration

- Large mission assets live outside this checkout. The default `DATA_DIR` is the
  sibling `ArtemisInRealTime_assets` directory. Each mission has
  `{mission}/raw/`, `{mission}/processed/`, and `{mission}/web/` trees.
- `BULK_ASSETS_DIR` defaults to `D:/ArtemisInRealTime_assets` for bulk originals;
  `PDS_ASSETS_DIR` can override PDS storage. Read `config.py` for current settings
  and mission path properties. Preserve originals and existing holdings on both drives.
- Vite serves `/artemis-assets` from the sibling assets directory and `/pds-assets`
  from the configured PDS raw tree. These local routes are configured in
  `vite.config.mts`; changing Python's `DATA_DIR` alone does not change the Vite mount.
- Missing local media may mean the external assets are absent. Check paths and
  configuration before changing frontend behavior or starting downloads.
- Keep credentials in the root `.env` using `.env.example` as the template. Do not
  commit credentials, downloaded media, generated datasets, virtual environments,
  or local build output.

## Data and publication contracts

- Mission timestamps are UTC. Preserve the distinction between planned times,
  observed event times, and report times; do not display a report timestamp as a
  confirmed execution time or infer a clock correction without evidence.
- Coordinate schema changes between Python exporters, `src/types/`, frontend
  loaders, and their tests. Keep private source catalogs and investigation data out
  of public presentation exports.
- Comm publication uses staged immutable releases; activation is a separate step.
  Preserve the release resolver's per-page pinning across timeline, crew, and audio
  requests. Only a 404 for `active-release.json` permits legacy-root fallback;
  malformed pointers and other HTTP failures must remain errors.
- Preserve path traversal checks, byte-range playback, gzip negotiation, and
  immutable-release caching when changing asset serving.
- Asset publication is separate from the application build. Consult
  `src/server-batch/docs/asset-publication.md` before changing publication behavior;
  only the mission web trees belong on production hosting.
- `.github/workflows/deploy.yml` runs on pushes to every branch. The `prod` branch
  deploys the production site; other branches deploy the development site.
