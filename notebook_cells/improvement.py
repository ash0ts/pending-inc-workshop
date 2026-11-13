# %% improvement


def improve(client, number, phase, execute, hypothesis):
    # The editor can edit harness files only. It cannot edit your frozen tools,
    # executor, final fixtures or final scorer. The workbench runs one phase per click.
    if phase == "propose":
        return client.edit(number, "propose", hypothesis)
    if phase in {"probe_a", "probe_b"}:
        branch = phase[-1]
        decision = client.snapshot()["decisions"][f"r{number}_{branch}"]
        # Task: approve THIS fingerprint after inspecting its diff, then execute
        # the same development case with decision['harness'] in this branch.
        raise NotImplementedError(
            "Approve the saved candidate, then call execute(case, stage, harness)"
        )
    if phase == "compose":
        return client.edit(
            number,
            "compose",
            "Compare both probes. Retain, select or combine changes supported by the evidence.",
        )
    if phase == "execute":
        decision = client.snapshot()["decisions"][f"r{number}_composed"]
        client.approve(f"r{number}_composed", decision["fingerprint"])
        return execute(
            "double-charge" if number == 1 else "missing-seat",
            f"r{number}_composed",
            Harness(**decision["harness"]),
        )
    if phase == "reflect":
        return client.edit(
            number, "reflect", "Save a cited lesson and a condition where it does not apply."
        )
    raise ValueError("Unknown phase")


# %% final


def evaluate_versions(client, execute):
    snapshot = client.snapshot()
    versions = [
        ("eval_original", snapshot["baseline"]),
        ("eval_manual", snapshot["manual"]["harness"]),
        ("eval_frozen", snapshot["freeze"]["harness"]),
    ]
    for index, case in enumerate(snapshot["heldout"]):
        # Interleave versions; every execution starts from its own original state.
        for stage, harness in versions[index % 3 :] + versions[: index % 3]:
            execute(case["id"], stage, Harness(**harness))
    client.grade()  # independent final state scorer, after all 12 runs
    client.evaluate()  # fixed final explanation rubric; unavailable stays missing
    return client.snapshot()
