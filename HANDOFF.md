# Shared maintenance contract

The owner wants one system, not a competing cropper. Claude owns question_boundaries.py; ChatGPT owns workspace_client.py. Changes to the launcher must preserve the existing interactive question/answer-key flow. The cropper imported from the owner-approved bundle has SHA-256 adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc and was copied without algorithm changes.

Private papers: JGUPTA2511/modulus-practice-corner, paper-workspace branch, paper_jobs/<PAPER-ID>/. Read that branch's AGENTS.md and HANDOFF.md. ChatGPT writes handoff/chatgpt.json; Claude writes handoff/claude.json. Mac owns status.json. Avoid same-file concurrent edits; refresh the current SHA and never force-push. Use small separate code branches for significant changes, then merge tested fixes into this public main. The private app's main must remain unchanged without publication approval.

No source PDF, key, solutions, cropped exam, private token, or local login file belongs in this public repo. The uploader checks that the destination is the private repository before sending any file content.

The Mac launcher updates from this public repo, crops locally, and uploads directly through GitHub's API using gh's browser login. It does not clone/scan the large application repo. Workspace commits have skip tags; this public code repo and the private paper-workspace tree have no Actions workflows.

Structural checks verify sequential numbers, readable images, image references and nonempty source keys. They do not prove boundaries or solve questions. Answers are preserved verbatim; 1-4 may be numerical responses. Separate solutions PDFs are preserved but matching remains an integration task. Supported explicit mock profile: JEE Main +4/-1/0, 180 minutes. No unspecified paper receives that profile automatically.

The first authenticated Mac sync is still an installation-level check to perform on the owner's Mac. Passing local tests is not proof of a Mac login/upload or a student-facing deployment.

## Batch addition (Codex)

`workspace_client.py batch FOLDER` and the existing interactive launcher now discover one input subfolder per paper. Original PDF/ZIP filenames are preserved. Canonical role names remain compatible; ambiguous roles are explicitly selected and hash-bound choices persist outside the source folders. Optional paper.json files/ignore mappings are supported; no question/key guessing occurs. Completed exports are reused unchanged. Marking is explicitly selected for the batch, with mixed/per-paper overrides. Original cropper/cleaning code remains unchanged.

The shared paper lock now covers preparation, cropping, attachment and sync. Local work finishes before upload, so network retries reuse completed runs. Export identity and opt-in color settings are immutable; cached artifacts are checked before reuse/sync. Legacy attached manifests are retained for exact matching exports. Batch output and local JSON reports stay outside input folders; failures are isolated and no review tabs open in batch mode.

Codex owns `handoff/codex.json`, both the private root implementation-session note and per-paper notes when Codex actually works on a paper. It never overwrites Claude/ChatGPT notes or the Mac's status to claim deployment. The Mac uploader does not generate assistant notes. Private durable context: `paper-workspace:docs/MODULUS_PROJECT_CONTEXT.md`; read its latest version in future sessions. No app branch or question-bank changes are part of this batch feature.

## Original-name and queue follow-up

The owner cannot rename source files. Never make canonical filenames a prerequisite. Numbered selections happen before cropping and are saved under local `.batch_selections`; input folders are unchanged. DOCX is unsupported directly; Word-only inputs are held, while Word originals beside a PDF remain untouched. A screenshot of an old launcher/results prompt does not establish current Mac installation or usable crops.

Actual API upload failures enqueue under local `.upload_queue`. The launcher retries idle uploads with persistent backoff, pauses new retries during local work, and resumes saved work on the next launch. Original/export processing is never repeated by the queue. Queue revisions preserve concurrent enqueues; existing private-target, immutable-file and job-lock checks still apply. Failed GitHub sign-in is nonfatal to local processing. No queue worker runs after the tool is closed and no hosted processing/deployment is created. GitHub Actions minutes are not consumed by local cropping or API file uploads.

## Startup interruption correction

Actual owner Mac screenshots showed successful package installation followed by a stalled Git credential question. Making failed login nonfatal did not remove this interactive gate. `run_core.sh` now makes no GitHub/auth call and does not require/install gh to start. It reuses an executable local .venv or an already-working Python; Homebrew is required only for missing local helper installation. Existing trusted launcher updates in place; do not routinely send the owner through another ZIP/Gatekeeper/setup cycle.

The normal interactive single/batch path completes local work, enqueues results/refusal reports and returns to the next paper. API/network waits happen in the idle upload worker. Explicit `g` connects with browser approval and noninteractive stdin, suppressing Git credential configuration questions; private uploads still require authorisation. New work wakes the idle worker, failures retain backoff, and original locks/integrity checks remain. CLI sync behaviour stays compatible. No accepted cropper, app branch, paper data or deployment is changed.

## Finder path correction and native folder choice

The owner reached the paper prompt but Finder paths ending in an escaped space were rejected. Preserve the raw main input and do not strip path text before shlex. Existing literal paths (including final spaces/backslashes) are accepted, and legacy dangling escapes recover only with one existing destination; no eval or ambiguous repair. The main prompt now offers Enter for a native Mac Choose Folder dialog. Blank key/solutions prompts retain their previous meaning. Native chooser output preserves path whitespace and source folders remain untouched. Reopen the trusted existing launcher for the update; avoid more downloads/renaming/Terminal path troubleshooting. This is input handling only; the cropper, queue and app branches are unchanged.

## Mixed Main/Advanced owner requirement

Owner confirmed the selected folder contains both Main and Advanced papers and all candidates must be accounted for. Main uses the explicitly selected +4/-1/0,180-minute profile; Advanced must obtain section-specific rules and time from its source. Main and Advanced exam-engine patterns/controls differ. Keep `jee-main` separate from `jee-advanced-1`, `jee-advanced-2`, `jee-advanced-both`; do not default an unknown Advanced part to Paper1. Vault underscore patterns require explicit mapping into these engine values.

The new explicit complete-file mode inventories loose/nested files without filename changes, while grouped companion-file associations remain supported. `x` selects source-backed family intent and Main-only standard rules. `jee-advanced-source` never receives requested Main scores/duration. Actual source snippets/page/hash are retained in optional job.exam_intent and new checks; source-scanned/ambiguous types remain unknown. Metadata intent does not itself route/deploy the portal. Integration/launch stays held for type, section-marking and source-completeness validation, even after structural cropping/upload.

Audits found cropper key grammar/options threshold/longest-chain limitations for Advanced. It can reject multi-correct/numerical-heavy inputs or lose reset-numbered sections; do not claim arbitrary Advanced support or completeness. The accepted cropper/cleaning is unchanged. Generic development importer still heuristically converts1–4 to choices; do not automatically promote these batches with it. The owner says these sources are only on the Mac; private workspace had no source PDFs at inspection. Existing local/queued handoff is needed before actual source validation.
