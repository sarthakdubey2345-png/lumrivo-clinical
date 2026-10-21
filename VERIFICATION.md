# Release verification — Lumrivo Clinical 0.1.0

Built on 21 October 2026. This report distinguishes executed checks from prepared or unverified capabilities.

## Executed successfully

- Python source compilation for the application, provider adapter and administration tool.
- JavaScript syntax validation with Node 24.
- **32 integration tests passed** against isolated temporary SQLite databases. The final test run completed in 1.739 seconds.
- Authentication rejection and logout/session revocation.
- CSRF and cross-origin write rejection.
- Reception receives no clinical draft/transcript fields and cannot read consultation history.
- Doctors see only their assigned encounters and cannot edit another doctor's encounter.
- Administrators cannot edit clinical encounters merely by being administrators.
- Cross-hospital patient visibility and visit-creation checks.
- Stale draft rejection without overwriting newer content.
- Explicit audio consent gate; refusal permits manual documentation.
- No default absence-of-allergy assertion.
- Approval blocks for unassessed/conflicting allergy details, incomplete or unconfirmed medicines, unresolved review items and absent clinician attestation.
- Duplicate medicine IDs and duplicate active visits rejected.
- Shared phone numbers do not merge patients.
- Double approval cannot create duplicate issued documents.
- Approved record cannot be edited without starting an amendment.
- Amendment retains the prior snapshot and links the replacement to its predecessor.
- AI unavailable response is explicit and does not fabricate generated content.
- Mocked model proposal cannot confirm medicines or directly persist/approve content.
- Audit records include approval events without copying the clinical note into audit metadata.
- Consistent SQLite backup and restoration of an approved snapshot.
- Administration CLI created a separate backup; read-only integrity check returned `ok` with one seeded approved document.
- Installed pinned Gunicorn 26.2.0 into an isolated validation directory; Gunicorn configuration/application import check passed with one worker, eight threads and a 150-second timeout.
- Preview server launched. Login page inspected through browser accessibility and screenshot; layout rendered in Lumrivo colours with clearly visible demonstration notice.

## Not executed / not established

- No paid AI transcription or extraction requests; no provider key supplied.
- No microphone capture/transcription browser round-trip.
- No authenticated browser walkthrough, mobile browser walkthrough, accessibility audit or print/PDF visual inspection.
- No Docker image build (Docker is not installed in the execution environment).
- No Coolify/server deployment, DNS/TLS setup, persistent-volume restart check or live load test.
- No clinical assessment of accuracy, completeness, patient comprehension or safety.
- No real-patient use, regulatory clearance or completed privacy compliance assessment.
- No licensed real medicine database, hospital HIS integration or translation evaluation.

## Release decision

Suitable as a source-code handoff and a local/hosted **fictional demonstration build**. Complete authenticated browser/print QA and actual provider tests before presenting unqualified claims about those capabilities. Real-patient mode is deliberately disabled until further engineering, clinical and operational readiness work is completed.

See README.md for run/deployment instructions, accounts, operational limits and remaining work.
