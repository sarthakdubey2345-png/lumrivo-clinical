# Lumrivo Clinical

A working, self-hostable hospital demonstration application. Reception registers a fictional patient and visit; the assigned doctor documents a consultation, reviews a draft, approves a versioned record, and prints a prescription or patient care sheet. Returning visits have an approved history timeline.

**Release 0.1.0 is demonstration-only. It is not ready for real-patient deployment.** This is enforced at startup: `DEMO_MODE=false` refuses to start. Actual provider transcription and extraction are implemented but were not end-to-end tested with a paid API key. Do not remove the clinical-use gate or demonstration labels as a shortcut to a patient pilot.

## Run locally on your Mac

Requires Python 3.12+; no Python packages or Node packages are needed for local development.

1. Unzip this folder and open Terminal in `lumrivo-clinical`.
2. Run:

```bash
python3 -m app.server
```

3. Open **http://localhost:8000**.

Local development logins (fictional accounts only):

| Role | Email |
|---|---|
| General physician | meera@demo.lumrivo.com |
| Orthopaedics | arjun@demo.lumrivo.com |
| Dermatology | sana@demo.lumrivo.com |
| Reception | reception@demo.lumrivo.com |
| Administrator | admin@demo.lumrivo.com |

The local development password for all five seeded accounts is `LumrivoDemo!2026`. It is never suitable for hosted access. Before the FIRST hosted startup, set a unique `DEMO_PASSWORD` as described below. Each user can then change their password from the account avatar menu. The environment password only affects first database creation, not existing passwords.

## Demonstrate the application without an AI key

1. Sign in as the general physician and open Aanya Sharma's visit.
2. Click **Load fictional walkthrough**. This inserts scripted text and an editable fictional draft; it does not call AI.
3. Edit the instructions, add a test or follow-up, and inspect the previous visit.
4. Confirm the fictional medicine row and resolve the review item after checking it.
5. Check the clinician-review confirmation, then approve.
6. Open the prescription and patient care sheet. Use Print / save PDF.
7. Start an amendment with a reason, change the instructions, and approve again.
8. The version history retains the original and identifies it as superseded.
9. Sign in as reception, create a fictional patient and assign a visit to another specialty.

Tokens are allocated per doctor per UTC day in this build. The display label is not the patient identifier. Queue metrics currently cover the records in the workspace, not a timezone-filtered daily analytics report.

## Connect actual speech and AI draft generation

Set `OPENAI_API_KEY` only in the server environment. Never place it in JavaScript, the repository or the browser. If you run Python directly, export your configuration in your private shell environment before starting the process. Docker Compose reads `.env` automatically; Python does not.

- Transcription adapter: `POST https://api.openai.com/v1/audio/transcriptions`
- Default transcription model: `gpt-4o-transcribe`
- Draft adapter: chat completions with a strict JSON schema
- Default document model: `gpt-4.1-mini`
- Model names can be configured through `TRANSCRIPTION_MODEL` and `DOCUMENT_MODEL`.

After configuration, the provider badge changes to **AI key configured**. This means a key is present, not that the key or provider is verified.

The doctor grants recording consent, records a segment, optionally pauses/resumes, then stops. The audio is sent for transcription; review the transcript and click Generate reviewable draft. Proposed differences must be accepted into the editable draft. Generation never approves or issues a record. Use Add / correct details for a typed clinician instruction; an additional recording can be made from the capture panel.

Microphone capture requires HTTPS or localhost, browser permission and a supported MediaRecorder format. Segments stop at ten recorded minutes and have a 14 MiB upload limit. Audio is held in browser memory until processing; it is not retained by this application. Closing the page loses unsent audio. Provider failures leave saved documents unchanged; an interrupted request does not recover audio across a page refresh. Provider retention, data location and commercial terms must be assessed separately. No paid provider call was made while building this release.

Official API documentation used for the adapter:

- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat

## Host the demonstration on Lumrivo

Suggested hostname: **clinical.lumrivo.com** (suggestion only; it has not been configured or deployed).

### Docker Compose with a reverse proxy

```bash
cp .env.example .env
```

Edit `.env` privately. Generate a random secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `APP_SECRET` to the generated value and `DEMO_PASSWORD` to a unique password of at least 16 characters. Replace both placeholders; the server rejects placeholder values. Keep the secret stable to preserve sessions. Rotating it invalidates existing sessions. Then:

```bash
docker compose up -d --build
```

The service listens on localhost port 8000, intended to sit behind a reverse proxy with HTTPS. Forward your chosen subdomain to it. Limit request body size to 20 MiB and allow at least a 120-second upstream response timeout for AI processing. Use access restrictions for hospital demonstrations. Do not expose the Python development server publicly.

### Coolify on your existing server

1. Upload or commit this source to a repository you control. This task has not created a GitHub repository.
2. Create a Dockerfile application in Coolify with the project root containing `Dockerfile`.
3. Set container port **8000**.
4. Add a persistent volume mounted at **/data**. It must be writable by UID **10001**.
5. Configure `PRODUCTION=true`, `DEMO_MODE=true`, a random `APP_SECRET` of 32+ characters and a unique `DEMO_PASSWORD` of 16+ characters before first deployment.
6. Optionally add `OPENAI_API_KEY` and model variables. Set `DATA_DIR=/data`.
7. Assign your intended HTTPS subdomain and configure its DNS to the server.
8. Deploy, then check `/health`, sign-in, a manual save, approval, restart persistence and the PDF print layout.

Docker / Coolify deployment was not executed in this environment. The runtime dependency is pinned in `requirements.txt`, but a container build and live TLS/volume verification remain deployment checks. The default Docker image uses one Gunicorn worker and eight threads, keeping the in-memory login rate limiter effective within that process. Apply additional request/login rate limiting at the reverse proxy before wider distribution.

### Multiple hospital demonstrations

The database schema and API enforce hospital boundaries. Provision each hospital separately rather than sharing the seeded hospital account:

```bash
python3 manage.py create-hospital --name "Fictional Hospital Two" --admin-email admin@example.com
```

The command securely prompts for an initial password. In Docker run it interactively inside the same container with the same data mount and configuration. Each hospital administrator can create doctors and reception staff in Hospital settings. New hospitals begin empty. No self-service registration is enabled. Every clinician should have an individual account. This release has not been load-tested for 10–15 simultaneous doctors or for 15 hospitals.

## Backup and restore

Use the SQLite backup API, not a raw copy of the live main database file: WAL transactions may be in companion files.

```bash
python3 manage.py backup backups/clinical-backup.sqlite3
python3 manage.py check-backup backups/clinical-backup.sqlite3
```

In Docker run backup commands inside the container and use a persistent backup destination. Protect and copy the resulting backup out of the container. Backups inherit the sensitivity of their content; this demonstration stores synthetic data only.

For isolated restoration: stop a separate test instance; place a verified backup at its configured `DATA_DIR/clinical.sqlite3` with ownership allowing the application user to read/write; start that instance with the same configuration; verify logins, history and approved snapshots. Do not restore over a running instance or reuse another hospital's volume. Do not copy stale WAL/SHM files onto a restored database.

A consistent-backup and restored-snapshot test is included. Full deployed disaster recovery has not been exercised.

## Implemented

- Reception registration, unique hospital patient ID and doctor-specific token allocation.
- Doctor, reception and administrator roles with server-side checks.
- Doctors can access/edit assigned encounters. They can view that patient's approved hospital history while assigned to an encounter. Reception and administrators do not receive clinical drafts or transcripts.
- Multi-specialty doctor profiles, configurable hospital identity and team creation.
- Persistent SQLite records with WAL and transactional stale-version protection.
- Visible recording consent, microphone start/pause/resume/stop, transcription adapter and manual entry.
- Structured draft fields, explicit allergy status, editable medicine rows and clinician confirmation.
- Side-by-side generated proposals; explicit application of corrections.
- Approval checks and immutable issued snapshots; amendments supersede rather than overwrite issued versions.
- Professional prescription and readable patient care sheet from the same approved snapshot.
- Printable layouts and browser Save as PDF.
- Account password changes, hashed passwords, hashed expiring sessions, CSRF checks, SameSite cookies and security headers.
- Access/change audit metadata and admin activity view.
- Clearly labelled scripted walkthrough with fictional medicines and patients.

## Known limits and next engineering work

This is a functioning demonstration foundation, not the complete five-day clinical pilot programme:

- No live provider accuracy/latency/cost evaluation, microphone end-to-end browser test, clinician validation or real-patient pilot has been completed.
- Browser visual verification reached the login screen. The authenticated UI and print layout still need interactive browser verification. The API workflows are covered by integration tests.
- The approved clinical snapshot is immutable through the application, but the database administrator can alter SQLite files; this is not a tamper-proof medical-record archive.
- SQLite data is not application-layer encrypted at rest. Use protected, encrypted infrastructure for demos; clinical deployment requires an assessed security architecture and retention/incident procedures.
- No reviewed Indian medicine master is included. Catalogue suggestions are fictional. There is no interaction checker, automatic substitution, dosing advice or treatment recommendation engine.
- Field-level transcript timestamps/speaker attribution and source-linked edit provenance are incomplete. The full transcript and medicine evidence text are available for review; speaker identity is not authenticated by audio.
- No background job queue, durable audio recovery, configured usage quotas, provider-spend metering or offline speech engine.
- No patient messaging, Hindi care-sheet translation, real lab status/results, EHR/HIS integration, ABHA/ABDM connection, billing or hospital inventory.
- No in-product user deactivation, password recovery email, hospital data export/deletion console or retention scheduler yet. Session revocation on password change is implemented.
- No automated migration framework, zero-downtime upgrade/rollback validation or PostgreSQL deployment. Keep a tested backup before schema changes.
- Patient consent is recorded as a clinician-entered event. It is not a signed patient consent form or legal determination.
- Demographic updates and duplicate-patient merges are deliberately absent. Build reviewed workflows before adding them.
- Patient-facing advice is printed from doctor-entered/approved fields; no independent clinical advice is generated. The clinician must write these fields in language the patient understands.

Before real-patient evaluation: complete the intended-use/regulatory assessment, hospital agreements and privacy/provider review; import a licensed, clinician-reviewed formulary; complete security and clinician evaluation; validate patient matching, recording consent, failure/recovery, print output and deployment operations. A doctor review checkbox does not establish regulatory clearance or general safety.

## Verify locally

```bash
python3 -m unittest discover -s tests -v
node --check app/static/app.js
```

The test suite uses isolated temporary databases and synthetic data. It does not call a paid AI provider. See `VERIFICATION.md` for the actual build evidence and limits.

## Source layout

- `app/server.py`: database, authentication, access control and API.
- `app/ai.py`: server-side transcription and structured-draft provider adapter.
- `app/static/`: responsive application, review workflow and print styling.
- `manage.py`: hospital provisioning and consistent database backup tools.
- `tests/test_workflows.py`: integration and record-integrity checks.
- `Dockerfile`, `compose.yaml`, `.env.example`: deployment preparation.

No patient content from the uploaded prescription is included in this source or demonstration database. This source package excludes databases, credentials, sessions and recordings.
