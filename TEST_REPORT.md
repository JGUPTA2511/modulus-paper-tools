# Batch verification — 13 November 2026

Executed in the assistant's Linux cloud workspace, using synthetic fixtures only.

- `python -m unittest discover -s tests -v`: **70 tests passed** (25 existing, 26 batch, 19 job-safety regressions).
- `python -m py_compile workspace_client.py question_boundaries.py tests/test_batch.py tests/test_job_safety.py`: passed.
- `bash -n Run_Question_Boundaries.command run_core.sh`: passed.
- `git diff --check`: passed.
- `batch`, `run` and `attach` help commands: executed successfully.

Tested client Git blob: `34f6eaa9f33c71ed4631cf56185e9fb8237da75b`.
Unchanged cropper SHA-256: `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`.

## Coverage and evidence boundaries

Batch tests cover multiple input subfolders, separate/embedded-key command routing, optional solution originals, existing exports copied unchanged, shared and mixed profiles, per-paper settings, duplicate normalized/custom IDs, changed originals/exports, Finder paths with spaces, corrupt and oversized PDFs/ZIPs, ambiguous roles, traversal, input/output overlap, symlinks and changing input inventories. All associations are discovered before processing.

Failure/retry tests exercise one paper failing while others finish, upload failure followed by reuse of completed crops, unchanged repeats without duplicate runs/commits, and final outcome/report generation. Source answers including numeric 0 and values 1–4, numerical type metadata and passage fields survive unchanged. Supplied solutions stay explicitly unmatched.

GitHub tests use an in-memory API simulator: private-target checks before blobs, immutable remote paths, unrelated concurrent edits retained, conflicting same-path edits refused, skip tags and non-force ref updates. Local tests exercise shared and legacy file locks, saved-export integrity, original-input hashes before sync, manifest/run identity, safe run/report paths, and exclusive temporary JSON writes. Cropper subprocesses are simulated for orchestration tests; no new cropping algorithm is tested or introduced.

An actual CLI subprocess processed a synthetic existing export offline, produced a job/report and reused it on repetition. Interactive prompts and Mac review-tab suppression are simulated, not observed on macOS. PDF preflight parses synthetic valid PDFs with pdfplumber. Parser success cannot guarantee that arbitrary source layouts will crop successfully.

## Repository checks

Read actual public main `7a8626c` and private paper-workspace `62210d7` / development `8cda593` heads before implementation; read applicable README/HANDOFF/AGENTS. Development has no root HANDOFF.md. Inspected all eight requested development integration files without changes. The generic development importer currently infers choices from answers 1–4, so arbitrary batch promotion remains blocked pending source-backed types; the batch tool never calls it.

Live read-only GitHub checks found **zero registered workflows** in the public tools repository and no `.github` directory in the inspected private paper-workspace tree. The private application has existing registered active workflows; available workflow definitions were inspected and are branch/path-limited or manual. Two registered paths were absent from development/main at inspection. Repository-wide Actions permissions returned HTTP 403, and Netlify account-side branch settings were not inspected. Therefore no blanket claim is made that nothing can run. No workflow, schedule, deployment setting or app branch was changed. Workspace commits retain `[skip ci] [skip netlify]`.

Private project context/AGENTS/Codex documentation was transferred through the connected GitHub API. This is a real documentation write, **not** a real paper transfer test. No real papers were used, no accepted GTM-01 export was recropped, and no private material was committed publicly.

## Not performed

Mac installation, Homebrew setup, Finder interaction on a real Mac, browser authentication, real paper cropping/upload, mathematical answer verification, worked-solution extraction/matching, portal build/promotion and live deployment. Those are not established by these tests. No AI API, paid service, PDF Actions pipeline or Netlify deployment was used.
