---
status: approved
issue: 60
intent: intent/2026-10-08-60-research-stale-window.md
---

# Spec: A slow research job is not run twice

## Measurement (intent outcome 3)

Docling, `ingest.to_markdown(…, max_pages=40)`, in the app image, on a
40-page cut (3.9 MB) of a public arXiv survey (2303.18223). No confidential
document was used.

| CPUs | Time |
| --- | --- |
| host, unlimited | 126 s, then 114 s |
| `--cpus 2` (the ECS worker: `worker_cpu = 2048`) | 473 s |

A PDF over 40 pages is rejected by Docling at once (`max_num_pages` fails the
conversion, it doesn't truncate), and `MAX_BYTES` caps a page at 5 MB, so a
40-page PDF is the worst case. The worst-case research job, on the ECS
worker:

- 8 such PDFs: about 63 min;
- network, bounded by the per-hop deadlines: about 10 min;
- one LLM call for the claims.

So about 75 minutes. The result goes on #60 as a comment.

## Design

A **heartbeat** (intent question 1). A fixed research window would have to
be over 75 minutes, so a dead worker's research job would wait that long
before anyone picked it up. With a heartbeat, every job keeps the 15-minute
recovery, and a live job is never stale.

`app/worker.py` `run_one()`:

- After the claim, start a daemon thread. Every `HEARTBEAT = 60` seconds it
  runs `update jobs set updated_at=now() where id=%s and attempts=%s and
  status='running'` on its own connection.
  - The `attempts` guard: a worker whose job was reclaimed stops refreshing
    the newer worker's job (the same rule as `finish()`).
  - `status='running'`: a tick after `finish()` changes nothing.
  - A failed tick (a DB blip) is caught and the next one tries again. The
    margin is 15 ticks per window.
- A `threading.Event`, set in a `finally` around the handler, stops the
  thread on every exit path: done, exception, and SIGTERM.
- This covers every job kind. Extract jobs (LLM on large documents) get the
  same protection.
- `CLAIM` is unchanged: crawls keep 6 hours and everything else 15 minutes.
  Crawls could drop to 15 minutes now, but that changes crawl recovery,
  which nobody asked for. I'll file it as a follow-up if you want it.

**No research time limit** (intent question 2). The measurement shows the
job is bounded (by the 40-page and 5 MB caps and the fetch deadlines). Only
a bug that hangs forever would keep a job heartbeating; see Risks.

## Alternatives rejected

- **A longer fixed window for research** (2 hours). One line, but a dead
  worker's job waits 2 hours, and the next time Docling gets slower the
  window is too short again.
- **Heartbeat from inside `research.run()`** between pages. A single 8-minute
  Docling call passes no checkpoint, so the gaps are uneven, and only
  research would be covered.
- **A per-job time limit** (`signal.alarm`). Docling's native code can't
  always be interrupted, and the measurement shows the job is bounded.

## Risks

- **A hung handler keeps its job forever.** Before, the job was reclaimed
  after 15 minutes while the hung worker kept running anyway, which is the
  double run this fixes. Now it stays `running` until the worker process is
  restarted, and the SIGTERM path requeues it. The heartbeat only proves the
  process is alive, not that it is making progress. Accepted. If it shows up,
  a job age limit can be added later.
- **Deploy:** new workers heartbeat and old ones don't. The claim query
  doesn't change, so mixed versions behave as today.
- **One more DB connection per worker**, once a minute. Negligible.
- **Hosts:** the worker only. No schema change. Rollback is a revert.

## Verification

- `docker compose build app && docker compose run --rm app pytest` is green.
- A new test in `tests/test_worker.py`, with `HEARTBEAT` patched to 0.1 s.
  The handler backdates its own job by 20 minutes, waits 0.3 s, and then
  tries `claim()` from another connection: it gets nothing, and `updated_at`
  is fresh. On today's code the claim returns the job, so the test fails.
- A test that the thread stops: after `run_one()` returns (done and
  exception cases), no thread named `heartbeat` is alive.
- The existing tests still pass, including
  `test_stale_running_job_reclaimed_fresh_one_not`, which proves a dead
  worker's job (no heartbeat) is still reclaimed, and
  `test_sigterm_requeues_and_gives_attempt_back`.
- The measurement is posted on #60.
