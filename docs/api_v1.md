# GoHotel ERP API v1 — Documentation

Base URL: `http://localhost:8000/api/v1`

## Authentication

All endpoints (except `/auth/login` and `/health`) require:

```
Authorization: Bearer <access_token>
```

For `SUPER_ADMIN`, pass `hotel_id` as a query parameter to scope requests to a specific hotel. `ADMIN` and `EMPLOYEE` users have their `hotel_id` embedded in the JWT.

---

## Modules

### Auth

**POST /auth/login**

- Description: Unified login for all user types
- Body:

```json
{
  "username": "admin",
  "password": "admin123"
}
```

- Response 200:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 7200
}
```

- Errors: 401 Invalid credentials, 403 Account inactive

---

**POST /auth/refresh**

- Description: Refresh an expired or near-expiry access token
- Body:

```json
{
  "refresh_token": "eyJ..."
}
```

- Response 200:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 7200
}
```

- Errors: 401 Invalid/expired token

---

**POST /auth/logout**

- Auth: required
- Description: Invalidate the current session
- Response 200:

```json
{
  "message": "Logged out successfully"
}
```

---

**GET /auth/me**

- Auth: required
- Description: Return the currently authenticated user's profile
- Response 200:

```json
{
  "id": "uuid",
  "user_type": "SUPER_ADMIN",
  "hotel_id": null,
  "branch_id": null,
  "username": "admin",
  "first_name": "Super",
  "last_name": "Admin",
  "email": null,
  "phone": null,
  "status": "ACTIVE",
  "permissions": [],
  "work_hours_per_day": 8,
  "work_start": "09:00",
  "work_end": "18:00",
  "allow_outside_work_hours": false,
  "work_hours_enforced": false,
  "work_hours_blocked": false,
  "last_login_at": "2026-06-22T12:00:00Z"
}
```

- Work-hours fields (see [Work-hours enforcement](#work-hours-enforcement)):
  - `allow_outside_work_hours` — the per-user exemption set by an admin.
  - `work_hours_enforced` — the hotel setting; always `false` for `ADMIN`/`SUPER_ADMIN` and users without a hotel.
  - `work_hours_blocked` — `true` when any non-allowlisted request by this user would be rejected right now with 403 `OUTSIDE_WORK_HOURS`. Computed on the server clock (`APP_TZ_OFFSET_MINUTES`) with the same function as the gate, so clients should rely on it instead of the browser/device clock.
- `CONFIGURATOR` / `SUPER_ADMIN`: `hotel_id`, `hotel_name`, `branch_id` and `branch_name` are the hotel/branch chosen with `POST /auth/context` (taken from the token), not the user row.
- `ADMIN`: `branch_id` / `branch_name` are the branch chosen with `POST /auth/context` (default — the admin's own branch). `EMPLOYEE`: always the employee's own branch. `branch_name` is now filled for every role.

---

### Branch isolation

Every hotel row belongs to exactly one branch (`branch_id`), and every authenticated request works inside ONE branch:

- `EMPLOYEE` — always the branch on the user row (read on every request, so moving an employee takes effect immediately; a `branch_id` claim in the token is ignored).
- `ADMIN` — the branch in the token (`POST /auth/context`, own hotel only); default is the admin's own branch, then the main branch.
- `CONFIGURATOR` / `SUPER_ADMIN` with a chosen hotel — the chosen branch. `SUPER_ADMIN` without a hotel ("all hotels" mode, `?hotel_id=`) is not branch-scoped.

What it means for the API:

- **Guests are global** (`/guests/*`, blacklist, face profiles): one shared directory for every hotel and branch; `guests.hotel_id` / `branch_id` only record where the guest was first registered. A guest's history (`/guests/{id}/history`) covers all branches of the current hotel.
- All other list/detail endpoints return only the current branch's data: rooms, floors, room types (+ global types), hotel services, amenities, reservations, invoices, payments, penalties, shop (products, stock, sales), expenses, shifts and cash, housekeeping tasks, checklists, problems, feedback, messages, notifications, incoming calls, document scans, cameras/sightings, devices, audit log, reports. Another branch's record by id → 404.
- New records are written into the current branch automatically (`branch_id` in the body may be omitted). Writing a record into another branch, or moving an existing record to another branch, → 403 `BRANCH_SCOPE`. Exceptions: an `ADMIN` may assign an employee (`PUT /employees/{id}`, `branch_id`) to any branch of the own hotel (the employee then leaves this branch's list); cameras / camera PCs may be assigned to a branch in settings.
- `GET /employees/` — employees of the current branch. `GET /branches/` — `EMPLOYEE`: only the own branch; `ADMIN` / `CONFIGURATOR` / `SUPER_ADMIN`: all branches of the hotel.
- **Settings are per branch** (`branches.settings`): every `GET/PUT …-settings` endpoint reads/writes the current branch. A new branch starts with a copy of the main branch's settings.
- Reports (`/finance/*`, `/shifts/*`, `/reports/*`, dashboard data) are per branch.
- Notifications and pushes go to the current branch's staff plus the hotel's `ADMIN`s; the broadcast (`POST /notifications/broadcast`) goes to the current branch's staff and the admins. Cleaners are assigned only from the task's branch.
- `POST /maintenance/reset-data` resets only the current branch.
- Face recognition matches a camera's sighting only against guests of the camera's branch.

**New branch** (`POST /branches/`, panel `POST /superadmin/hotels/{id}/branches`): starts with copies of the main branch's settings and catalogs (hotel room types, enabled room types, amenities, service prices, checklist templates, chart of accounts, shop products without stock). A branch with no operational data (only those copies) can be deleted from the panel; a branch with guests, bookings, staff, etc. → 409 `BRANCH_NOT_EMPTY`.

---

### Guest face recognition: one index for the whole system

- Guest face templates (`guest_face_profiles`) are matched **across all hotels and branches**: a guest enrolled at one hotel is recognized by any camera of any other hotel or branch. `hotel_id`/`branch_id` on a profile only record where it was enrolled.
- `GET /vision/stats`: `profiles` / `guests_with_face` count the whole recognition pool; `enrolled_here` — templates enrolled at the current hotel.
- `DELETE /vision/guests/{id}/face` removes the guest's biometrics everywhere (consent withdrawal is global).

---

### Face login: only the enrolled face

- `POST /auth/login` → `face_required` for an employee with a face profile; `POST /auth/face/verify-login` compares the frame only with that employee's own profiles (cosine ≥ 0.40) — 401 `FACE_MISMATCH` otherwise.
- `POST /auth/login/no-camera` (`face_token`, `reason`): on a device **without a camera** (or when the server has no face engine — `503 FACE_ENGINE_UNAVAILABLE`) the password is enough. Clients call it only when no camera is present; with a camera the face step is mandatory and cannot be skipped. The reason is stored on the session.
- `POST /auth/face/enroll`: when profiles already exist, the new sample must match them (same person) — 422 `FACE_NOT_SAME_PERSON` otherwise; to replace the face, delete the profiles first.

---

### Superadmin panel: full control

**POST /superadmin/hotels/{id}/enter** — open the main app in a hotel/branch

- Auth: panel token. Body: `{"branch_id": "uuid | null"}` (default — the main branch; another hotel's branch → 422 `BRANCH_NOT_IN_HOTEL`).
- The panel user gets a hidden `CONFIGURATOR` account in `users` (username `panel__<id>`, unusable password, excluded from `/superadmin/configurators`). The response is a main-app token pair for that account with the chosen hotel/branch in the claims (as after `POST /auth/context`), plus the `/auth/me` profile: `{"access_token", "refresh_token", "user": {...}, "hotel": {"id", "name", "code"}}`. The panel writes them to the browser storage and opens the main app in a new tab — everything a configurator can do (settings, rooms, staff, permissions, cameras, shop…) becomes available without re-implementing it in the panel.
- Deactivating or deleting the panel user deactivates the hidden account and revokes its sessions (configurator tokens are checked against the session on every request, so they stop working immediately).

**PATCH /superadmin/staff/{id}/branch** — `{"branch_id"}`: move an employee to another branch of the same hotel (422 `BRANCH_NOT_IN_HOTEL`). Takes effect on the employee's next request. `GET /superadmin/hotels/{id}/users` items now carry `branch_id` and `branch_name`.

**GET /superadmin/system** — `{"app": {version, env, uptime_seconds, server_time, tz_offset_minutes}, "database": {ok, version, size, connections, migration, counts, open_shifts, live_sessions}, "storage": {ok, endpoint, buckets}, "push": {ok, project_id, panel_key_stored, …}, "scheduler": {auto_checkout_enabled, interval_seconds, grace_minutes, running}}`. Never fails as a whole: a component that is unreachable reports `ok: false` with `error`.

**POST /superadmin/broadcast** — `{"title", "body"?, "audience": "admins" | "staff", "hotel_id"?, "branch_id"?, "send_push": true}`. Without `hotel_id` — every active hotel; `branch_id` needs `hotel_id` (422 `HOTEL_REQUIRED`). `admins` → the hotel's `ADMIN`s; `staff` → the branch's active employees plus the admins. One notification (`entity_type = "announcement"`) per recipient, written inside the branch; an admin receives it once even with several branches. Response: `{"hotels", "recipients", "push"}`.

---

### Superadmin panel: app store — chunked upload

`POST /superadmin/apps` (multipart, whole file in one request) still works, but a request whose body takes longer than the proxy's read timeout (Traefik v3 default 60 s) is cut with 504 — a 93 MB APK needs ≥ 1.5 MB/s. The panel therefore uploads in chunks; every request is short, a failed chunk is retried, progress is reported.

**POST /superadmin/apps/uploads** — start

- Auth: panel token
- Body: `{"platform": "ANDROID|WINDOWS", "name", "version"?, "notes"?, "filename", "size", "content_type"?}` (422 `INVALID_PLATFORM`, `NAME_REQUIRED`, `EMPTY_FILE`, `FILE_TOO_LARGE` > 500 MB)
- Response 200: `{"upload_id", "chunk_size": 4194304, "total_chunks"}`

**PUT /superadmin/apps/uploads/{upload_id}/chunks/{index}** — one chunk

- Body: raw bytes (`Content-Type: application/octet-stream`), exactly `chunk_size` bytes except the last chunk. Re-sending an index overwrites it (safe to retry).
- Errors: 404 `UPLOAD_NOT_FOUND`, 422 `CHUNK_INDEX`, `CHUNK_SIZE`, `CHUNK_TOO_LARGE` (> 8 MB)
- Response 200: `{"index", "received", "total_chunks"}`

**GET /superadmin/apps/uploads/{upload_id}** — `{"received", "total_chunks", "missing": [indexes]}` (to resume)

**POST /superadmin/apps/uploads/{upload_id}/complete** — assembles the chunks, streams the file to MinIO (never fully in memory), creates the release and removes the temporary files. 409 `UPLOAD_INCOMPLETE` / `UPLOAD_SIZE_MISMATCH`. Response: the release (same shape as `GET /superadmin/apps` items).

**DELETE /superadmin/apps/uploads/{upload_id}** — abort. Unfinished uploads are also removed automatically after 6 hours.

---

### Superadmin panel: delete a hotel permanently

`DELETE /superadmin/hotels/{id}` only stops a hotel (`status = INACTIVE`, everything kept). Permanent deletion is a separate, two-step action:

**GET /superadmin/hotels/{id}/purge-preview**

- Auth: panel token. Changes nothing (the plan is computed in a transaction that is rolled back).
- Response 200: `{"hotel": {"id", "name", "code", "status"}, "tables": [{"table", "rows"}], "total_rows", "shared_guests": {"count", "hotels": [{"hotel_id", "hotel_name", "guests"}]}, "system_users": [{"id", "username", "user_type"}], "conflicts": [{"table", "column", "references", "rows", "on_delete", "blocking"}], "files", "can_purge", "blocked_by": ["HOTEL_ACTIVE" | "CROSS_HOTEL_REFERENCES"]}`

**POST /superadmin/hotels/{id}/purge**

- Auth: panel token of the **system owner** (`is_root`), else 403 `ROOT_ONLY`.
- Body: `{"confirm_code": "<hotel code, exact>", "password": "<the owner's panel password>"}` — wrong password 403 `WRONG_PASSWORD` (not 401, the panel session stays), code mismatch 422 `CONFIRM_CODE_MISMATCH`.
- 409 `HOTEL_ACTIVE` — stop the hotel first. 409 `CROSS_HOTEL_REFERENCES` — another hotel's rows point at this hotel's rows with a blocking rule (NO ACTION / RESTRICT / CASCADE); nothing is deleted.
- What is deleted: the `hotels` row and every row that belongs to it — tables with `hotel_id`, and tables without it that hang off those rows through foreign keys (found from the database catalog, so new tables are covered automatically). Global data is not touched: amenities, permissions, services, room types without a hotel, app releases, panel accounts.
- A guest who also has rows in another hotel (e.g. a booking) is not deleted: they are moved to the hotel that references them most. System accounts (`SUPER_ADMIN`, `CONFIGURATOR`) assigned to the hotel are not deleted either — `hotel_id` (and `branch_id` if it is this hotel's branch) is cleared. Non-blocking references from other rows (`SET NULL`) are cleared.
- Everything runs in one transaction (all or nothing). MinIO files of the hotel (attachment paths and the `{hotel_id}/` prefix in the documents and guests buckets) are removed after the commit; an object still referenced by another `file_attachments` row is kept.
- Response 200: `{"hotel", "deleted": {"<table>": rows}, "total_rows", "guests_moved", "guests_moved_to", "system_users_kept", "set_null", "files_removed", "files_failed", "seconds"}`

---

### Configurator (CONFIGURATOR)

A configurator is a hotel-less account (`users.user_type = "CONFIGURATOR"`, `hotel_id` NULL) created only in the superadmin panel (`/superadmin/configurators`). It signs in to the main app with `POST /auth/login`, chooses a hotel and branch, and in the chosen hotel works like a hotel `ADMIN` (`require_permission` bypass, admin-only actions). It is exempt from trusted-device approval and from the "hotel suspended" block (like `SUPER_ADMIN`).

**Settings are configurator-only.** Every settings write (`PUT /hotels/{nav,booking,discount,move-discount,work-hours}-settings`, `PUT /reservations/{edit-window,cancellation}-settings`, `PUT /guests/{scan,blacklist}-settings`, `PUT /shifts/settings`, `PUT /housekeeping/{assignment,auto-complete}-settings`, checklist template writes, `PUT /shop/receipt-settings`, vision device create/revoke and camera update, branch SMS key save/delete/test, `POST /maintenance/reset-data`) is allowed only to `CONFIGURATOR` and `SUPER_ADMIN`; everyone else (hotel `ADMIN` included) gets 403 `SETTINGS_CONFIGURATOR_ONLY`. Reading settings is unchanged.

**GET /auth/context/options**

- Auth: `CONFIGURATOR`, `SUPER_ADMIN` or `ADMIN` (others: 403 `CONTEXT_FORBIDDEN`). `ADMIN` gets only the own hotel with its branches.
- Response 200: `{"hotels": [{"id", "name", "code", "status", "branches": [{"id", "name", "code", "is_main", "status"}]}]}` — main branch first.

**POST /auth/context**

- Auth: `CONFIGURATOR`, `SUPER_ADMIN` or `ADMIN` (checked against the user row, not only the token). `ADMIN` may choose only a branch of the own hotel (another hotel → 403 `CONTEXT_FORBIDDEN`).
- Body: `{"hotel_id": "uuid", "branch_id": "uuid | null"}` — without `branch_id` the main (or first) branch is used. `SUPER_ADMIN` may send `hotel_id: null` to go back to "all hotels"; a configurator may not (422 `HOTEL_REQUIRED`).
- Response 200: a new token pair (same shape as `/auth/refresh`). The choice lives in the token claims and is kept by `/auth/refresh`; the current session is revoked.
- Errors: 403 `CONTEXT_FORBIDDEN`, 404 `HOTEL_NOT_FOUND`, 422 `BRANCH_NOT_IN_HOTEL`, 401 `USER_INACTIVE`

---

### Work-hours enforcement

Hotel setting `settings.work_hours.enforce` (default `false` — nothing changes until an admin turns it on). When it is on, every authenticated request by an `EMPLOYEE` is rejected with:

```json
{
  "detail": "Ish vaqtingiz emas (09:00–18:00). Ish vaqti boshlanganda tizimdan foydalanishingiz mumkin.",
  "error_code": "OUTSIDE_WORK_HOURS"
}
```

(status 403) when ALL of the following hold:

- the hotel exists and is `ACTIVE` (a blocked hotel keeps returning `HOTEL_*` instead);
- the user's `allow_outside_work_hours` is `false`;
- the current hotel-local time (UTC + `APP_TZ_OFFSET_MINUTES`) is outside `work_start`–`work_end` (start inclusive, end exclusive, schedules crossing midnight such as `22:00`–`06:00` are supported). A missing/invalid schedule or `start == end` means round-the-clock and is never blocked;
- the employee has no `ACTIVE` shift session in the hotel (a receptionist whose time ended keeps working with the open cash session — the existing "work ended" → "Davom etish" flow — and can still close the cash and hand over the shift);
- the path is not one of: `GET /auth/me`, `POST /auth/logout`, `POST /notifications/register-device`, `GET /hotels/work-hours-settings`, `GET /auth/face/status`, `/auth/webauthn/*` (passkey management).

`ADMIN` and `SUPER_ADMIN` are never blocked. Unauthenticated endpoints (login, `/auth/refresh`, face/passkey login) are not affected: an employee can log in outside hours, the client shows a waiting screen (`work_hours_blocked` in `/auth/me`) and recovers by itself when the working time starts. Implementation: `app/application/services/work_hours_access.py`, called at the end of `get_current_user`.

---

### Hotels

All endpoints in this section require the `SUPER_ADMIN` role. Use `?hotel_id=` for scoping.

**GET /hotels**

- Auth: SUPER_ADMIN
- Query: `?active_only=true`
- Response 200: Array of Hotel objects

```json
[
  {
    "id": "uuid",
    "name": "Test Hotel",
    "code": "TH001",
    "stars": 4,
    "phone": "+77771112233",
    "email": "info@test.kz",
    "address_line1": null,
    "city": "Astana",
    "country": "Kazakhstan",
    "status": "ACTIVE",
    "settings": {},
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /hotels**

- Auth: SUPER_ADMIN
- Description: Create a new hotel (auto-creates a "Main Branch")
- Body:

```json
{
  "name": "Test Hotel Astana",
  "code": "THA",
  "stars": 4,
  "phone": "+77771112233",
  "email": "info@testhotel.kz",
  "city": "Astana",
  "country": "Kazakhstan"
}
```

- Response 201: Hotel object
- Errors: 409 Hotel code already exists

---

**GET /hotels/{hotel_id}**

- Auth: SUPER_ADMIN
- Description: Get a single hotel by ID
- Response 200: Hotel object
- Errors: 404 Not found

---

**PUT /hotels/{hotel_id}**

- Auth: SUPER_ADMIN
- Description: Update hotel details (partial update)
- Body:

```json
{
  "stars": 5,
  "phone": "+77770000000"
}
```

- Response 200: Updated Hotel object
- Errors: 404 Not found

---

**PATCH /hotels/{hotel_id}/status**

- Auth: SUPER_ADMIN
- Description: Change hotel operational status
- Body:

```json
{
  "status": "ACTIVE"
}
```

- Valid statuses: `ACTIVE`, `SUSPENDED`, `CLOSED`
- Response 200: Updated Hotel object
- Errors: 404 Not found

---

**GET /hotels/work-hours-settings**

- Auth: any authenticated user (also open to an employee blocked by work hours)
- Query: `?hotel_id=` (SUPER_ADMIN only; others always get their own hotel)
- Description: Work-hours enforcement setting of the current hotel. Without a hotel context the default is returned.
- Response 200:

```json
{
  "enforce": false
}
```

---

**PUT /hotels/work-hours-settings**

- Auth: CONFIGURATOR / SUPER_ADMIN (others, hotel ADMIN included: 403 `SETTINGS_CONFIGURATOR_ONLY`)
- Query: `?hotel_id=` (SUPER_ADMIN only)
- Description: Turn work-hours enforcement on/off (stored in `hotel.settings.work_hours`; other settings keys are untouched).
- Body:

```json
{
  "enforce": true
}
```

- Response 200: `{"enforce": true}`
- Errors: 403 `FORBIDDEN`, 403 Hotel context required, 404 `HOTEL_NOT_FOUND`

---

**GET /hotels/move-discount-settings**

- Auth: any authenticated user (the room-change panel shows the limit to staff)
- Description: Whether staff may give a discount when a guest moves to a MORE EXPENSIVE room, and the maximum. Without a hotel context (or never saved) the default is returned — switched OFF.
- Response 200:

```json
{
  "enabled": false,
  "max_percent": 0,
  "max_amount": 0
}
```

- `max_percent` — of the price difference (0 = no limit, i.e. up to the whole difference); `max_amount` — so'm per move (0 = no limit). Both apply together.

---

**PUT /hotels/move-discount-settings**

- Auth: CONFIGURATOR / SUPER_ADMIN (others, hotel ADMIN included: 403 `SETTINGS_CONFIGURATOR_ONLY`)
- Description: Save the setting (stored in `hotel.settings.room_move_discount`; other settings keys are untouched).
- Body: `{"enabled": true, "max_percent": 50, "max_amount": 0}` (`max_percent` 0–100, `max_amount` ≥ 0)
- Response 200: the saved setting
- Errors: 403 `FORBIDDEN`, 403 Hotel context required, 404 `HOTEL_NOT_FOUND`, 422 validation

---

### Branches

**GET /branches**

- Auth: required
- Query: `?hotel_id=` (optional for SUPER_ADMIN; auto-scoped for ADMIN/EMPLOYEE)
- Response 200: Array of Branch objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "name": "Main Branch",
    "code": "THA-MAIN",
    "is_main_branch": true,
    "status": "ACTIVE",
    "address_line1": null,
    "city": null,
    "phone": null,
    "email": null,
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /branches**

- Auth: require_permission("branches.create")
- Description: Create a new branch under a hotel
- Body:

```json
{
  "name": "Branch 2",
  "code": "THA-B2",
  "city": "Almaty"
}
```

- Response 201: Branch object
- Errors: 409 Branch code already exists

---

**GET /branches/{branch_id}**

- Auth: required
- Description: Get a single branch by ID
- Response 200: Branch object
- Errors: 404 Branch not found

---

**PUT /branches/{branch_id}**

- Auth: require_permission("branches.update")
- Description: Update branch details (partial update)
- Body:

```json
{
  "name": "Updated Branch",
  "phone": "+77771112233"
}
```

- Response 200: Updated Branch object
- Errors: 404 Not found

---

**GET /branches/{branch_id}/floors**

- Auth: required
- Description: List all floors belonging to a branch
- Response 200: Array of Floor objects for the given branch

---

**GET /branches/{branch_id}/sms** · **PUT** · **DELETE**

- Auth: require_permission("branch.update")
- Description: Branch SMS (Xabarchi) API key. The key is stored encrypted
  and never returned — only a masked hint.
- PUT body: `{"api_key": "xab_live_..."}`
- Response 200: `{"configured": true, "key_hint": "xab_live_7…ABCD"}`

**POST /branches/{branch_id}/sms/test**

- Auth: require_permission("branch.update")
- Body: `{"phone": "+998 90 123 45 67"}`
- Description: Sends a test SMS with the saved key and returns the result
  immediately.
- Response 200: `{"ok": true, "phone": "+998901234567", "status": "queued", "message_id": 42}`
  (`queued` — accepted by Xabarchi; the paired phone sends it)
- Errors: 422 `SMS_KEY_NOT_SET`, `BAD_PHONE`, `SMS_SEND_FAILED` (detail
  carries the reason: wrong key, monthly quota, misconfigured `SMS_API_BASE` …)

---

### Floors

**GET /floors**

- Auth: required
- Query: `?branch_id=` (optional)
- Response 200: Array of Floor objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "branch_id": "uuid",
    "floor_number": 1,
    "name": "First Floor",
    "created_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /floors**

- Auth: require_permission("floors.create")
- Description: Add a floor to a branch
- Body:

```json
{
  "branch_id": "uuid",
  "floor_number": 1,
  "name": "First Floor"
}
```

- Response 201: Floor object

---

**PUT /floors/{floor_id}**

- Auth: require_permission("floors.update")
- Description: Update floor details
- Body:

```json
{
  "floor_number": 2,
  "name": "Ground Floor"
}
```

- Response 200: Updated Floor object
- Errors: 404 Not found

---

**DELETE /floors/{floor_id}**

- Auth: require_permission("floors.delete")
- Description: Delete a floor (must have no rooms assigned)
- Response 200:

```json
{
  "message": "Floor deleted"
}
```

- Errors: 404 Not found, 409 Floor has rooms assigned

---

### Room Types

**GET /room-types**

- Auth: required
- Query: `?active_only=true`
- Response 200: Array of RoomType objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "name": "Deluxe Room",
    "description": null,
    "capacity": 2,
    "base_price": 35000.0,
    "amenities": ["wifi", "tv"],
    "is_active": true,
    "created_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /room-types**

- Auth: require_permission("room.manage")
- Description: Create a new room type
- Body:

```json
{
  "name": "Deluxe Room",
  "base_price": 35000,
  "capacity": 2,
  "amenities": ["wifi", "tv"]
}
```

- Response 201: RoomType object

---

**GET /room-types/{type_id}**

- Auth: required
- Description: Get a single room type by ID
- Response 200: RoomType object
- Errors: 404 Not found

---

**PUT /room-types/{type_id}**

- Auth: require_permission("room.manage")
- Description: Update room type details (partial update)
- Body:

```json
{
  "base_price": 40000,
  "is_active": true
}
```

- Response 200: Updated RoomType object
- Errors: 404 Not found

---

**PATCH /room-types/{type_id}/status**

- Auth: require_permission("room.manage")
- Description: Toggle room type active status
- Query: `?is_active=true` or `?is_active=false`
- Response 200: Updated RoomType object
- Errors: 404 Not found

---

### Rooms

**GET /rooms**

- Auth: required
- Query: `?branch_id=&floor_id=&room_type_id=&status=`
- Response 200: Array of Room objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "branch_id": "uuid",
    "floor_id": "uuid",
    "room_type_id": "uuid",
    "room_number": "101",
    "current_status": "AVAILABLE",
    "notes": null,
    "is_deleted": false,
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**GET /rooms/available**

- Auth: required
- Query: `?check_in=2026-06-22&check_out=2026-06-25&branch_id=&room_type_id=`
- Description: Find rooms available for the given date range
- Response 200: Array of available Room objects

---

**POST /rooms**

- Auth: require_permission("room.manage")
- Description: Add a room to a branch/floor
- Body:

```json
{
  "branch_id": "uuid",
  "floor_id": "uuid",
  "room_type_id": "uuid",
  "room_number": "101",
  "notes": "Corner room"
}
```

- Response 201: Room object
- Errors: 409 Room number already exists in branch

---

**GET /rooms/{room_id}**

- Auth: required
- Description: Get a single room by ID
- Response 200: Room object
- Errors: 404 Room not found

---

**PUT /rooms/{room_id}**

- Auth: require_permission("room.manage")
- Description: Update room details (partial update)
- Body:

```json
{
  "floor_id": "uuid",
  "room_type_id": "uuid",
  "notes": "Updated"
}
```

- Response 200: Updated Room object
- Errors: 404 Not found

---

**PATCH /rooms/{room_id}/status**

- Auth: require_permission("room.status.update")
- Description: Manually change room operational status
- Body:

```json
{
  "status": "CLEANING",
  "notes": "Post checkout"
}
```

- Valid statuses: `AVAILABLE`, `RESERVED`, `OCCUPIED`, `CLEANING`, `MAINTENANCE`, `INSPECTION`, `OUT_OF_SERVICE`
- Side effects: Room status history entry is created
- Response 200: Updated Room object
- Errors: 404 Not found

---

**GET /rooms/{room_id}/status-history**

- Auth: required
- Query: `?limit=50`
- Description: View status change history for a room
- Response 200: Array of status history entries

```json
[
  {
    "id": "uuid",
    "room_id": "uuid",
    "status": "CLEANING",
    "changed_by": "uuid",
    "notes": "Post checkout",
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**DELETE /rooms/{room_id}**

- Auth: require_permission("room.manage")
- Description: Soft delete a room (marked as deleted, preserved in DB)
- Response 200:

```json
{
  "message": "Room deleted"
}
```

- Errors: 404 Not found

---

### Guests

**GET /guests**

- Auth: required
- Query: `?query=` (search by name/phone/passport), `?page=&page_size=`
- Response 200: Array of Guest objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "first_name": "Nursultan",
    "last_name": "Testov",
    "phone": "+77011234567",
    "email": "guest@test.kz",
    "passport_number": "N12345678",
    "nationality": "KZ",
    "birth_date": "1990-01-15",
    "id_document_type": null,
    "id_document_number": null,
    "address": null,
    "notes": null,
    "is_deleted": false,
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /guests**

- Auth: require_permission("guest.create")
- Description: Register a new guest profile
- Body:

```json
{
  "first_name": "Nursultan",
  "last_name": "Testov",
  "phone": "+77011234567",
  "email": "guest@test.kz",
  "passport_number": "N12345678",
  "nationality": "KZ",
  "birth_date": "1990-01-15"
}
```

- Response 201: Guest object

---

**GET /guests/{guest_id}**

- Auth: required
- Description: Get a single guest by ID
- Response 200: Guest object
- Errors: 404 Guest not found

---

**PUT /guests/{guest_id}**

- Auth: require_permission("guest.update")
- Description: Update guest details (partial update)
- Body:

```json
{
  "phone": "+77019876543",
  "email": "new@test.kz"
}
```

- Response 200: Updated Guest object
- Errors: 404 Not found

---

**GET /guests/{guest_id}/reservations**

- Auth: required
- Description: List all reservations for a guest
- Response 200: Array of Reservation objects for this guest
- Errors: 404 Guest not found

---

**POST /guests/{guest_id}/document-images**

- Auth: ADMIN/SUPER_ADMIN or any of `guest.create`, `guest.update`, `reservation.create`, `reservation.update`, `file.upload`
- Description: Save the scanned passport / ID card image(s) of a guest to MinIO (bucket `MINIO_BUCKET_GUESTS`). The web scanner calls it after the guest is created or picked. Multipart fields: `passport`, `front`, `back` (each optional, at least one; JPEG/PNG/WEBP, max 12 MB), `document_type` (`ID_CARD` | `PASSPORT`, optional).
- Response 200: `{"stored": [{"id", "category": "document_front", "side": "front", "mime_type", "file_size", "created_at", "uploaded_by_name"}], "disabled": false}`; when the scanner setting `store_images` is off: `{"stored": [], "disabled": true}`
- Errors: 404 `GUEST_NOT_FOUND`, 403 `DOCUMENT_IMAGES_FORBIDDEN`, 422 `DOCUMENT_IMAGE_INVALID` / `DOCUMENT_IMAGE_REQUIRED` / `IMAGE_TOO_LARGE`, 503 `DOCUMENT_IMAGE_STORAGE_FAILED`

**GET /guests/{guest_id}/document-images**

- Auth: ADMIN/SUPER_ADMIN or any of `guest.view`, `guest.create`, `guest.update`, `reservation.read`, `reservation.create`, `reservation.update`
- Description: The guest's document images saved IN THIS HOTEL (scanner images `document_passport|front|back` and the manual "passport photo" upload `photo`), newest first.

**GET /guests/{guest_id}/document-images/{file_id}**

- Auth: as above
- Description: The image bytes, streamed through the API (`Cache-Control: private, no-store`). Errors: 404 `FILE_NOT_FOUND`.

---

**DELETE /guests/{guest_id}**

- Auth: require_permission("guest.create")
- Description: Soft delete a guest (marked as deleted, preserved in DB)
- Response 200:

```json
{
  "message": "Guest deleted"
}
```

- Errors: 404 Not found

---

### Reservations

**GET /reservations**

- Auth: required
- Query: `?status=&branch_id=&date_from=&date_to=&page=&page_size=`
- Response 200: Array of Reservation objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "branch_id": "uuid",
    "reservation_number": "RES-THA070705-20260622-A3F9",
    "guest_id": "uuid",
    "room_id": "uuid",
    "check_in_date": "2026-06-23",
    "check_out_date": "2026-06-25",
    "adults": 2,
    "children": 0,
    "status": "CONFIRMED",
    "discount_amount": 0,
    "discount_percent": 0,
    "notes": null,
    "cancelled_reason": null,
    "cancelled_at": null,
    "created_by": "uuid",
    "created_at": "2026-06-22T12:00:00Z",
    "updated_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**POST /reservations**

- Auth: require_permission("reservation.create")
- Description: Create a new reservation
- Body:

```json
{
  "guest_id": "uuid",
  "room_id": "uuid",
  "branch_id": "uuid",
  "check_in_date": "2026-06-23",
  "check_out_date": "2026-06-25",
  "adults": 2,
  "children": 0,
  "discount_amount": 0,
  "notes": "Late arrival"
}
```

- Response 201: Reservation object
- Errors: 409 Room not available for the requested date range

---

**GET /reservations/calendar**

- Auth: required
- Query: `?view=daily|weekly|monthly&date=2026-06-22&branch_id=&room_type_id=`
- Description: Retrieve reservations for a calendar view
- Response 200: Array of Reservation objects for the calendar period

---

**GET /reservations/availability**

- Auth: required
- Query: `?check_in=2026-06-23&check_out=2026-06-25&branch_id=&room_type_id=&adults=2`
- Description: Check room availability for a date range
- Response 200: Array of available Room objects

---

**GET /reservations/{reservation_id}**

- Auth: required
- Description: Get a single reservation by ID
- Response 200: Reservation object
- Errors: 404 Reservation not found

---

**PUT /reservations/{reservation_id}**

- Auth: require_permission("reservation.update")
- Description: Update reservation details (partial update)
- Body:

```json
{
  "room_id": "uuid",
  "check_in_date": "2026-06-24",
  "notes": "Updated"
}
```

- Response 200: Updated Reservation object
- Errors: 404 Not found, 409 Room not available for new date range

---

**POST /reservations/{reservation_id}/check-in**

- Auth: require_permission("guest.checkin")
- Description: Process guest check-in
- Body: None
- Response 200: Updated Reservation object (status → `CHECKED_IN`)
- Side effects:
  - Room status → `OCCUPIED`
  - Room status history entry created
- Errors:
  - 404 Reservation not found
  - 409 Reservation not in `CONFIRMED` status

---

**Hamrohlar — turish davomida (companions)**

Mehmon kirib ketgach xonadagilar o'zgarishi mumkin: hamroh ketadi, o'rniga
boshqasi keladi. Hamrohlar `Reservation.companions` JSONB ro'yxatida saqlanadi;
yozuv hech qachon o'chirilmaydi (faqat kirishdan oldin), ketgani `left_at`
bilan belgilanadi — mehmon tarixi va kamera moslashuvi saqlanadi. Qoida:
ichkaridagilar (asosiy mehmon + `left_at` bo'lmagan hamrohlar) `adults` dan
oshmaydi. Bu amallar shartnomani (sana, narx, mehmonlar soni) o'zgartirmaydi,
shu sababli tahrir oynasi bilan cheklanmaydi. Hamma javob — yangilangan
Reservation object. SUPER_ADMIN `hotel_id` query bermasa bronning o'zidan
olinadi. Qoidalar: `app/application/services/companion_ops.py`.

Yozuv shakli:

```json
{
  "guest_id": "uuid", "name": "Ali Valiyev",
  "added_at": "ISO", "added_by": "user uuid",
  "left_at": "ISO", "left_by": "user uuid",
  "returned_at": "ISO", "returned_by": "user uuid"
}
```

`added_*` — turish davomida qo'shilgan; `left_*` — xonadan ketgan;
`returned_*` — ketib, yana qaytgan. Eski yozuvlarda faqat `guest_id`, `name`.

**POST /reservations/{reservation_id}/companions**

- Auth: require_permission("reservation.update")
- Description: Xonaga yangi hamroh — ketgan o'rniga kelgan yoki bo'sh joyga.
  Mehmon oldin `POST /guests` bilan yaratiladi. Ilgari ketgan mehmon qaytsa
  yangi yozuv ochilmaydi: `left_*` tushadi, `returned_at/by` yoziladi.
- Body: `{"guest_id": "uuid"}`
- Response 200: Reservation object
- Errors:
  - 404 RESERVATION_NOT_FOUND, COMPANION_NOT_FOUND (mehmon bazada yo'q)
  - 422 INVALID_STATUS (faqat `CONFIRMED` yoki `CHECKED_IN`)
  - 422 CHECKOUT_IN_PROGRESS (chiqish jarayoni boshlangan)
  - 422 COMPANION_IS_MAIN_GUEST, COMPANION_ALREADY_PRESENT
  - 422 ROOM_GUESTS_FULL (ichkaridagilar + 1 > adults)

**POST /reservations/{reservation_id}/companions/{guest_id}/leave**

- Auth: require_permission("reservation.update")
- Description: Hamroh xonadan ketdi (`CHECKED_IN`). Yozuv qoladi, `left_at`,
  `left_by` qo'yiladi; bo'shagan joyga yangi hamroh qo'shish mumkin.
- Body: None
- Response 200: Reservation object
- Errors:
  - 404 COMPANION_NOT_FOUND
  - 422 INVALID_STATUS, COMPANION_ALREADY_LEFT

**POST /reservations/{reservation_id}/companions/{guest_id}/return**

- Auth: require_permission("reservation.update")
- Description: "Ketdi" belgisini bekor qilish — adashib bosilgan bo'lsa
  (`CHECKED_IN`). Joy tekshiriladi: o'rniga boshqasi kirgan bo'lsa rad etiladi.
- Response 200: Reservation object
- Errors:
  - 404 COMPANION_NOT_FOUND
  - 422 INVALID_STATUS, COMPANION_NOT_LEFT, ROOM_GUESTS_FULL

**DELETE /reservations/{reservation_id}/companions/{guest_id}**

- Auth: require_permission("reservation.update")
- Description: Hamrohni ro'yxatdan olib tashlash — faqat kirishdan OLDIN
  (`CONFIRMED`). Kirgan bronda `leave` ishlatiladi: tarix saqlanadi.
- Response 200: Reservation object
- Errors:
  - 404 COMPANION_NOT_FOUND
  - 422 INVALID_STATUS

`GET /rooms/{room_id}/reservations` javobidagi `occupants[]` yozuvlarida ham
`added_at`, `left_at`, `is_present` bor (asosiy mehmonda doim `true`).

---

**POST /reservations/{reservation_id}/check-out**

- Auth: require_permission("guest.checkout")
- Description: Process guest check-out
- Body: None
- Response 200: Updated Reservation object (status → `CHECKED_OUT`)
- Side effects:
  - Invoice auto-generated with room charges
  - Room status → `CLEANING`
  - Housekeeping `CLEANING` task auto-created
  - Room status history entry created
- Errors: 409 Reservation not in `CHECKED_IN` status

---

**POST /reservations/{reservation_id}/cancel**

- Auth: require_permission("reservation.cancel")
- Description: Cancel a reservation
- Body:

```json
{
  "reason": "Guest requested cancellation"
}
```

- Response 200: Cancelled Reservation object
- Side effects: Room status → `AVAILABLE`, room status history entry created

---

**POST /reservations/{reservation_id}/no-show**

- Auth: require_permission("reservation.update")
- Description: Mark a reservation as no-show
- Body: None
- Response 200: Reservation with status `NO_SHOW`
- Side effects: Room status → `AVAILABLE`

---

**GET /reservations/{reservation_id}/services**

- Auth: required
- Description: List additional services added to a reservation
- Response 200: Array of service entries

```json
[
  {
    "id": "uuid",
    "service_id": "uuid",
    "service_name": "Breakfast",
    "service_code": "BREAKFAST",
    "quantity": 2,
    "unit_price": 5000,
    "total_price": 10000,
    "service_date": "2026-06-23",
    "notes": null
  }
]
```

---

**POST /reservations/{reservation_id}/services**

- Auth: require_permission("reservation.update")
- Description: Add a service to a reservation
- Body:

```json
{
  "hotel_service_id": "uuid",
  "quantity": 2,
  "service_date": "2026-06-23",
  "notes": "Extra request"
}
```

- Response 201: Service entry added

---

**DELETE /reservations/{reservation_id}/services/{service_id}**

- Auth: require_permission("reservation.update")
- Description: Remove a service from a reservation
- Response 200:

```json
{
  "message": "Service removed"
}
```

---

**POST /reservations/{reservation_id}/move-room**

- Auth: require_permission("reservation.update"); staff only within the edit window (`/reservations/edit-window-settings`), ADMIN/SUPER_ADMIN any time
- Description: Move an active booking (PENDING / CONFIRMED / CHECKED_IN) to another free room; the price is recalculated (CHECKED_IN: nights already stayed keep the old price). Every move is appended to `room_moves`.
- Body:

```json
{
  "new_room_id": "uuid",
  "discount_amount": 50000
}
```

- `discount_amount` (optional, so'm) — discount off the price difference when moving to a MORE EXPENSIVE room. Omitted or 0 — no discount (previous behaviour). Staff: only if `move-discount-settings.enabled` and within `max_percent` / `max_amount`; ADMIN/SUPER_ADMIN: any amount up to the difference. The discount accumulates in `reservations.move_discount_amount`, is subtracted again at check-out / invoice creation and is added to `invoice.discount_amount`. Moving back to a cheaper room reduces the earlier move discount first. "Difference" is what the guest actually pays extra: with a percentage booking discount it is reduced by that percentage. On every recalculation the move discount is clamped to the difference between the current room and the room the discount started from (`discount_baseline_price`), so shortening the stay never lets the guest pay below that room's price.
- Response 200: Reservation (`move_discount_amount`; the new `room_moves` entry also has `price_increase`, `discount_amount`, `move_discount_total`, `discount_baseline_price`)
- Errors: 422 `MOVE_DISCOUNT_DISABLED`, `MOVE_DISCOUNT_NOT_UPGRADE`, `MOVE_DISCOUNT_MAX_PERCENT`, `MOVE_DISCOUNT_MAX_AMOUNT`, `MOVE_DISCOUNT_EXCEEDS_DIFFERENCE`, `SAME_ROOM`, `INVALID_STATUS`; 403 `EDIT_WINDOW_EXPIRED`; 409 `ROOM_ALREADY_BOOKED`, `ROOM_NOT_AVAILABLE`

---

**GET /reservations/{reservation_id}/penalties**

- Auth: required
- Description: Penalties of a booking (late checkout, damage, other) — voided ones included with `voided_at`. `total` is the sum of active penalties (already part of `total_amount`). `late_suggestion` is a server-side late-checkout amount when `penalty-settings.late_hourly_amount` > 0 and the guest is late beyond `grace_minutes` (CHECKED_IN: until now; CHECKED_OUT: until `checkout_requested_at`), otherwise `null`.
- Response 200:

```json
{
  "items": [
    {"id": "uuid", "reservation_id": "uuid", "kind": "DAMAGE", "amount": 150000.0, "note": "Stakan sindi",
     "penalty_date": "2026-10-02", "created_by": "uuid", "created_by_name": "Dilnoza R",
     "created_at": "2026-10-02T09:10:00+00:00", "voided_at": null, "voided_by_name": null, "void_reason": null}
  ],
  "total": 150000.0,
  "can_add": true,
  "late_suggestion": {"late_minutes": 130, "hours": 3, "hourly_amount": 50000.0, "amount": 150000.0, "due_at": "2026-10-02T07:00:00+00:00"}
}
```

---

**POST /reservations/{reservation_id}/penalties**

- Auth: require_permission("reservation.update")
- Description: Add a penalty. Allowed only for CHECKED_IN and CHECKED_OUT bookings (a guest who already left can still be charged for damage found later). The amount is added to `reservations.penalty_amount` and `total_amount`, `payment_status` is recalculated, and a `PENALTY` line is added to the booking invoice (if it exists; otherwise it is added when the invoice is created / at check-out). Payment is taken through the usual `settle-payment` / invoice payment flow.
- Body: `{"kind": "LATE_CHECKOUT" | "DAMAGE" | "OTHER", "amount": 150000, "note": "optional, max 500"}`
- Response 200: `{"penalty": {...}, "reservation_total": 750000.0, "penalty_total": 150000.0, "payment_status": "PARTIALLY_PAID"}`
- Errors: 422 `PENALTY_INVALID_STATUS`, `PENALTY_INVALID_KIND`, `PENALTY_INVALID_AMOUNT`; 404 `RESERVATION_NOT_FOUND`

---

**POST /reservations/{reservation_id}/penalties/{penalty_id}/void**

- Auth: ADMIN / SUPER_ADMIN or permission `shift.force_close` (manager)
- Description: Void a penalty (never deleted — kept with `voided_at`, `voided_by`, `void_reason`). The amount is taken back out of the booking total and the invoice, its `PENALTY` invoice line is removed.
- Body: `{"reason": "optional"}`
- Response 200: same shape as add
- Errors: 403 `PENALTY_VOID_FORBIDDEN`; 404 `PENALTY_NOT_FOUND`; 422 `PENALTY_ALREADY_VOIDED`

---

### Employees

**GET /employees**

- Auth: required
- Query: `?status=ACTIVE`
- Response 200: Array of Employee (User) objects

```json
[
  {
    "id": "uuid",
    "user_type": "EMPLOYEE",
    "hotel_id": "uuid",
    "branch_id": "uuid",
    "username": "reception1",
    "first_name": "Aidar",
    "last_name": "Manager",
    "email": "aidar@test.kz",
    "phone": null,
    "status": "ACTIVE",
    "hire_date": "2026-01-01",
    "work_hours_per_day": 8,
    "work_start": "09:00",
    "work_end": "18:00",
    "allow_outside_work_hours": false,
    "termination_date": null,
    "is_deleted": false,
    "last_login_at": null,
    "created_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /employees**

- Auth: require_permission("employee.create")
- Description: Create a new employee account
- Body:

```json
{
  "hotel_id": "uuid",
  "branch_id": "uuid",
  "username": "reception1",
  "password": "pass123",
  "first_name": "Aidar",
  "last_name": "Manager",
  "email": "aidar@test.kz",
  "phone": "+77001112233",
  "hire_date": "2026-01-01",
  "work_start": "09:00",
  "work_end": "18:00",
  "allow_outside_work_hours": false
}
```

- `allow_outside_work_hours` (optional): the employee is not blocked by [work-hours enforcement](#work-hours-enforcement). Only ADMIN/SUPER_ADMIN may send `true`; anyone else gets 403 `FORBIDDEN`.
- Response 201: Employee object
- Errors: 409 Username already exists, 403 `FORBIDDEN`

---

**GET /employees/{employee_id}**

- Auth: required
- Description: Get a single employee by ID
- Response 200: Employee object
- Errors: 404 Employee not found

---

**PUT /employees/{employee_id}**

- Auth: require_permission("employee.update")
- Description: Update employee details (partial update)
- Body:

```json
{
  "first_name": "Aidar Updated",
  "status": "ACTIVE",
  "allow_outside_work_hours": true
}
```

- `allow_outside_work_hours` (optional): only ADMIN/SUPER_ADMIN may change it. A non-admin may send it only with the current value (e.g. when the edit form posts the whole object); a different value gives 403 `FORBIDDEN`. `username`/`password` are admin-only as well.
- Response 200: Updated Employee object
- Errors: 404 Not found, 403 `FORBIDDEN`

---

**DELETE /employees/{employee_id}**

- Auth: require_permission("employee.update")
- Description: Soft delete an employee (marked as deleted, preserved in DB)
- Response 200:

```json
{
  "message": "Employee deleted"
}
```

- Errors: 404 Not found

---

### Permissions

**GET /permissions**

- Auth: required
- Description: List all available permission codes in the system
- Response 200: Array of Permission objects

```json
[
  {
    "id": "uuid",
    "code": "reservation.create",
    "name": "Create Reservation",
    "module": "reservation",
    "description": "Create new reservations"
  }
]
```

---

**GET /permissions/modules**

- Auth: required
- Description: List permissions grouped by module
- Response 200:

```json
[
  {
    "module": "reservation",
    "permissions": [
      {
        "id": "uuid",
        "code": "reservation.create",
        "name": "Create Reservation"
      }
    ]
  }
]
```

---

**GET /permissions/{employee_id}/permissions**

- Auth: required
- Description: Get permissions assigned to a specific employee
- Response 200:

```json
{
  "user_id": "uuid",
  "permissions": [
    {
      "id": "uuid",
      "code": "reservation.create",
      "name": "Create Reservation",
      "module": "reservation"
    }
  ]
}
```

- Errors: 404 Employee not found

---

**PUT /permissions/{employee_id}/permissions**

- Auth: require_permission("employee.manage")
- Description: Bulk replace all permissions for an employee
- Body:

```json
{
  "permission_ids": ["uuid1", "uuid2"]
}
```

- Response 200: Updated permissions list
- Note: Replaces ALL existing permissions with the new list

---

**POST /permissions/{employee_id}/permissions/{perm_id}**

- Auth: require_permission("employee.manage")
- Description: Grant a single permission to an employee
- Response 200: Permission granted

---

**DELETE /permissions/{employee_id}/permissions/{perm_id}**

- Auth: require_permission("employee.manage")
- Description: Revoke a single permission from an employee
- Response 200:

```json
{
  "message": "Permission revoked"
}
```

---

### Services (Global Catalog)

**GET /services**

- Auth: required
- Description: List all globally defined services available to hotels
- Response 200: Array of Service objects

```json
[
  {
    "id": "uuid",
    "name": "Breakfast",
    "code": "BREAKFAST",
    "description": "Continental breakfast",
    "category": "Food",
    "is_active": true,
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /services**

- Auth: SUPER_ADMIN only
- Description: Add a new service to the global catalog
- Body:

```json
{
  "name": "Airport Transfer",
  "code": "TRANSFER",
  "description": "Airport pick-up/drop-off",
  "category": "Transport"
}
```

- Response 201: Service object

---

**PUT /services/{service_id}**

- Auth: SUPER_ADMIN only
- Description: Update a global service definition (partial update)
- Body:

```json
{
  "name": "Updated Name",
  "is_active": true
}
```

- Response 200: Updated Service object
- Errors: 404 Not found

---

### Hotel Services

**GET /hotel-services**

- Auth: required
- Query: `?hotel_id=`
- Description: List services configured for a specific hotel (price, availability)
- Response 200: Array of HotelService objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "service_id": "uuid",
    "price": 5000.0,
    "is_active": true,
    "created_at": "2026-06-01T00:00:00Z",
    "updated_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**POST /hotel-services**

- Auth: require_permission("service.manage")
- Description: Link a global service to a hotel with pricing
- Body:

```json
{
  "service_id": "uuid",
  "price": 5000
}
```

- Response 201: HotelService object

---

**PUT /hotel-services/{hotel_service_id}**

- Auth: require_permission("service.manage")
- Description: Update hotel-specific service pricing/status
- Body:

```json
{
  "price": 7500,
  "is_active": true
}
```

- Response 200: Updated HotelService object
- Errors: 404 Not found

---

**DELETE /hotel-services/{hotel_service_id}**

- Auth: require_permission("service.manage")
- Description: Disable a hotel service (soft disable)
- Response 200:

```json
{
  "message": "Service disabled"
}
```

- Errors: 404 Not found

---

### Housekeeping

**GET /housekeeping/tasks**

- Auth: required
- Query: `?status=&room_id=&branch_id=&assigned_to=`
- Response 200: Array of Housekeeping Task objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "branch_id": "uuid",
    "room_id": "uuid",
    "task_type": "CLEANING",
    "status": "OPEN",
    "priority": "HIGH",
    "assigned_to": null,
    "notes": "Post check-out cleaning",
    "scheduled_date": "2026-06-22",
    "started_at": null,
    "completed_at": null,
    "created_by": "uuid",
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**POST /housekeeping/tasks**

- Auth: require_permission("housekeeping.task.create")
- Description: Create a housekeeping task
- Body:

```json
{
  "branch_id": "uuid",
  "room_id": "uuid",
  "task_type": "CLEANING",
  "priority": "HIGH",
  "assigned_to": null,
  "notes": "Post check-out cleaning",
  "scheduled_date": "2026-06-22"
}
```

- Valid `task_type` values: `CLEANING`, `MAINTENANCE`, `INSPECTION`, `TURN_DOWN`
- Valid `priority` values: `LOW`, `MEDIUM`, `HIGH`, `URGENT`
- Response 201: Task object

---

**GET /housekeeping/tasks/my-tasks**

- Auth: required
- Query: `?status=OPEN`
- Description: List tasks assigned to the currently authenticated employee
- Response 200: Array of Task objects assigned to current user

---

**GET /housekeeping/tasks/open**

- Auth: required
- Query: `?branch_id=`
- Description: List all open and in-progress tasks
- Response 200: Array of Task objects with status `OPEN` or `IN_PROGRESS`

---

**GET /housekeeping/tasks/{task_id}**

- Auth: required
- Description: Get a single task by ID
- Response 200: Task object
- Errors: 404 Task not found

---

**PUT /housekeeping/tasks/{task_id}**

- Auth: require_permission("housekeeping.task.update")
- Description: Update task details (notes, priority, scheduled date)
- Body:

```json
{
  "notes": "Updated notes",
  "priority": "URGENT"
}
```

- Response 200: Updated Task object
- Errors: 404 Not found

---

**PATCH /housekeeping/tasks/{task_id}/status**

- Auth: require_permission("housekeeping.task.update")
- Description: Transition task to a new status
- Body:

```json
{
  "status": "COMPLETED",
  "notes": "Done"
}
```

- Valid statuses: `OPEN`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`
- Side effects:
  - `IN_PROGRESS` → sets `started_at` timestamp
  - `COMPLETED` → sets `completed_at` timestamp
  - `COMPLETED` + `CLEANING` task type → auto-sets room status to `AVAILABLE`
- Response 200: Updated Task object
- Errors: 404 Not found

---

**POST /housekeeping/tasks/{task_id}/assign**

- Auth: require_permission("housekeeping.task.assign")
- Description: Assign a task to an employee
- Body:

```json
{
  "assigned_to": "uuid"
}
```

- Response 200: Updated Task object with assignee
- Errors: 404 Not found

---

### Finance

**GET /finance/daily**

- Auth: required (same rule as `/finance/summary`; `?hotel_id=` for SUPER_ADMIN)
- Query: `?date_from=2026-10-01&date_to=2026-10-31` (both required, at most 366 days)
- Description: One row per day of the range — booking payments, refunds, expenses and paid shop sales — for charts in a single request. Days are defined exactly as in `/finance/summary` (payment `payment_date`, expense `expense_date`, shop sale paid day), so the rows add up to the summary. Days without activity are included with zeros.
- Response 200:

```json
[
  {"date": "2026-10-01", "income": 500000.0, "payment_count": 3, "refunds": 0.0, "expense": 120000.0, "shop": 30000.0}
]
```

- Errors: 422 `INVALID_RANGE` (from after to), 422 `RANGE_TOO_LONG`

---

**GET /finance/by-staff**

- Auth: ADMIN / SUPER_ADMIN or any of `finance.view`, `shift.force_close`, `report.view` (403 `STAFF_REPORT_FORBIDDEN`); `?hotel_id=` for SUPER_ADMIN
- Query: `?date_from=2026-10-01&date_to=2026-10-07` (optional; 422 `INVALID_RANGE` if from > to)
- Description: Revenue of the period split by the employee who took the money — booking payments (`Payment.created_by`, by `payment_date`, refunds are negative), shop payments (`ShopSalePayment.created_by` — the employee who took the money, partial payments included, by the payment's `paid_at`; split payments go to each part's method) and expenses (`Expense.created_by`, by `expense_date`). Same day definitions as `/finance/summary`, so the sum of `revenue` equals summary `income + shop_revenue`. Sorted by revenue, then expense. An employee with only expenses is listed too.
- Response 200:

```json
{
  "items": [
    {"user_id": "uuid", "name": "Dilnoza Rahimova", "user_type": "EMPLOYEE", "status": "ACTIVE",
     "revenue": 2230000.0, "income": 2200000.0, "payment_count": 7, "refunds": 0.0,
     "shop": 30000.0, "shop_count": 1, "cash": 1530000.0,
     "expense": 0.0, "expense_count": 0, "cash_expense": 0.0,
     "methods": [{"key": "CASH", "pay": 1500000.0, "shop": 30000.0}, {"key": "CARD", "pay": 700000.0, "shop": 0.0}]}
  ],
  "total": {"revenue": 2230000.0, "income": 2200000.0, "shop": 30000.0, "refunds": 0.0, "expense": 0.0, "payment_count": 7}
}
```

---

**GET /finance/penalties**

- Auth: required (`?hotel_id=` for SUPER_ADMIN)
- Query: `?date_from=2026-10-01&date_to=2026-10-31` (optional; by `penalty_date`)
- Description: Penalty journal for the period — every penalty with booking number, room and guest; voided ones are listed but not counted. `/finance/summary` also returns `penalty_total` and `penalty_count` (active penalties of the period).
- Response 200:

```json
{
  "summary": {"total": 200000.0, "count": 2, "by_kind": {"LATE_CHECKOUT": {"total": 50000.0, "count": 1}, "DAMAGE": {"total": 150000.0, "count": 1}, "OTHER": {"total": 0.0, "count": 0}}},
  "items": [{"id": "uuid", "kind": "DAMAGE", "amount": 150000.0, "reservation_number": "R-0012", "room_number": "204", "guest_name": "Ali Valiyev", "voided_at": null}]
}
```

---

**GET /hotels/debt-settings** / **PUT /hotels/debt-settings**

- Auth: GET — any employee; PUT — configurator / SUPER_ADMIN (`assert_can_manage_settings`)
- Body (PUT): `{"enabled": true, "interval_minutes": 120}` — interval one of 30/60/120/240/480 (422 `INVALID_INTERVAL`)
- Response 200: `{"enabled": true, "interval_minutes": 120, "allowed_intervals": [...], "default": {...}}`
- Debt reminders (`debt_reminder_service`, run from the automation tick every 5 min): a digest of debtors with reasons every `interval_minutes` (08:00–22:00 local), plus once-per-booking alerts "left with debt" (checked out in the last 24 h) and "leaves today with debt". Recipients: hotel ADMINs and employees with `finance.payment.create`; DB notification + FCM push. Notification `type` in `/notifications` is `debt` (`entity_type` `debt_*`).

---

**GET /reservations/{id}/debt**

- Auth: required (hotel-scoped)
- Description: The booking's account — how much is owed and WHY. Payments cover charges chronologically (room first, then extension, services, penalties); the unpaid part of each charge is a reason. For CHECKED_IN the amount is what `check-out` will charge (extended stays included). Shop sales charged to the booking (PENDING remainder) are part of the guest debt.
- Response 200: `{"reservation_id", "status", "total_amount", "expected_total", "paid_amount", "projected", "reservation_debt", "shop_debt", "total_debt", "items": [{"kind": "room|extension|service|penalty|shop", "amount", "charged", ...}], "charges", "shop_sales", "payments", "overdue_days", "acknowledged": {"at", "by", "by_name", "note", "amount"} | null}`

**POST /reservations/{id}/request-checkout** — debt check

- Body (optional): `{"acknowledge_debt": true, "debt_note": "ertaga keltiradi"}`
- With an outstanding debt (booking + shop) the reception call is refused with 409 `CHECKOUT_DEBT` (message lists the reasons) unless `acknowledge_debt` is sent with a note (422 `DEBT_NOTE_REQUIRED` if empty); who/when/amount/note are saved on the booking (`debt_ack_*`) and managers are notified. The cleaner's button (`self_assign=true`) is never blocked — cashiers are notified instead. Automatic check-outs are not blocked; the reminder service reports them.

**POST /reservations/{id}/settle-payment** — for a CHECKED_IN guest a PAY first raises `total_amount` to the check-out total (extended stay), so the extension can be collected before the guest leaves.

**GET /finance/debtors** — each item now also has `reservation_debt`, `shop_debt`, `expected_total`, `projected`, `reasons`, `reason_text`, `overdue_days`, `acknowledged`; `debt_amount` = booking + shop. Includes CHECKED_IN guests whose check-out total exceeds what they paid, and bookings with only an unpaid shop sale. `summary` adds `reservation_debt` and `shop_debt`.

---

**GET /hotels/penalty-settings** / **PUT /hotels/penalty-settings**

- Auth: GET — any employee; PUT — configurator / SUPER_ADMIN (`assert_can_manage_settings`, 403 `SETTINGS_CONFIGURATOR_ONLY`)
- Body (PUT): `{"late_hourly_amount": 50000, "grace_minutes": 30}` — `late_hourly_amount` 0 turns the late-checkout suggestion off (default)
- Response 200: `{"late_hourly_amount": 50000.0, "grace_minutes": 30, "checkout_hour": 12}` (`checkout_hour` — server checkout hour for daily bookings, read-only)

---

**GET /finance/ledgers**

- Auth: required
- Query: `?hotel_id=`
- Description: List chart of accounts (ledgers)
- Response 200: Array of Ledger objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "name": "Room Revenue",
    "code": "4100",
    "type": "INCOME",
    "parent_id": "uuid",
    "is_active": true,
    "created_at": "2026-06-01T00:00:00Z"
  }
]
```

---

**GET /finance/invoices**

- Auth: required
- Query: `?status=ISSUED`
- Response 200: Array of Invoice objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "reservation_id": "uuid",
    "guest_id": "uuid",
    "invoice_number": "INV-THA070705-20260622-A3F9",
    "invoice_date": "2026-06-22",
    "due_date": null,
    "subtotal": 70000.0,
    "tax_amount": 0,
    "discount_amount": 0,
    "total_amount": 70000.0,
    "paid_amount": 0,
    "status": "ISSUED",
    "notes": null,
    "created_at": "2026-06-22T12:00:00Z",
    "updated_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**POST /finance/invoices**

- Auth: require_permission("finance.invoice.create")
- Description: Manually create an invoice for a reservation
- Body:

```json
{
  "reservation_id": "uuid"
}
```

- Response 201: Invoice object with line items auto-generated from reservation details

---

**GET /finance/invoices/{invoice_id}**

- Auth: required
- Description: Get a single invoice with line items and payment history
- Response 200:

```json
{
  "id": "uuid",
  "invoice_number": "INV-THA070705-20260622-A3F9",
  "total_amount": 70000.0,
  "paid_amount": 10000.0,
  "status": "PARTIALLY_PAID",
  "line_items": [
    {
      "id": "uuid",
      "description": "Room charge: 2 nights",
      "line_type": "ROOM_CHARGE",
      "quantity": 2,
      "unit_price": 35000,
      "total_price": 70000
    }
  ],
  "payments": [
    {
      "id": "uuid",
      "payment_number": "PAY-THA070705-20260622-A3F9",
      "amount": 10000,
      "payment_method": "CASH",
      "payment_date": "2026-06-22"
    }
  ]
}
```

- Errors: 404 Invoice not found

---

**POST /finance/invoices/{invoice_id}/pay**

- Auth: require_permission("finance.payment.create")
- Description: Record a payment against an invoice
- Body:

```json
{
  "invoice_id": "uuid",
  "amount": 10000,
  "payment_method": "CASH",
  "payment_date": "2026-06-22",
  "reference": "Receipt #001",
  "notes": "Partial payment"
}
```

- Valid `payment_method` values: `CASH`, `CREDIT_CARD`, `DEBIT_CARD`, `BANK_TRANSFER`, `MOBILE_PAYMENT`, `ONLINE`
- Response 201: Payment object
- Side effects:
  - Invoice `paid_amount` updated
  - Invoice status auto-updated (`PAID`, `PARTIALLY_PAID`)
- Errors: 404 Invoice not found

---

**GET /finance/payments**

- Auth: required
- Query: `?invoice_id=`
- Description: List payments, optionally filtered by invoice
- Response 200: Array of Payment objects

---

**GET /finance/journal-entries**

- Auth: required
- Description: List journal entries (double-entry accounting records)
- Response 200: Array of JournalEntry objects (with associated lines)

---

**POST /finance/journal-entries**

- Auth: require_permission("finance.journal.create")
- Description: Create a journal entry (debits must equal credits)
- Body:

```json
{
  "entry_date": "2026-06-22",
  "description": "Monthly utility bill",
  "reference_type": "expense",
  "lines": [
    {
      "ledger_id": "uuid",
      "debit": 50000,
      "credit": 0,
      "description": "Electricity"
    },
    {
      "ledger_id": "uuid",
      "debit": 0,
      "credit": 50000,
      "description": "Cash payment"
    }
  ]
}
```

- Response 201: JournalEntry object with lines (status: `DRAFT`)
- Errors: 422 Debits do not equal credits

---

### Shifts — cash overview

**GET /shifts/cash-overview**

- Auth: ADMIN / SUPER_ADMIN or an employee with `shift.force_close` (others: 403 `FORBIDDEN`)
- Description: How much cash should be in every open drawer right now (ACTIVE and PENDING_HANDOVER sessions of the whole hotel), with the same breakdown as the handover count: opening cash + cash payments + cash shop sales − cash expenses. The blind-count rule is kept — the amount is shown to the supervisor, not to the session owner. In `simple` shift mode the list is empty.
- Response 200:

```json
{
  "mode": "cash",
  "total_expected": 3480000.0,
  "active_count": 1,
  "pending_count": 1,
  "sessions": [
    {
      "id": "uuid", "user_name": "Dilnoza Karimova", "branch_name": "Yunusobod",
      "status": "PENDING_HANDOVER", "started_at": "2026-10-15T03:30:00+00:00",
      "opening_cash": 100000.0, "payments_cash": 700000.0, "shop_cash": 0.0,
      "expenses_cash": 50000.0, "expected_cash": 750000.0, "counted_cash": 720000.0
    }
  ]
}
```

**GET /shifts/handovers**

- Auth: ADMIN / SUPER_ADMIN or `shift.force_close` (others: 403 `FORBIDDEN`)
- Query: `?date_from=2026-10-01&date_to=2026-10-06&limit=200` (all optional; local day of `ended_at`; 422 `INVALID_RANGE`; `limit` ≤ 500)
- Description: Cash passed from shift to shift. One item per CLOSED or PENDING_HANDOVER session — where the counted cash went (`kind`): `HANDOVER` (the next employee accepted it; it became their opening cash), `PENDING` (handed over, waiting for acceptance), `CASH_OUT` (day close — the cash left the drawer, the employee's next session starts from 0), `FORCE_TAKEN` (force-closed and the manager took the cash). For `HANDOVER` the receiver's new session (started within ±2 minutes of `accepted_at`) is matched and its `received_opening_cash` returned, so a broken chain (e.g. the counted amount corrected later) is visible.
- Response 200:

```json
{
  "mode": "cash",
  "summary": {"handed_over_total": 1200000.0, "handed_over_count": 1, "taken_out_total": 800000.0, "taken_out_count": 2,
              "pending_total": 800000.0, "pending_count": 1, "shortage_total": 50000.0, "surplus_total": 10000.0},
  "items": [
    {"id": "uuid", "kind": "HANDOVER", "from_user_name": "Dilnoza R", "to_user_name": "Aziz K", "closed_by_name": "Dilnoza R",
     "branch_name": "Markaziy", "ended_at": "...", "accepted_at": "...", "opening_cash": 100000.0,
     "expected_cash": 1250000.0, "counted_cash": 1200000.0, "cash_diff": -50000.0, "force_closed": false,
     "corrected": false, "received_opening_cash": 1200000.0}
  ]
}
```

---

### Reports

**GET /reports**

- Auth: required
- Query: `?report_type=&hotel_id=`
- Description: List previously generated and saved reports
- Response 200: Array of saved Report objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "name": "Occupancy Report — June 2026",
    "report_type": "occupancy",
    "parameters": {
      "start_date": "2026-06-01",
      "end_date": "2026-06-30"
    },
    "result_data": {
      "occupancy_rate": 75.5,
      "total_rooms": 100,
      "occupied_rooms": 75
    },
    "generated_by": "uuid",
    "generated_at": "2026-06-22T12:00:00Z",
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**POST /reports/generate**

- Auth: require_permission("report.view")
- Description: Generate a new report
- Body:

```json
{
  "report_type": "occupancy",
  "start_date": "2026-06-01",
  "end_date": "2026-06-30",
  "branch_id": null
}
```

- Valid `report_type` values: `occupancy`, `revenue`
- Response 201: Generated Report object with `result_data` populated

---

**GET /reports/{report_id}**

- Auth: required
- Description: Retrieve a previously generated report by ID
- Response 200: Saved Report object with cached result
- Errors: 404 Not found

---

### Audit Logs

**GET /audit-logs**

- Auth: require_permission("report.view")
- Query: `?entity_type=Reservation&entity_id=uuid&action=reservation.created&from=2026-06-01T00:00:00&to=2026-06-30T23:59:59&page=1&page_size=50`
- Description: Query the audit trail for system changes
- Response 200: Array of AuditLog objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "user_id": "uuid",
    "action": "reservation.created",
    "entity_type": "Reservation",
    "entity_id": "uuid",
    "old_values": null,
    "new_values": {
      "guest_name": "Nursultan",
      "room_number": "101",
      "check_in": "2026-06-23"
    },
    "ip_address": "127.0.0.1",
    "user_agent": "Mozilla/5.0 ...",
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

### Files (MinIO)

**POST /files/upload**

- Auth: required
- Content-Type: `multipart/form-data`
- Description: Upload a file to MinIO storage
- Fields:
  - `file` — file binary
  - `entity_type` — `guest` | `room` | `hotel` | `reservation`
  - `entity_id` — UUID of the associated entity
  - `category` — `passport` | `room_photo` | `document` | `other`
- Response 201:

```json
{
  "id": "uuid",
  "entity_type": "guest",
  "entity_id": "uuid",
  "file_name": "passport.jpg",
  "original_name": "passport_photo.jpg",
  "mime_type": "image/jpeg",
  "file_size": 245760,
  "minio_bucket": "hotel-guests",
  "minio_path": "guest/passport_photo.jpg",
  "category": "passport",
  "uploaded_by": "uuid",
  "created_at": "2026-06-22T12:00:00Z"
}
```

---

**GET /files/{file_id}**

- Auth: required
- Description: Get file metadata
- Response 200: File metadata object
- Errors: 404 Not found

---

**GET /files/{file_id}/download**

- Auth: required
- Description: Get a presigned download URL (expires after 1 hour)
- Response 200:

```json
{
  "url": "http://minio:9000/bucket/path?signature=..."
}
```

- Errors: 404 Not found

---

**DELETE /files/{file_id}**

- Auth: required
- Description: Soft delete a file — metadata preserved, MinIO file archived
- Response 200:

```json
{
  "message": "File deleted"
}
```

- Errors: 404 Not found

---

### Notifications

**GET /notifications**

- Auth: required
- Query: `?unread_only=true`
- Description: List notifications for the current user
- Response 200: Array of Notification objects

```json
[
  {
    "id": "uuid",
    "hotel_id": "uuid",
    "user_id": "uuid",
    "title": "Room 301 ready for inspection",
    "body": "Cleaning completed for Room 301",
    "entity_type": "HousekeepingTask",
    "entity_id": "uuid",
    "is_read": false,
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

**GET /notifications/broadcasts**

- Auth: required
- Description: List hotel-wide broadcast notifications (`user_id` = null)
- Response 200: Array of Notification objects (broadcasts)

---

**PATCH /notifications/{notification_id}/read**

- Auth: required
- Description: Mark a notification as read
- Response 200:

```json
{
  "message": "Marked as read"
}
```

- Errors: 404 Notification not found

---

## Health Check

**GET /health**

- No auth required
- Description: Service health check
- Response 200:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "env": "development"
}
```

---

## Common Error Responses

| Status | Code              | Description                         |
| ------ | ----------------- | ----------------------------------- |
| 400    | `BAD_REQUEST`     | Invalid request                     |
| 401    | `UNAUTHORIZED`    | Missing or invalid token            |
| 403    | `FORBIDDEN`       | Insufficient permissions            |
| 403    | `OUTSIDE_WORK_HOURS` | Employee outside working hours (see [Work-hours enforcement](#work-hours-enforcement)) |
| 403    | `HOTEL_INACTIVE` / `HOTEL_SUSPENDED` | Hotel service stopped        |
| 404    | `NOT_FOUND`       | Resource not found                  |
| 409    | `CONFLICT`        | Duplicate or conflicting resource   |
| 422    | `VALIDATION_ERROR`| Invalid input data                  |
| 500    | `APP_ERROR`       | Internal server error               |

All error responses follow this format:

```json
{
  "detail": "Human-readable error message",
  "error_code": "ERROR_CODE"
}
```

---

## Pagination

List endpoints support pagination via query parameters:

| Parameter    | Type    | Default | Max   | Description                |
| ------------ | ------- | ------- | ----- | -------------------------- |
| `page`       | integer | 1       | —     | Page number (1-indexed)    |
| `page_size`  | integer | 20      | 100   | Number of items per page   |

---

## Soft Delete

The following endpoints implement soft delete (record marked as `is_deleted: true` and preserved in the database):

- `DELETE /rooms/{room_id}`
- `DELETE /guests/{guest_id}`
- `DELETE /employees/{employee_id}`
- `DELETE /files/{file_id}`
- `DELETE /hotel-services/{hotel_service_id}`
