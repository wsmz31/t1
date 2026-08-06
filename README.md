# t1 — Task Board

A small full-stack **Task Board** app used to demonstrate a working Cloud Agent
development environment end to end.

- **Frontend** — Vite + React + TypeScript (`client/`)
- **Backend** — Express REST API with a file-backed store (`server/`)
- **Tooling** — npm workspaces monorepo

## Requirements

- Node.js 22+
- npm 10+

## Getting started

```bash
npm install      # install all workspace dependencies
npm run dev      # start the API (:3001) and the web app (:5173) together
```

Then open http://localhost:5173. The Vite dev server proxies `/api/*` requests
to the Express backend, so the browser only ever talks to a single origin.

### Useful scripts

| Command | Description |
| --- | --- |
| `npm run dev` | Run backend and frontend dev servers concurrently |
| `npm run dev:server` | Run only the Express API (`:3001`) |
| `npm run dev:client` | Run only the Vite dev server (`:5173`) |
| `npm run build` | Type-check and build the frontend for production |
| `npm run lint` | Lint the frontend |
| `npm run start` | Run the API in production mode |

## API

Base URL: `http://localhost:3001`

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/tasks` | List tasks |
| `POST` | `/api/tasks` | Create a task (`{ "title": string }`) |
| `PATCH` | `/api/tasks/:id` | Update a task (`{ title?, done? }`) |
| `DELETE` | `/api/tasks/:id` | Delete a task |

Tasks are persisted to `server/data/tasks.json` (git-ignored) and seeded on
first run.

## Cloud Agent environment

The Cloud Agent environment is defined in
[`.cursor/environment.json`](.cursor/environment.json):

- `install` runs `npm install` to refresh workspace dependencies.
- Two `terminals` run the backend and frontend dev servers with visible logs.
- Ports `3001` (API) and `5173` (web) are exposed.
