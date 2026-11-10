# Offboarding participant

The platform worker pulls `GET /v1/offboarding` as the OWUI product credential and
acknowledges each task through `POST /v1/offboarding/{id}/acknowledgement`. Its
lifespan matches the existing soev loops. Task data contains immutable platform
person references, mode, target and fixed archive expiry, without contact data.

The worker maps source and transfer target through `User.person_id`. A missing
source reports `source_conflict`; a missing or inactive target reports
`target_missing`. It never invokes legacy chat offboarding or calls the platform
as the suspended person. No new OWUI routes or route exemptions are added.

Local PostgreSQL transactions dispose of assistants, prompts, tools, functions,
notes, memories, files, calendar, chat organisation, folders and dependent rows.
Delete removes owned content and its shares. Transfer moves local ownership to
the target and preserves other people's shares. Cloud file copies are removed in
every mode. Other people's content and shares remain unchanged. Storage paths
referenced by another owner's file are retained. The vector adapter requires
PostgreSQL and propagates database failures; it does not rely on the legacy
collection-existence method, which can hide a failed query.

Archive removes local product content and keeps only chat organisation and its
folder ancestry under an opaque archive owner. It clears legacy chat payloads
while retaining platform thread references. Its receipt records the fixed expiry.
The local archive expiry consumer is unimplemented, so this is not a complete
archive facility.

Prepared and completed receipts use namespaced Config rows, bound to the immutable
job disposition and person. Preparation locks the source and sets its local role
to pending. PostgreSQL advisory locks serialize workers; completion is committed
with local row changes before acknowledgement. Replays after a lost acknowledgement
do not repeat local disposal. External vector and storage mutations are idempotent
and precede row erasure. Errors leave the receipt prepared and report
`local_cleanup_failed`; logs contain exception classes, never response bodies.
Expired archives refuse effects; a completed receipt can still be acknowledged.

Source-owned legacy knowledge rows and legacy archive snapshots are refused with
`unsupported_content`: their creator field does not prove platform ownership.
Delete and archive also refuse an owned calendar containing another person's
events. These refusals preserve data rather than claim complete cleanup.

The existing platform admin deletion refusal remains in force. Legacy deletion
cannot implement the participant contract: it deletes chats during transfer and
uses the departing person's live authority for remote work. Native platform
threads still need disposition under the job authority, plus archive export and
expiry. Platform production admission remains disabled for every mode. Revocation
of existing OWUI sessions before this participant and fencing writes already in
flight are not established. Tests prove local PostgreSQL dispositions, replay,
fixed failure acknowledgements and vector mutation isolation; storage/vector
service integration and the complete platform job remain unproven.
