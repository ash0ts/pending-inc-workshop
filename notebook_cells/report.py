# %% report


def write_report(project, conclusion, links, comparison=None):
    import wandb_workspaces.reports.v2 as reports

    entity, name = project.split("/")
    report = reports.Report(
        entity=entity,
        project=name,
        title="Pending, Inc. · Our harness experiment",
        description="Original, manual and recursively revised harnesses on the same office cases.",
    )
    report.blocks = [
        reports.MarkdownBlock(text="## What we found\n\n" + conclusion),
        reports.MarkdownBlock(
            text="[Open the aligned EvalTables comparison](" + comparison["url"] + ")"
            if comparison and comparison.get("url")
            else "Native EvalTables publication is unavailable. The saved Weave evaluations and W&B evidence are linked below."
        ),
        reports.MarkdownBlock(
            text="## Open the evidence\n\n"
            + "\n".join(
                "- [Evaluation run and harness]("
                + item["run_url"]
                + ") · [Weave evaluation]("
                + item["weave_url"]
                + ") · dataset: "
                + item["dataset"]
                for item in links
            )
        ),
        reports.MarkdownBlock(
            text="Three harness versions, four fresh cases per version. State correctness and explanation judgments are separate. This is a teaching adaptation of Harness²; model weights stayed fixed."
        ),
    ]
    report.save()
    url = report.url
    if url.startswith(f"https://forge.coreweave.com/{project}/"):
        url = url.replace("https://forge.coreweave.com/", "https://forge.coreweave.com/wandb/", 1)
    return {"url": url}
