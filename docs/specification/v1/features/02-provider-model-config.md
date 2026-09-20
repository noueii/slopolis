# 10.2 Provider & model configuration (BYOK)

- Supported providers: **LiteLLM first-class**, plus **any OpenAI-compatible** endpoint (base URL + key).
- **Credentials live in the workspace vault**, encrypted (AES-256-GCM, envelope-encrypted with a master key from env/KMS); never logged in plaintext; never stored in a repo.
- **Model catalog:** import from the provider's model list (e.g. LiteLLM `/v1/models`) plus **manual model IDs**.
- **Model assignment** is workspace-level and configurable in the UI: agent role → model. `auto` resolves to the **workspace default model**.
- A **test-connection** action validates credentials and records last-known status.
- **Public vs private policy:** private repos require read access to trigger; **public repos require write access**. A per-repo override in the workspace UI may loosen or tighten this.
- **Stopgap caps (no cost accounting):** max concurrent sessions and max sessions per user per day.
- **Budgets are deferred** to a later phase.

## Vault

`slopolis_core.vault.SecretVault` is the only code that sees a key in the clear.

- **Envelope encryption, AES-256-GCM.** Every secret gets its own random 32-byte data key; the
  data key is wrapped by the master key and stored beside the ciphertext
  (`0x01 | wrap_nonce | wrapped_key | data_nonce | ciphertext`, all part of one `bytea`).
- The master key is `ENCRYPTION_KEY` (base64, hex, or ≥32 raw bytes); a 32-byte key is derived
  with HKDF-SHA256 so any sufficiently long secret works. An unset or too-short key is a typed
  configuration error, surfaced as **503 `vault_not_configured`** — never a silently weakened key.
- Decryption failures (wrong key, tampered blob) raise; a credential that cannot be decrypted is a
  configuration error, not a 401.
- Plaintext never reaches a log, an audit row, or an error message; the API exposes `key_last4`
  only.

## API surface

Workspace-scoped, **admin-only** except the catalog read; every mutation writes an `AuditLog` row.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/providers` | — | `{items: ProviderCredential[]}` |
| `POST /api/providers` | `{provider, baseUrl?, apiKey}` | 201 `ProviderCredential` |
| `PATCH /api/providers/{id}` | `{baseUrl?, apiKey?, enabled?}` | `ProviderCredential` |
| `DELETE /api/providers/{id}` | — | 204 |
| `POST /api/providers/{id}/test` | — | `{status, detail, checkedAt}`, recorded on the row |
| `GET /api/catalog/models` | — | `{items: CatalogModel[], defaultModelId}` |
| `POST /api/catalog/models` | `{modelId, provider, displayName?}` | 201 `CatalogModel` (`source: "manual"`) |
| `POST /api/catalog/models/import` | `{credentialId}` | `{imported, items[]}` from the provider's `/v1/models` |
| `DELETE /api/catalog/models/{id}` | — | 204, addressed by catalog row id (model ids contain slashes) |
| `GET /api/catalog/assignments` | — | `{defaultModelId, roles: [{role, modelId}]}` |
| `PUT /api/catalog/assignments/{role}` | `{modelId}` (`null` = `auto`) | `{role, modelId}` |

- `ProviderCredential` = `{id, provider, baseUrl, keyLast4, enabled, lastStatus, lastCheckedAt,
  createdAt}` — never the key. `CatalogModel` = `{id, modelId, provider, displayName, source,
  credentialId}`.
- `/api/models` (the review composer's picker) is unchanged; the admin surface lives under
  `/api/catalog` so the existing contract and its consumers keep working.
- Roles come from `slopolis_core.roles.ASSIGNABLE_ROLES`; an unknown role is **422**.
- **`auto` is the absence of an assignment row**, not a stored sentinel. `GET` reports
  `modelId: null` for those roles, and `auto` resolves to the workspace's default model (the
  catalog's first entry, which is what `GET /api/models` already reports).
- A model id absent from the workspace catalog is **422 `unknown_model`**: assignment can only
  point at something the workspace has actually imported or added.
- `POST /api/providers/{id}/test` and the import call a LiteLLM / OpenAI-compatible
  `/v1/models` with the stored key; both are the only outbound calls this feature makes. A failing
  provider records `lastStatus: "failed"` and returns 200 with the detail — a provider being down
  is a status, not an API error.
- Non-admin callers get **403 `admin_required`**.
- Deleting a credential deletes the catalog rows imported through it; manual rows (`credentialId:
  null`) survive. Deleting a catalog row clears the assignments that pointed at it, so those roles
  fall back to `auto` instead of holding a model id nothing can serve. Both are audited.
- `POST /api/catalog/models/import` counts **newly created** rows in `imported`; a model already in
  the catalog is refreshed (provider/credential), not counted twice.
