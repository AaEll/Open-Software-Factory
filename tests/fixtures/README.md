# Recorded engine responses

Real Fireworks chat-completion bodies, captured once so the integration tests can exercise the
engine adapters — prompt assembly, JSON extraction, tool-call parsing, the sandboxed write — with
no API key, no network, and no credits spent per run.

`tests/test_integration.py` replays them through an `httpx.MockTransport`, the same technique
`tests/test_github_forge.py` uses for GitHub.

| Fixture | The call it answers |
|---|---|
| `clarify.json` | the driver's clarifying questions for "a landing page for my dog Pobrecita" |
| `plan.json` | the plan proposed from that request plus the answers (note: fenced ```json — the parser has to cope) |
| `plan_revised.json` | the plan after the feedback "also add a photo gallery page" — two steps |
| `worker_index_write.json` / `worker_index_done.json` | a worker turn: the `write_file` tool call for `index.html`, then the turn that stops |
| `worker_gallery_write.json` / `worker_gallery_done.json` | the same for `gallery.html`, so a two-step plan can be driven to merge |

## Re-recording

Only when the prompts or the expected shapes change — it spends real credit:

```bash
python -m evals.record_fireworks     # keeps existing files; delete one to re-record just it
```

Recorded fixtures are kept deliberately: they pin the *shape* of a real reply, including the
awkward parts (code fences, an empty `content` beside a tool call) that a hand-written stub would
tidy away.
