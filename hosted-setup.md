# Start the Pending, Inc. workshop

Participants need a Forge account with hosted Notebook/Sandbox access, a project they can write, and two workshop tokens from the facilitator. They need no local install for the hosted path.

[Open the participant template](https://forge.coreweave.com/notebooks/Tm90ZWJvb2s6MTU2ODc=) · [Laptop checkout](https://github.com/ash0ts/pending-inc-workshop)

## Participant steps

1. Sign in to Forge. Open the template and choose **Copy to project**. Choose a project in your own team, or the workshop team your host has explicitly invited you to. One person makes and operates each pair's copy.
2. Open the copy in **Edit** mode. Wait for the runtime. Click the bottom-right Run button once if the office map and exercise controls have not appeared. This initializes the free cells; model and Sandbox work has separate run buttons.
3. Find **Clock in**. The project field should already contain the destination of your copy, in `team/project` form. The gateway URL is also filled in. Keep both values unless the facilitator supplies a correction.
4. Paste the **pair token** and **gateway access token** from your private access sheet into their password fields. Click **Check connection**. The receipt must show your pair and `pending-v4-code-r2`.
5. Click **Send my first Forge trace**, then **Open my setup conversation**. Confirm the tool event appears in Agent Lens. Forge provides the runtime credential automatically; do not paste an API key into notebook code.
6. Continue to **Equipment was not included**. Implement `lookup_invoice`, run its local checks, complete the tool-observation handoff, and register the code in a Sandbox. The remaining chapters instrument, evaluate, and improve that employee.
7. Save the notebook before leaving. Keep your copy's URL. Completed model executions are reused after reconnection; publication retries do not rerun the agent.

The public template grants reading and copying access. It does **not** grant membership in W&B's internal team or the entitlement to launch a hosted runtime. If your own team cannot launch one, use the laptop path below with a writable Forge project and the same two workshop tokens.

## Host checklist

- Share the template link and one private access sheet per pair. Each sheet contains that pair's token and the endpoint access token. Never publish these sheets in the template, repository, chat recording, or slides.
- Confirm each pair has a writable project and hosted Notebook/Sandbox access. There is no automatically assigned team project. If you want a shared workshop team, create/invite members explicitly; do not assume attendees can write into `wandb`.
- Ask one attendee to copy, launch, connect, and open their setup trace before the room begins. The operator account has completed these checks; an external attendee account still needs this permission check.
- The deployed gateway is a protected CoreWeave service, independent of the facilitator's laptop. Its current window ends **November 12 at 5:59 p.m. Eastern**. Stop or renew it deliberately after the workshop; the endpoint is not permanent.
- The gateway enforces pair isolation and model quotas. The $300 workshop ceiling remains shared. Private state backups are stored separately from participant material.
- A 25-pair timing test and human learning rehearsal are still outstanding. Do not describe single-pair qualification as a load test.

## If EvalTables is unavailable

Native EvalTables returned HTTP 500 in both API and hosted-browser checks. Continue the workshop: final publication displays a case-by-case score table in the notebook and links saved Weave evaluations, Agent Lens traces, and W&B evidence. Those are the fallback comparison surfaces, not a claim that native EvalTables succeeded.

Keep the saved executions and retry **publication** after service recovery. Do not rerun the agent to repair a dashboard. A missing judgment stays missing in the table.

## Laptop fallback

```sh
git clone https://github.com/ash0ts/pending-inc-workshop.git
cd pending-inc-workshop
uv sync --frozen
uv run wandb login
uv run marimo edit office_notebook.py
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first. Use Python 3.11–3.13 through the lockfile. No Docker, Node, Electron, or local gateway is required. Enter a writable `team/project` destination in the connection form and use the host's gateway and tokens. Laptop authentication uses your W&B account; the gateway provides the workshop model quota.

## Troubleshooting

| What happens | What to do |
|---|---|
| Can't view or copy the template | Sign in and check the template URL. |
| Destination project is missing | Choose a project your account can write, or ask its team administrator for access. |
| Hosted notebook cannot start | Check Notebook/Sandbox access in the destination team; use the laptop fallback if unavailable. |
| Project is only `test-workshop` | Use `your-team/test-workshop`, not a URL. |
| Gateway returns 401/403 | Check both workshop tokens. These are separate from Forge authentication. |
| Gateway is unreachable | Check its lifetime and ask the host for status. Never use the host's `localhost` in a hosted notebook. |
| Trace export stops | Retry export from the saved audit record. Do not rerun inference. |
| Native EvalTables returns 500 | Use the notebook table and Weave links; retry publication later. |

## Operator records

The standalone operator repository holds the deployment script, private access files, and validation receipts. They are deliberately excluded from this public participant checkout. The operator's private access-sheet directory is `.context/hosted-gateway/access-sheets/` in that repository. The source and private grader are not part of `wandb/core` product code.
