"""Named scorers over saved outputs. Publication never reruns the employee."""

import weave

from .evaluation import score_review


class OfficeState(weave.Scorer):
    name: str = "office_state"

    @weave.op
    def score(self, output: dict) -> dict:
        return output["execution"]["scores"]


class ExplanationReview(weave.Scorer):
    name: str = "explanation_review"

    @weave.op
    def score(self, output: dict) -> dict:
        return score_review(output["judgment"].get("outcome"))


class ExecutionBehavior(weave.Scorer):
    name: str = "execution_behavior"

    @weave.op
    def score(self, output: dict) -> dict:
        r = output["execution"]
        return {
            k: r.get(k) for k in ["seconds", "tool_errors", "observed_usd", "bounded_unknown_usd"]
        }
