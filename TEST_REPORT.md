# Flat same-ID companions — 13 November 2026

Current result: **229 tests passed** using `python -m unittest discover -s tests -v` in Linux (189prior +24 companion grouping +9 pipeline +7 unchanged-key/heading tests). Python compilation, both shell syntax checks and `git diff --check` passed. Tested client blob: `2effda9b5870f374bef3ff4dcb0352f5ec2cbc57`. Cropper SHA is unchanged: `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`.

A real owner screenshot showed every loose QP/key/solutions file held for lack of per-paper folders. Synthetic regressions now group exact IDs, prefer Final keys, use combined fallback for both roles, preserve source bytes and existing configured/canonical folders, reject ambiguity/duplicates/orphans/unsafe inputs, and recognise case/Unicode dash spelling. Pipeline tests process each QP once with the correct chosen key, save supplied solutions as unmatched, preserve numerical zero and existing ZIP exports, reuse immutable legacy manifest bytes/crops, and retry simulated uploads without new crops/commits. Identical combined content is sent as one Git blob for its two canonical references.

Three tests execute the real unchanged key_from_file with mocked page rendering/text:15keys with zero preserved, solution-only page skipped, per-page ten-key threshold and later-grid overwrite behaviour. Four cover Main/Advanced paired-parenthesis titles and ambiguity. A further pipeline test keeps ambiguous PDF intent unresolved despite Main export metadata. None of these is real PDF OCR, actual Mac operation, independent answer verification or Advanced grading completeness evidence. Test dependencies now include the cropper's existing NumPy import.

No source paper was fetched/processed in this cloud workspace, no cropper/cleaning algorithm was modified, and no app, bank, workflow, billing plan or live-site deployment was changed. Earlier Advanced source/type/completeness holds remain necessary.

# Mixed paper inventory, source evidence and interface intent — 13 November 2026

Current result: **189 tests passed** with `python -m unittest discover -s tests -v` on Linux:145 prior,15 complete-file inventory,18 source evidence,11 mixed-policy integration. Python compilation, both shell syntax checks and `git diff --check` passed. Client Git blob: `01e8a8c5151af1e43a6329cffaa318eeed6f4b0f`. Cropper SHA remains unchanged `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`.

New cases cover every loose/nested candidate in an explicitly declared complete-paper collection, stable IDs, unsafe/corrupt/oversized inputs, duplicate/PDF-export ambiguity, Word sidecars without blocking their PDF copy, source-title/export metadata classification, verbatim instruction snippets with pages/hashes, source/evidence limits, unknown scans/conflicts, separate Main/Advanced1/2 intent, Main scoring only on Main sources, no default Advanced part/rules/duration, unchanged existing exports and numerical zero, immutable legacy cache reuse, refused-paper isolation and actual offline CLI intake. Synthetic cropper/API calls do not prove the cropper supports Advanced layouts.

Read-only development audit at `a23c770ec4c86560f05b64d7aab4058574460618` confirmed existing pattern-specific instructions, answer controls and Advanced grader. Existing pure Advanced-grader Node tests passed in a temporary copy; this is separate synthetic evidence, not a browser exam or deployment test. No app files changed. The Main/Advanced patterns share a renderer shell but use different behaviour; batch metadata intent does not itself switch or deploy it.

The actual unchanged cropper was audited, and synthetic key/gate probes showed real limitations: multiple-correct keys/fractions are not generally parsed, numerical-heavy inputs can fail the option threshold, and restarted subject/section numbering can lose questions/overwrite keys. New mixed jobs/checks/status explicitly hold exam launch pending source-scoped completeness/type/marking validation. Source text evidence is collected, not converted into independently verified Advanced grading rules. No claim is made that arbitrary Advanced papers are now supported completely.

The owner confirmed sources are only on the Mac; no source PDFs for this folder were present in the private workspace at inspection. No actual folder contents, real-paper crop/upload, native Mac GUI or Main/Advanced exam run was verified in this task. No new paid AI, cloud processing, Actions pipeline, portal build or deployment occurred.

# Finder paths and native folder selection — 13 November 2026

Current result: **145 tests passed** with `python -m unittest discover -s tests -v` on Linux (125 retained tests plus20 new Finder/chooser regressions). Python compilation, both shell launcher syntax checks and `git diff --check` passed. Tested client Git blob: `b6075658268332ba7dec9e53cd67d05742767806`. Cropper SHA-256 remains `adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc`.

The owner supplied a real Mac screenshot of repeated path-reader failure. The code defect was trimming raw input before shlex could decode an escaped trailing space, leaving a dangling backslash. Tests generate folders ending in one/multiple spaces and cover Finder separators, existing literal/quoted Unicode/apostrophe/backslash paths, unique legacy recovery, ambiguous/multi-path rejection, symlink identity and shell-expression nonexecution. Interactive tests prove the main prompt now passes raw input unchanged.

The main Mac prompt also offers Enter for a standard Choose Folder dialog through fixed osascript arguments. Tests mock chooser output/error/cancellation and verify literal path preservation and quiet cancellation. No user text is executed as shell/AppleScript code, and blank optional key/solutions inputs retain their meaning. Native macOS dialog operation and a completed real-paper Mac run have not been observed; the tests are synthetic/mocked. No cropper, upload protocol, app branch or deployment was changed.

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
