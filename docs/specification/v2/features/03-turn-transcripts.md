# 11.3 Turn transcripts — the raw request and response of every model call

- **Scope:** make the harness's own prompts inspectable. The event log keeps redacted one-line digests (`agent.step`, `agent.message`) — enough for a run tree, not enough to improve a prompt, because the request the model actually saw is nowhere in it.
- **One turn = one real gateway call.** The messages as sent, the raw completion text as returned, and the usage the gateway reported. Nothing is synthesized or re-rendered after the fact.
- **Emitted at the LLM seam.** `LlmClient.complete` records the turn and attributes it to the run that made the call through the ambient run context — so a call the harness composes itself (the PR orchestrator's delegated single-pass review) is recorded as the *harness-composed* prompt, not the runtime transcript it ignores.
- **Rides `agent_events`.** No new table: it streams over the existing SSE stream and replays over the existing paged run-events endpoint, inheriting the access rules those already enforce (v1 10.8, v1 §12).
- **Allow-listed payload, 200 000-char caps.** Nine keys; `messages[].content` and `response` clip at 200 000 chars and set `truncated`. Every other event type keeps its 2 000-char cap.
- **Still never stored:** provider credentials, credential-shaped strings (the redaction layer masks them), and the model's internal reasoning. **Not a streaming protocol:** a turn lands when the call returns, and nothing pairs or diffs two turns.

---

## 1. Goals / non-goals

**Goals**
1. Answer "what exactly did we send this model, and what did it send back?" from a stored run, later, without a rerun.
2. Make a prompt change reviewable: two stored turns of the same role are a diff of the prompt.
3. Cover every model call in a run tree uniformly — orchestrator, sub-agent, and the harness-composed calls in between — with no per-call-site instrumentation to keep in sync.
4. Cost the event log one predictable payload shape, not a new storage surface with its own endpoints, access rules, and retention.

**Non-goals**
- Token-level streaming, partial captures, or persisting a call that never returned.
- Cross-run prompt diffing or prompt versioning; the stored turns are the raw material for it, not the mechanism.
- Replacing `agent.step`/`agent.message` — those stay the readable activity log (`01-supervisor-subagents.md` §8).
- Tool payloads. Tool args and results stay on `agent.tool_call`/`agent.tool_result`; the messages already contain the tool calls the model made.

---

## 2. What one turn is

| Part | Meaning |
|---|---|
| Request messages | the exact ordered `{role, content}` list handed to the gateway, after the harness composed it |
| Response | the raw completion text returned by the gateway, unparsed |
| Usage | prompt / completion / total tokens and cost as the gateway reported them |
| Size | total characters across the request and response, so a reader can judge the turn's weight without loading it |

**Exactly one turn event per real gateway call.** A retry is a second call and therefore a second event: a review whose findings JSON fails validation and is repaired produces two `agent.turn` events on the same run. That is normal, not a duplicate, and it is what makes a repair loop visible.

---

## 3. The event

`agent.turn` is an ordinary `AgentEvent` — same `id`, `run_id`, `parent_run_id`, `seq`, `type`, `payload`, `created_at` — with an allow-listed payload built from typed fields, never from unvalidated model output.

| Key | Type | Notes |
|---|---|---|
| `model_id` | string | the catalog model that served the call, as resolved for the run's role |
| `messages` | array | `[{role, content}]` in send order; `role` ∈ `system` \| `user` \| `assistant` \| `tool` |
| `response` | string | raw completion text |
| `prompt_tokens` | int | gateway usage |
| `completion_tokens` | int | gateway usage |
| `total_tokens` | int | gateway usage |
| `cost_usd` | float | gateway cost for this call |
| `chars` | int | total characters across `messages[].content` + `response`, measured before clipping |
| `truncated` | bool | true when any message content or the response was clipped |

```json
{
  "model_id": "gpt-5",
  "messages": [
    {"role": "system", "content": "…"},
    {"role": "user", "content": "…"}
  ],
  "response": "…raw completion text…",
  "prompt_tokens": 1234,
  "completion_tokens": 56,
  "total_tokens": 1290,
  "cost_usd": 0.0123,
  "chars": 45678,
  "truncated": false
}
```

All nine keys are always present; usage the gateway did not report is `0`, not omitted.

---

## 4. Where it is emitted

The seam is `LlmClient.complete` — the one place a real call leaves the process. The recorder wraps it, so a call site cannot forget and a future call site is covered by construction.

Attribution comes from the **ambient run context**: the runtime publishes the run it is executing, and the recorder reads that run's id back from it (the event's parent comes from the run row, as it does for every other event). No call site passes a run id, so no call site can attribute a turn to the wrong run.

That matters for the calls the harness makes *outside* the model-driven loop. **The PR orchestrator's delegated review** does not replay a runtime transcript — it composes its own prompt (system prompt, objective, PR context) and calls the model. That composed prompt is what the turn records, which is exactly what a reader needs to improve; the transcript the runtime ignores is irrelevant to them. **V1.2's aspect sub-agents** and every later role get transcripts for free: they are ordinary runs on the same seam.

A call made with no ambient run (pre-flight's live model check) has no run to attach to and produces no event.

---

## 5. Why `agent_events` and not a new table

| Requirement | Already served by `agent_events` |
|---|---|
| Live delivery | the existing SSE stream, tagged with `run_id`/`parent_run_id` (v2 01 §7) |
| Replay / pagination | `GET /api/sessions/{id}/runs/{run_id}/events`, paged by `seq` |
| Access control | both are filtered by the viewer's own repository access (v1 10.8), and raw model payloads are restricted to authorized viewers (v1 §12) |
| Ordering, retention, pruning | per-run `seq` ordering and the workspace retention policy that already prunes events |

A new table would duplicate all four and add a second place for the access rules to drift out of step with the session reads. The event log is the run's single source of truth; a turn is an event of that run.

---

## 6. Cost

A turn row is the bulkiest payload in the system, by design: a prompt carries the PR context, capped only by the repo config's `max_diff_lines` (default 20 000 lines, v1 §11). One review's initial pass can dwarf every other event of its run combined.

Accepted, because the caps below bound a single row, retention prunes turns like any other event (v2 01 §8), and the payload is written once at the seam — never copied into `agent.step` or the run row.

---

## 7. Caps and what is never stored

| String | Cap |
|---|---|
| `agent.turn` message content | 200 000 chars each |
| `agent.turn` response | 200 000 chars |
| every other event type's strings | 2 000 chars (unchanged) |

Clipping is per string, never across the payload: a turn with 30 messages keeps all 30, each clipped independently, and `truncated` is set if any of them was. `chars` stays the pre-clip total, so a clipped turn still reports its true size.

Never stored, in a turn or anywhere else: **provider keys and base-URL credentials** (the seam records the request, not the transport's auth); **credential-shaped strings** in repo or PR content, which the redaction layer masks before persistence (v2 01 §10); and **the model's internal reasoning** — only the completion text the gateway returned.

---

## 8. What this does not do

- **No streaming.** A turn is written when the call returns; a call in flight has no partial row, and a cancelled call leaves no turn.
- **No cross-run diffing or prompt rewriting.** Two turns can be compared by a reader or a later tool, but nothing in the harness pairs them or rewrites a prompt from them.
- **No change to the run tree's readability.** `agent.step`/`agent.message` remain the summary a node detail pane shows first; a turn is opened deliberately.

---

## 9. Testing

- **Unit:** the payload carries exactly the nine allow-listed keys; per-string clipping at 200 000 sets `truncated`; `chars` is the pre-clip total; redaction runs before persistence.
- **Integration:** a scripted gateway call records one event on the calling run; a JSON-repair pass records a second on the same run; a call with no ambient run records nothing.
- **Replay:** a turn survives the paged run-events endpoint intact, in `seq` order, filtered by repo access like any other event.
