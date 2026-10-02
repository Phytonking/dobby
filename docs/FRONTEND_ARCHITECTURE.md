# Frontend Architecture

The frontend is the web dashboard for Dobby (a "UW Student Portal"). Pre-registered OAuth users and administrator-created local accounts sign in and keep
their calendar email current. Admins also manage the user allowlist (including everyone's calendar
email), guild settings, the audit log and Dobby's own service-account connections. It holds no data of its own. Everything it shows comes from the dashboard API
(`dashboard/`).

## Tech stack

| Concern       | Choice                                                                                  |
| ------------- | --------------------------------------------------------------------------------------- |
| Framework     | Next.js 15 (App Router), React 18, TypeScript; `output: 'standalone'`                   |
| UI components | shadcn/ui (`components.json`: style `default`, base color `zinc`, RSC on) on Radix UI   |
| Icons         | `lucide-react`                                                                          |
| Styling       | Tailwind CSS 3 with CSS variables (`globals.css`), `darkMode: ['class']`; `<html class="dark">` |
| Class helpers | `class-variance-authority`, `clsx` + `tailwind-merge` (`cn()` in `src/lib/utils.ts`)    |
| Data fetching | TanStack React Query v5 on the client; plain `fetch` in one server layout               |
| Font          | Inter via `next/font/google`                                                            |

## Directory layout

```
frontend/
├── Dockerfile, next.config.ts, components.json, tailwind.config.ts, postcss.config.js, tsconfig.json
└── src/
    ├── app/
    │   ├── layout.tsx            # root layout: metadata, Inter font, <Providers>
    │   ├── globals.css           # Tailwind layers + shadcn CSS variables
    │   ├── page.tsx              # "/" cookie check, then redirect
    │   ├── login/page.tsx
    │   └── dashboard/
    │       ├── layout.tsx        # server-side auth gate + sidebar
    │       ├── page.tsx, integrations/
    │       └── admin/{users,settings,audit}/
    ├── components/
    │   ├── providers.tsx         # QueryClientProvider
    │   ├── nav.tsx               # sidebar navigation
    │   └── ui/                   # shadcn primitives
    └── lib/
        ├── api.ts                # typed API client + API base URL
        ├── types.ts              # User, Integration, AuditEntry, Paginated
        └── utils.ts              # cn()
```

The `@/*` import alias maps to `./src/*`.

## Routes

| URL                          | Rendering | Shows                                                                                   | Access |
| ---------------------------- | --------- | --------------------------------------------------------------------------------------- | ------ |
| `/`                          | Server    | Nothing. Goes to `/dashboard` if a `dobby_session` cookie exists, otherwise to `/login`. | Anyone |
| `/login` | Client | Fetches `/auth/methods`; displays Google/Discord buttons, a local username/password form, or both | Anyone who can reach the configured API |
| `/dashboard`                 | Client    | Welcome line and a **Calendar email** card: edit your own address (`PATCH /me`)         | Signed in |
| `/dashboard/integrations`    | Client    | Dobby's service accounts: Google Calendar, Notion, Instagram, LinkedIn cards with Connect/Disconnect. Non-admins see an "admins only" notice | Admin |
| `/dashboard/admin/users`     | Client    | Allowlist table with calendar emails: add user (dialog), edit name/Discord ID/calendar email (dialog), promote/demote, remove | Admin* |
| `/dashboard/admin/audit`     | Client    | Audit log table with tool and status filters, 25 rows per page (limit/offset)           | Admin* |

\* The frontend's role check is cosmetic. `Nav` hides the Admin section unless `user.role === 'admin'`,
but the `admin/*` pages don't check the role themselves. The API enforces it.

## Layouts, providers, shared components

- **Root layout** (`app/layout.tsx`): sets the page title "Dobby | UW Student Portal", forces dark mode
  and wraps everything in `Providers`.
- **Providers** (`components/providers.tsx`): creates one `QueryClient` per browser session. Query
  defaults: `staleTime` 60 s; retry up to 2 times, but not when the error message contains `401`.
- **Dashboard layout** (`app/dashboard/layout.tsx`, server component): forwards all request cookies to
  `GET {API}/me` with `cache: 'no-store'`. A failed or non-OK response redirects to `/login`. On success
  it renders `Nav` (a fixed 240px sidebar) next to the page content.
- **Nav** (`components/nav.tsx`, client): shows the user's name, email or Discord ID and role badge. Holds
  the student links, the admin links (admins only) and a Sign out button, which calls `api.logout()` and
  then `router.push('/login')`.
- **UI primitives** (`components/ui/`): `badge`, `button`, `card`, `dialog`, `input`, `label`, `select`,
  `skeleton`, `table` (shadcn-style). Pages use `Skeleton` for loading states.

## Talking to the backend

- **Base URL:** `NEXT_PUBLIC_API_URL`, falling back to `http://localhost:8000`. It is set in
  `lib/api.ts` and repeated in `app/dashboard/layout.tsx`.
- **Client:** `request<T>()` in `lib/api.ts` wraps `fetch` with `credentials: 'include'` and a JSON
  `Content-Type`. On a non-OK response it throws `Error(detail)`, falling back to `Request failed: <status>`.
- **Endpoints used:**
  - `GET /auth/methods`, `POST /auth/local`
  - `GET /me`, `PATCH /me`, `POST /auth/logout` (204; client navigates to `/login`)
  - `GET /integrations`, `DELETE /integrations/{provider}` (admin)
  - `GET/POST /admin/users`, `PATCH /admin/users/{id}`, `DELETE /admin/users/{id}`, `PATCH /admin/users/{id}/role`
  - `GET /admin/audit?limit&offset&tool&status`
  - Paginated endpoints (`/admin/users`, `/admin/audit`) return `{total, items}`; `api.ts` unwraps `items`.
- **Browser redirects (not fetch):** `{API}/auth/google`, `{API}/auth/discord` (login) and
  `{API}/integrations/{provider}/connect` (OAuth connect).
- **Auth/session:** the frontend never handles tokens. The API sets a `dobby_session` cookie during either OAuth or local login,
  and the browser sends it back through `credentials: 'include'`. The server layout forwards it
  explicitly. Signing in only works if the API allows credentialed cross-origin requests from the
  frontend's origin.
- **Caching:** query keys include `['login-methods']`, `['me']`, `['integrations']` and
  `['admin', 'users' | 'settings' | 'audit', ...]`. Mutations invalidate the list they change.

## Login behavior

The login page reads server-side `AUTH_MODE` through `/auth/methods`; there is no separate frontend auth-mode variable. Local login posts `{username, password}` with credentials included, then navigates to `/dashboard`. The form uses username/current-password autocomplete, disables submit while pending, and displays API errors inline. API discovery failures show a connection error. Google login accepts verified emails from any domain; the UI labels the roster field **Google email**, while the API retains `uw_email`. Local credentials are created/reset by the server CLI, not the user-management form.

For LAN access, use the same server LAN IP/hostname in `DASHBOARD_URL` and `API_URL` (ports 3000/8000). The API address must be reachable from both the browser and the frontend container, since the dashboard server layout also calls `/me`. See [README setup](../README.md#step-5-dashboard-login).

## Config and environment variables

- `NEXT_PUBLIC_API_URL`: base URL of the dashboard API.
- `PORT`, `NODE_ENV`, `NEXT_TELEMETRY_DISABLED`: set in the Dockerfile.

Next.js bakes `NEXT_PUBLIC_*` values into client bundles at build time, so the Dockerfile takes
`NEXT_PUBLIC_API_URL` as a build `ARG` and the root `compose.yaml` passes `API_URL` to it both as a
build arg (browser code) and as a runtime variable (the server-side dashboard layout). Changing
`API_URL` therefore needs `docker compose build frontend`.

## Build and run

- **Local dev:** `npm install`, then `npm run dev` (serves at http://localhost:3000). Other scripts:
  `npm run build`, `npm start`, `npm run lint` (ESLint with `eslint-config-next`).
- **Docker:** a multi-stage `node:20-alpine` build (deps → builder → runner). It runs `npm ci`, then
  `next build`, and copies `.next/standalone` and `.next/static` into the final image. The server runs
  as a non-root `nextjs` user with `node server.js` on port 3000.
- **Compose:** the `frontend` service in the root `compose.yaml` builds `./frontend`, maps port
  `3000:3000` and depends on `dashboard`.
- `frontend/package-lock.json` is committed (`npm ci` needs it) and `frontend/public/` holds a
  `robots.txt` that disallows indexing, which also keeps the Dockerfile's `COPY public` step valid.
