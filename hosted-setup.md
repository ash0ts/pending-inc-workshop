# Run Pending, Inc. yourself

The complete workshop runs on your laptop. You write the employee's tools, inspect its work in Agent Lens, edit its harness, then build an agent that proposes and tests further changes. The accounts and payments are fictional. The model calls, traces, and evaluations are real.

You use your own Forge account and inference billing. There is no shared workshop gateway to renew.

## Before you start

1. Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs Python if needed. Python 3.11–3.13 is supported.
2. Sign in to [Forge](https://forge.coreweave.com). Create a project named `pending-inc` in your personal entity or a team where you can write. Copy its `entity/pending-inc` name from the project URL. This is your destination for inference usage, traces, and results.
3. Ensure your account has W&B Inference access and available credits or billing. A writable project alone does not grant inference access. No hosted Notebook or remote Sandbox entitlement is required for this local path.
4. Get your API key from [W&B authorization](https://wandb.ai/authorize). Enter it only in the login command below, not in notebook code, chat, or GitHub.

## Run the workshop

These commands work in macOS/Linux terminals and Windows PowerShell. Replace `YOUR_ENTITY` with your own entity or team name.

```sh
git clone https://github.com/ash0ts/pending-inc-workshop.git
cd pending-inc-workshop
uv sync --frozen
uv run wandb login
uv run python start.py --project YOUR_ENTITY/pending-inc
```

The browser opens your editable marimo notebook. Keep the terminal open. Click **Check my setup**, then **Send my first Forge trace**. Open that conversation in Agent Lens before running the employee.

Your project is filled in automatically. There are no attendee tokens or gateway fields to copy. Opening the notebook and editing code do not send model requests. Explicit run buttons do.

The notebook contains starter exercises. Read each task, change its Python cell, and run the local check. The **Need a hint?** and **Show a worked answer** controls are there if you get stuck. Use marimo's Run button once if the free cells have not initialized.

## Where the work happens

| Component | Location |
|---|---|
| Notebook, draft code, office state, and execution receipts | Your laptop |
| Invoice tool | Restricted local Python interpreter; no host files or network |
| Onboarding tools | Local MCP server using the fictional office |
| Fixed model and explanation judge | W&B Inference, billed to your selected project |
| Conversations and spans | Agent Lens in your project |
| Cases, evaluations, harness files, and Report | Weave, EvalTables, and W&B in your project |

Local tool receipts say `local-restricted-python`. This release does not create remote CoreWeave Sandboxes. The notebook still teaches isolation, source fingerprints, and MCP using the local backend.

## Spending and saving

The default allowance is **$8 for the entire local workspace**, including previous calls, retries, and reserved unknown costs. It is a stop limit, not a promise that the workshop costs $8. You can set your own limit:

```sh
uv run python start.py --project YOUR_ENTITY/pending-inc --budget-usd 12
```

Stop an already-running launcher with Ctrl+C before changing its budget or credentials. Provider prices and settings are saved with the workspace; usage and costs remain visible. The model is `zai-org/GLM-5.3-Flash`. A missing model or unavailable inference is reported; the runtime does not switch models.

Your editable copy is `.pending-local/my_workshop.py`. The local database, execution receipts, notebook journal, and connection credentials stay under `.pending-local/`, which is ignored by Git. Do not share that directory: it contains your connection credentials and work. Restart the same command to resume. Updates to the template never overwrite your existing notebook copy.

To start another experiment, use a new directory:

```sh
uv run python start.py --project YOUR_ENTITY/pending-inc --state .pending-local/experiment-2
```

A workspace stays bound to its original project. Use a new `--state` when changing projects. Stop the previous launcher before starting another, or supply unused `--port` and `--gateway-port` values. Both services listen only on this laptop.

## What the final comparison means

The workshop includes two development cases and four final cases. You compare the starting, manually revised, and recursively revised harnesses once on each final case: **12 final executions**.

The final cases and grading code are public teaching materials. The agent does not receive them during improvement, and the controller waits for the freeze before final execution and grading. They are not a secret benchmark against a learner who can inspect the checkout. Development scorers you edit and the fixed final scorer are labeled separately.

## If something stops

- **No login or HTTP 401:** run `uv run wandb login --relogin`, stop the launcher, and restart it. If `WANDB_API_KEY` is set, update or unset that environment variable too; it takes precedence over saved login.
- **Project permission denied:** verify that `--project` names a project your account can write. Use a new `--state` to change projects without mixing saved work.
- **Inference access, credit, or model error:** resolve access or billing on your own account. Local exercises and saved receipts remain available. Do not change the model to make an existing comparison continue.
- **Port occupied:** stop your earlier launcher or choose unused `--port` and `--gateway-port` values. The launcher does not terminate other services.
- **Trace export interrupted:** retry export using the saved audit record. Exporting does not run the employee again.
- **EvalTables unavailable:** use the notebook's case-by-case comparison and saved Weave evaluations. Retry publication after service recovery; keep unavailable judgments missing.
- **A request stopped with uncertain usage:** its reservation remains charged locally. Completed work is not repeated. Read the saved error before retrying; do not delete the ledger to erase spending.

The service log is `.pending-local/gateway.log`; notebook errors are in `.pending-local/notebook.log`. Never publish logs without checking them for credentials and private work.

## Materials

- [Workshop slides and interactive deck](https://github.com/ash0ts/pending-inc-workshop/releases/tag/workshop-2026-11-12)
- [Current releases and downloadable code](https://github.com/ash0ts/pending-inc-workshop/releases)
- [Hosted notebook preview](https://forge.coreweave.com/notebooks/Tm90ZWJvb2s6MTU2ODc=)
- [Office code map](code-map.md)
- [Release checks and known limitations](validation.md)

A hosted copy cannot reach a service on your laptop at `localhost`. Use the checkout instructions above for the independent workshop. The hosted preview and the November 12 recording remain reference materials.
