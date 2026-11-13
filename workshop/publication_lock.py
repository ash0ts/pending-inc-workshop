"""W&B and Weave publication share process-global SDK context."""

from threading import RLock

# Inference and Agent Lens readback do not take this lock. Curation and result
# publication do: a concurrent wandb.init/finish can unset another thread's Weave client.
PUBLICATION_LOCK = RLock()
