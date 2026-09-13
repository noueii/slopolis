# 10.2 Provider & model configuration (BYOK)

- Supported providers: **LiteLLM first-class**, plus **any OpenAI-compatible** endpoint (base URL + key).
- **Credentials live in the workspace vault**, encrypted (AES-256-GCM, envelope-encrypted with a master key from env/KMS); never logged in plaintext; never stored in a repo.
- **Model catalog:** import from the provider's model list (e.g. LiteLLM `/v1/models`) plus **manual model IDs**.
- **Model assignment** is workspace-level and configurable in the UI: agent role → model. `auto` resolves to the **workspace default model**.
- A **test-connection** action validates credentials and records last-known status.
- **Public vs private policy:** private repos require read access to trigger; **public repos require write access**. A per-repo override in the workspace UI may loosen or tighten this.
- **Stopgap caps (no cost accounting):** max concurrent sessions and max sessions per user per day.
- **Budgets are deferred** to a later phase.
