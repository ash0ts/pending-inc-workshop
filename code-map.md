# Open the employee's handbook

The notebook builds the employee, its checks, and the editor that changes its harness. Use the **Office Lab source** selector near the end of the notebook to read the code without leaving marimo. The viewer shows the source bundled with that release. The editable cells show your own changes.

## Follow one ticket

1. **Instructions and skills.** `Harness.files()` turns the settings into instructions, skill files and workflow rules. `build_messages()` puts the task and tool guide into the model's context. A skill affects a request only after its contents are loaded.
2. **Model request.** `agent_step()` asks for the next action. Your `dispatch_tool()` sends its name and arguments to the gateway.
3. **Sandbox execution.** The gateway runs the registered tool source against that attempt's office state and saves the returned observation. Your `append_observation()` puts it into the next model request.
4. **Agent Lens conversation.** Your Forge scopes export the saved requests and events with their original timestamps. Stored-span readback checks them against the gateway receipts. This lab exports completed attempts, so a single unfinished step is still shown locally.
5. **Evaluation.** Your development scorer checks account state; an LLM judge checks the explanation. Weave and native EvalTables receive the same case identity, outcome and trace link. Unavailable native publication keeps the Weave/W&B evidence and can be retried.
6. **Harness edit.** The manual edit and the Propose–Probe–Compose controller change the instructions, skills and bounded rules. Tools and executor code stay frozen for the comparison.

## Read the code

| File | Start here |
|---|---|
| [office_notebook.py](office_notebook.py) | The complete runnable notebook; exercise code is visible in Edit mode. |
| [Harness](office_lab/harness.py) | `Harness`, `files`, `build_messages`, fingerprints and diffs. |
| [Employee exercises](notebook_cells/employee.py) | Instructions, invoice tool, dispatcher and model/tool loop. |
| [Office simulator](office_lab/simulator.py) | Billing, account access, idempotency and the committed-write timeout. |
| [Forge instrumentation](notebook_cells/instrumentation.py) | Conversation, turn, model spans and the tool-span exercise. |
| [Evaluation](notebook_cells/evaluation.py) | Development checks, judge validation, Weave logging and native EvalTables writes. |
| [Improvement controller](notebook_cells/improvement.py) | Propose, probe, compose, execute, reflect, then repeat. |
| [Gateway client](office_lab/client.py) | Requests and durable execution receipts. No provider credentials. |

The files above are public teaching code. The participant repository excludes held-out fixtures, final graders, budget controls and host credentials. Editing a file in this reference directory does not replace an already registered runtime; implement the exercises in the notebook.

## Keep Forge beside the notebook

In **Clock in**, send the environment trace and open it in Agent Lens. While building the invoice tool, implement its span. Registration then exports the real Sandbox test, before any employee model call. When the original, manual or candidate run finishes, its result offers a verified conversation link.

For each attempt, find one tool call and read what the model did next. During evaluation, follow a result back to that conversation and its executed harness. A tool error and an incorrect account state are different observations.

Export failures do not discard the execution. Repair the instrumentation and use **Export / verify my Sandbox tool test**, **Export / verify my original execution**, or **Inspect saved branches and their evidence**. These actions export saved work without repeating inference.

## Existing copies

The revised template does not overwrite an open notebook. Finish an active run in your current copy. Save your edited notebook and `.pending-code/journal` before updating. Reconnect with the same attendee token and project to reuse gateway executions. Keep your registered executor and tool source unchanged after the baseline starts.
