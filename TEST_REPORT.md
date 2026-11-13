# Login-gate correction — 13 November 2026

Current executed result: **125 tests passed** using `python -m unittest discover -s tests -v` on Linux. This retains 113 prior tests and includes three actual-Bash startup tests plus nine local-first processing tests. Python compilation, both launcher shell syntax checks and `git diff --check` passed. Client Git blob: `8459d55cefed8d8de92e0696d77a277dadadebb3`; runner blob: `f2ed07e8fa0063b9d0d56b3e4cd38be325a3fb51`.

The regression reproduces the observed interruption by providing a synthetic gh command that would consume the first input line as a credential answer if invoked. The actual Bash runner starts the local client with the original paper path intact and **never invokes gh**, brew, pip or venv creation when packages are available. Separate executions verify reuse of an installed .venv without system Python/Homebrew and reuse of installed Python packages without creating a new environment. Dependency/network programs are controlled stubs, not real Mac installation tests.

Local-first tests finish two synthetic papers and save both upload jobs without any foreground GitHub/API call, then drain those jobs through the simulated API without recropping. Unchanged repeats create no duplicate runs/commits. Tests also cover explicit-only Connect, noninteractive browser-login arguments, immediate idle-worker wakeup, refusal reason preservation and truthful local-only reporting if a safe queue cannot be saved.

Owner screenshots are evidence of a real Mac security prompt, successful package installation and a Git login question; they do not establish completed paper processing, private upload or a working end-to-end exam. This correction has not been observed running on the owner's Mac. Optional browser authorisation remains necessary for private sync when not already signed in. Cropper SHA-256 remains unchanged: `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`. No real-paper processing, paid service, hosted pipeline, portal build or live deployment was performed.

# Original filenames and durable upload queue — 13 November 2026

Executed in the Linux cloud workspace with synthetic fixtures only:

- `python -m unittest discover -s tests -v`: **114 tests passed** (70 existing/batch/job safety, 24 original-filename, 19 upload queue, 1 offline launcher).
- Python compilation for client, unchanged cropper and new tests; shell syntax for both launchers; `git diff --check`: passed.
- Tested client Git blob: `40d48a31225ea86ffda561b0936efbde8b6cff58`.
- Cropper SHA-256 unchanged: `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`.

New tests cover arbitrary PDF/ZIP names including spaces/non-ASCII, explicit numbered selections saved outside inputs with hashes, no repeated prompts for unchanged selections, changed inventory/hash refusal, mapping/ignore safety, symlink/FIFO rejection, unchanged filenames/bytes, original numerical zero/passage metadata, ambiguous associations never guessed, Word-only refusal and untouched Word originals beside PDFs. An actual offline CLI subprocess reuses a synthetic existing export without cropping.

Queue tests simulate real client/API failure paths to persist pending records, retry without recropping, avoid duplicate commits, continue other papers, preserve newer concurrent enqueue revisions, enforce private targets and safe paths, guard multiple drainers, pause retries during processing and persist bounded backoff. Worker timing is tested with controlled events/mocks. Existing per-paper locks and integrity checks remain in force.

An actual Bash subprocess exercised a temporary launcher with stubbed programs, failed update/login commands and installed local client/venv. It reached interactive processing despite GitHub sign-in failure. This proves the shell fallback logic under simulation, not real Homebrew installation/browser login on a Mac.

No real source papers, real paper transfers, actual Mac operation, new cropper execution, Actions processing, paid API calls, portal promotion or deployment were performed. Queue records resume while the launcher is open/idle or on its next start; no external scheduler/service was installed. GitHub Actions minutes are not required for local cropping or GitHub API file uploads. Earlier workflow-setting access limits remain below. The screenshot supplied by the owner showed an older waiting prompt with many flagged crops; it is not evidence that the current updater/queue ran or that those crops are usable.

# Earlier batch verification — 13 November 2026

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
