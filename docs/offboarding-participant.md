# Offboarding participant

The platform worker pulls `GET /v1/offboarding` as the OWUI product credential and
acknowledges each task through `POST /v1/offboarding/{id}/acknowledgement`. Its
lifespan matches the existing soev loops. Task data contains immutable platform
person references, mode, target and fixed archive expiry, without contact data.

Local execution is deliberately unavailable. The worker maps source and transfer
target through `User.person_id`, reports `target_missing` or `source_conflict`
when a binding is absent, and otherwise reports `unsupported_content`. It never
calls legacy offboarding or acknowledges successful disposal. Logs include only
the exception class, never the response body. Repeated failure acknowledgements
are idempotent at the platform. No new OWUI routes or route exemptions are added.

The existing platform admin deletion refusal remains in force. Legacy deletion
cannot implement the participant contract: it deletes chats during transfer and
uses the departing person's live authority for remote work. Native platform
threads and local product tables still need disposition under the job authority,
plus archive export and expiry, before this worker can acknowledge success.
