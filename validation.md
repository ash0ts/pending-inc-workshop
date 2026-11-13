# Release checks

The self-service release was checked on November 12, 2026.

- Clean GitHub Actions jobs installed the locked dependencies, ran the local-runtime tests, and rendered the notebook on Windows, macOS, and Linux. [Validation runs](https://github.com/ash0ts/pending-inc-workshop/actions/workflows/check-setup.yml).
- A live technical rehearsal used the public package, local Python tools, real local MCP, and the pinned W&B Inference model. Both improvement rounds finished, including composition and reflection. All 12 final executions and all 12 explanation judgments completed.
- Agent Lens stored-span readback passed for the model executions and judges. The tool-registration check has local receipts; the notebook's instrumentation exercise exports its own tool spans.
- Weave evaluations, W&B runs, harness artifacts, and a final Report were published to a separate rehearsal project. Publication made no new inference requests.
- Native EvalTables returned HTTP 500. The notebook preserved the comparison and Weave results and labeled native publication unavailable. This service issue is not presented as a successful native comparison.
- The rehearsal recorded $0.032022 for 145 provider attempts at the saved prices, with no unknown or in-flight costs. That is one run's recorded cost, not a quote for another account or run. The default workspace stop limit remains $8.
- Reopening completed work and restarting the local service preserved execution identities and accounting without duplicate requests.
- Headless Chrome checks covered connection, the first Forge trace, invoice-tool feedback, timeout recovery, the source viewer, and a narrow-screen layout.

The original, manually edited, and recursively edited harnesses each passed all four final state checks in this rehearsal. This checks the teaching workflow; it does not demonstrate that self-improvement increased quality.

The live rehearsal used the maintainer's credentials in a new project. It does not establish that a newly created account already has inference credits or every Forge permission. Each attendee's setup check verifies their own destination. This was a technical rehearsal, not a usability study with workshop participants.
