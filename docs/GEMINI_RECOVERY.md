# Gemini availability and directed IA recovery

Gemini generation keeps the configured model and the persisted Vision identity.
It does not switch provider/model, invent a caption or interpret an unavailable
service as a valid IA result.

## Retry policy

The provider retries timeouts, connection failures and HTTP 429/500/502/503/504.
HTTP 400/401/403/404 and other rejected requests stop immediately. Configuration,
credentials and unsupported models require a correction before another attempt.

The wait grows exponentially. A valid `Retry-After` (seconds or HTTP date) or
Google `google.rpc.RetryInfo.retryDelay` takes precedence. If both are present,
the longer wait is honored. A server wait beyond the local limit stops the call;
the client never shortens that wait and sends another request prematurely.

| Variable | Default | Accepted range |
| --- | ---: | ---: |
| `ARMORED_IA_MAX_ATTEMPTS` | 5 | 1–10 integers |
| `ARMORED_IA_RETRY_DELAY` | 2 seconds | 0–60 seconds |
| `ARMORED_IA_MAX_RETRY_DELAY` | 30 seconds | 0–60 seconds |
| `ARMORED_IA_TOTAL_TIMEOUT` | 180 seconds | 1–600 seconds |
| `ARMORED_IA_API_TIMEOUT` | 90 seconds | 1–120 seconds |
| `ARMORED_IA_IMAGE_TIMEOUT` | 10 seconds | 1–30 seconds |

The generation deadline uses a monotonic clock and limits each subsequent
request timeout and wait to the remaining budget. Requests' timeout applies to
network inactivity; this is not a process watchdog. The optional image fetch
precedes the generation budget and has its own timeout. Responses are closed
after success and failure. Non-finite, invalid or excessive budgets fail before
any external call.

Malformed generated JSON remains a provider failure and is bounded by the same
budget. Local caption policy evaluation runs once on the returned candidate
batch. It does not silently request another batch when every caption is rejected.

`last_attempts` contains attempt number, status, HTTP status and retry delay only.
No credentials, request bodies, product/image URLs, headers or raw exception
messages are included. Exceptions persisted by the pipeline are similarly
sanitized. A received HTTP error is evidence of that response, not proof of the
account's remaining quota or of model availability at a later time.

## Durable stage behavior

When all allowed attempts fail, the pipeline retains the immutable original and
Vision metadata, persists the IA → RECOVERY event and leaves the caption empty.
The Coordinator's directed recovery re-enters IA. Only a generated batch accepted
by the central policy advances to SPEECH. The v5 Speech/Studio boundary must then
be completed before Hub can be considered. Recovery does not rerun Sync, Vision
or Stock merely because Gemini was unavailable.

The regression test closes and reopens the experimental SQLite database after a
503 exhaustion, then resumes IA with a valid response. It verifies caption
evidence, original bytes, persisted identity, the SPEECH boundary and no
publication record. Separate tests cover bounded waits, server guidance,
authentication/model rejection, interruption, malformed responses and response
resource cleanup.

Reference: [Google Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting).
