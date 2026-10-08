---
status: approved
issue: 42
author: olafkfreund
---

# Intent: Reproducible image builds from a CPU-torch lock

## Problem

`pyproject.toml` lists the Python dependencies without versions, and the
Dockerfile installs whatever is newest at build time. CPU torch comes first,
from the PyTorch CPU index; everything else comes from PyPI.

Two builds a day apart can therefore install different versions, and an
upstream release can break the build without any change on our side. That
happened today. rapidocr 3.10.0 dropped a name that docling 2.135 imports
during the model download step. Every fresh image build failed, including
CI on main after #68 and #69 were merged. PR #70 caps rapidocr as a stopgap.

There is also no record of which versions a deployed image contains, so a
bug can't be traced to a dependency change, and a rollback image can't be
rebuilt the same way.

A `uv.lock` once crept in by accident (removed in 1dea17a). It resolved
CUDA torch, which is several GB of GPU libraries the Fargate tasks can't
use.

## Proposed outcome

- Every image build installs exactly the versions in a committed lock file,
  including all transitive dependencies.
- The lock resolves CPU-only torch and torchvision.
- A rebuild of the same commit gives the same Python packages.
- Updating dependencies is one deliberate command, and the change shows in
  the PR diff.
- The rapidocr cap from #70 comes out, because the lock holds a working
  version.

## Affected users and systems

- `pyproject.toml`, `Dockerfile`, a new lock file, the README (how to update
  it), CI.
- The app, worker and web images on ECS (all three built from the same
  Dockerfile).
- Developers who add or upgrade a dependency.

## Constraints

- CPU torch only: Fargate has no GPU, and the image must not grow.
- Build-time internet stays as it is: the Docling models are still
  downloaded at build time. No runtime egress.
- No change to app behaviour: the lock starts from the versions in today's
  working image (docling 2.135.0, rapidocr 3.9.2, …).
- Prefer tools that already ship in the image (pip) over a new one, unless
  the spec shows the new one is needed for the CPU index.

## Open questions

1. **Tool.** One option is `pip-compile` (pip-tools) to a hashed
   `requirements.lock`, installed with `pip install --require-hashes`. The
   other is `uv lock` with the CPU index pinned for torch. The spec compares
   the two; I lean to whichever handles the second index for torch more
   simply.
2. **Dev dependencies.** Today the image installs `.[dev]` (pytest, moto),
   because CI runs the tests in that image. Should the lock cover dev too,
   and should production images drop it? I'd keep one image for now and
   leave the split as a follow-up.
3. **Keeping it fresh.** Should Dependabot (or a scheduled job) open
   lock-update PRs, or do we update by hand? I'd do it by hand for now.
4. **Apt packages** (LibreOffice and others) are also unpinned. I'd leave
   them out of scope: they come from Debian stable, which doesn't make
   breaking changes.
