# 10.9 Usage and cost tracking

- Cost comes from **LiteLLM-reported cost when available**, falling back to a **token × price table**.
- Usage is attributed **per model, per repo, per user, and per session**.
- Surfaced in a **usage page** (totals, breakdowns, and a time series), on the **session detail** page, as a line in the **PR comment**, and via an **API endpoint**.
- Tracking only; **enforcement (budgets) is deferred**.
