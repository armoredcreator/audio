# Targeted recovery and runtime validation

The offer auditor is a diagnostic tool. Its dedicated SQLite ledger does not
replace the Coordinator database, change item states, fetch Telegram messages,
download media, generate affiliate links, or publish anything. Keep its input,
ledger, exports, and quota evidence private.

## Exact product checks

`get_exact_product` accepts absence only when a successful response contains an
explicit `data.productOfferV2.nodes` list and that list is empty. Missing/null
envelopes and malformed nodes are technical failures. Authentication, quota,
and transient failures cannot populate Vision's negative-offer cache.

The auditor deduplicates exact product identities while retaining a relation
for every input content ID. It records a charged `IN_FLIGHT` attempt before
network I/O and never silently replays an interrupted attempt. Completed
negative checks expire after six hours; positive checks expire after one hour.
Technical outcomes have no negative-cache validity.

Example input (synthetic identifiers):

```json
{"content_id":"example_1","primary_cause":"HISTORICAL","canonical_product_identity":{"shop_id":"10","item_id":"20"}}
```

Preview/export without network:

```powershell
python scripts/audit_product_offers.py --manifest private-input.jsonl --ledger private.offer-audit.sqlite3 --export private-preview
```

When remaining quota cannot be verified, the executable observation is limited
to one read-only request, with automatic retries disabled:

```powershell
python scripts/audit_product_offers.py --manifest private-input.jsonl --ledger private.offer-audit.sqlite3 --execute --budget-id reviewed-observation --export private-results
```

Reusing that budget ID does not replenish it. A new export directory is required;
existing reports are preserved. Legacy no-node summaries without the response
envelope remain labeled as prior queries with unavailable response shape.

A larger run requires `--verified-budget private-budget.json`. Example schema:

```json
{"request_limit":5,"evidence":"Account quota reviewed for this bounded run","valid_until":"2030-01-01T12:00:00+00:00","interval_seconds":10}
```

The values above illustrate the schema and are not a Shopee quota policy. Use
the actual account/service evidence and an unexpired validity window. A budget
is capped at 1,000 requests and cannot be enlarged on resume. Authentication,
quota, malformed responses, transient failures, or observed zero remaining
quota stop the run. A server `Retry-After` longer than the bounded retry window
also stops execution instead of shortening the server's requested delay.
Never put credentials or authorization headers in the budget document.

## Original preservation and Recovery

Sync retains a complete original when its rename succeeds but the SQLite hash
commit fails. A retry checks the existing bytes against the stored digest, or
finishes the missing digest write, without downloading over the file. Stock
also checks an existing canonical original before returning `existing`.
Both components delegate hashing to `armored_core.integrity.sha256_file`;
compatibility wrappers retain their existing call interfaces.

Recovery remembers the interrupted IA state so caption generation is not
skipped. Coordinator-owned recovery leaves a validated v5 result at
`PUBLISHING`; the outer Coordinator decides when Hub may start. Generic recovery
does not opt a `WAITING_VISION` item into an unreviewed retry. Legacy standalone
Recovery compatibility remains covered separately.

The RVC entry points refuse an existing output, including an output equal to
the input. They do not delete a prior result to begin another conversion.

## RVC environment evidence and remaining migration gate

The deployment target is one repository-root `.venv` using **Python 3.11.9**
for the complete pipeline, including RVC. `.python-version` records the exact
target and the setup entry point rejects other interpreter versions before
installing packages. Python 3.10 results below are diagnostic comparisons only;
they are not a deployment candidate or an alternative runtime architecture.

A separate two-second Melody probe also passed using the existing shared
Python **3.11.9** dependencies and a writable package copy, with network
disabled: 79,200 finite samples at 40 kHz (1.98 seconds). Model and index
hashes remained unchanged. No packages were installed or removed. This proves
the exercised conversion path, not a clean dependency installation, a passing
`pip check`, full separation coverage, or subjective voice quality.

The shared Python 3.11 environment and a separate legacy Python 3.10 runtime
have different package sets. The observed shared environment has `rvc-python
0.1.5`, `fairseq 0.12.3.1`, and `omegaconf 2.3.0`. The distribution requires
`fairseq==0.12.2`, `omegaconf==2.0.6`, and NumPy `<=1.23.5`; six additional
unconditional metadata requirements were absent: FastAPI, ffmpeg-python,
Loguru, Pydantic, python-multipart, and Uvicorn. These are not optional extras
in the published package metadata. Several serve its web/API entry points and
were not imported by the exercised file-inference path.

The separate Python 3.10.11 runtime satisfies all 18 direct `rvc-python`
requirements, but its global `pip check` still fails because `opencv-python
5.0.0.93` requires NumPy >=2. Its pip 24.0 also warns about OmegaConf 2.0.6's
legacy `PyYAML>=5.1.*` metadata.

A disposable metadata package-set simulation excluding OpenCV reported zero
missing requirements and zero version conflicts. A two-second Melody inference
using a separate writable package copy, existing Python 3.10 dependencies, and
network disabled produced finite 40 kHz audio; model/index hashes were
preserved and OpenCV was not imported. The private exact-version candidate lock
and execution artifacts are retained locally.

This does not install or promote a new runtime. A reproducible Python 3.11.9
dependency set, resolver/package-metadata compatibility, separation, and the
full Coordinator path remain migration gates. The normal engine still resolves
the repository-root `.venv`; it has no silent legacy-runtime fallback.
