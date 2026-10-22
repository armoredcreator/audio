# Exact-message original recovery

`scripts/recover_telegram_original.py` is a private recovery tool, separate from
the Coordinator. It does not open a pipeline database, advance checkpoints,
approve products, join chats, send messages or scan historical messages.

## New authorization

Create a new dedicated workspace outside the repository. An existing directory
without the tool's workspace marker is rejected. Operational sessions must never
be copied into this workspace. Reusing a marked workspace resumes only this
new recovery session.

Run interactively in a local terminal with Python 3.11.9. Supply a private audit
manifest, one exact content ID and a credentials environment file containing the
application's existing `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`:

```text
python scripts/recover_telegram_original.py --workspace PRIVATE_NEW_DIRECTORY --credentials-env PRIVATE_ENV_FILE --manifest PRIVATE_MANIFEST --content-id=EXACT_CONTENT_ID --login
```

The operator enters their telephone number, Telegram login code and optional
two-step verification password directly in that terminal. Entries are hidden.
Do not put login secrets in chat, command arguments, reports or Git. There is
no generic resend loop: authentication, flood or ambiguous timeout errors stop
the run. A server-rejected `PhoneMigrateError` permits at most one further RPC,
only after the SDK confirms the new session's DC changed to that exact target.
The rejected redirect does not establish code delivery. The real last RPC error
is retained instead of Telethon's generic exhausted-attempt `ValueError`.
Reports contain only request counts/DC numbers, never phone or code. Account
creation and password changes are outside this tool.

Telegram describes authorization and its account-specific flood limits in its
[official authorization documentation](https://core.telegram.org/api/auth).
An authorized session does not establish that every historical source/message
is accessible to that account.

## Bounded proof

The manifest must identify one numeric source ID and one positive message ID.
The tool resolves that exact source. If a new session lacks its cached access
hash, it examines at most 200 dialog metadata entries, without reading message
history or joining any source. It requests only the selected message and checks
both source and message identity before downloading.

One video of at most 512 MiB is permitted. The download uses exclusive creation
in a private `.part` file. Declared byte count, video streams, full FFmpeg decode
and current SHA-256 are checked before the result is recorded. An existing file
is reused only with a matching receipt, exact identity and unchanged hash. An
unexplained existing file, partial file or hash mismatch is preserved and blocks
that case for review.

A current SHA-256 is evidence of the retrieved bytes. It is not a historical
checksum when no checksum was originally stored. The receipt states this
limitation and records the exact authorized source/message/document association.
Message captions and recovered source links remain private local evidence.

## Separate reconciliation

Successful recovery creates only a local media file and receipt. It does not
update the operational database or establish current affiliate eligibility.
Any later database reconciliation requires its own exact record list, consistent
backup, isolated validation, rollback procedure and explicit authorization.
