# Live findings (phase 0, deployed 2026-10-03 ~01:00, image from main c89753de3+batch7)
L-01 worker: "RuntimeError: Event loop is closed" (38/10min) + dispose_task_engine "got Future attached to a different loop" — some task path still reuses engines across loops (TX-LEAK follow-up).
L-02 worker: "source <id> has no collection_id; nothing can be indexed" repeated every sync tick (24/10min) — legacy sources must be flagged once (status + reason), not retried forever.
L-03 workflow-worker: WorkerLostError SIGKILL (jobs 91,97,98,99) — check memory limit (1GiB) vs new image footprint (torch/ColBERT prefetch in workers?).
L-04 run_forever: after reboot the launchd API bound :8000 before colima — fixed (API gate + docker abs path + DOCKER_HOST in plist).
