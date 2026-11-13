# Shared maintenance contract

The owner wants one system, not a competing cropper. Claude owns question_boundaries.py; ChatGPT owns workspace_client.py. Changes to the launcher must preserve the existing interactive question/answer-key flow. The cropper imported from the owner-approved bundle has SHA-256 adeb38640c19d18156456819e2d9a4e80d71b93fbcb15a89a3d73b03b0df41dc and was copied without algorithm changes.

Private papers: JGUPTA2511/modulus-practice-corner, paper-workspace branch, paper_jobs/<PAPER-ID>/. Read that branch's AGENTS.md and HANDOFF.md. ChatGPT writes handoff/chatgpt.json; Claude writes handoff/claude.json. Mac owns status.json. Avoid same-file concurrent edits; refresh the current SHA and never force-push. Use small separate code branches for significant changes, then merge tested fixes into this public main. The private app's main must remain unchanged without publication approval.

No source PDF, key, solutions, cropped exam, private token, or local login file belongs in this public repo. The uploader checks that the destination is the private repository before sending any file content.

The Mac launcher updates from this public repo, crops locally, and uploads directly through GitHub's API using gh's browser login. It does not clone/scan the large application repo. Workspace commits have skip tags; this public code repo and the private paper-workspace tree have no Actions workflows.

Structural checks verify sequential numbers, readable images, image references and nonempty source keys. They do not prove boundaries or solve questions. Answers are preserved verbatim; 1-4 may be numerical responses. Separate solutions PDFs are preserved but matching remains an integration task. Supported explicit mock profile: JEE Main +4/-1/0, 180 minutes. No unspecified paper receives that profile automatically.

The first authenticated Mac sync is still an installation-level check to perform on the owner's Mac. Passing local tests is not proof of a Mac login/upload or a student-facing deployment.

## Batch addition (Codex)

`workspace_client.py batch FOLDER` and the existing interactive launcher now discover one input subfolder per paper. Canonical filenames and optional paper.json select associations; no question/key guessing occurs. Completed exports are reused unchanged. Marking is explicitly selected for the batch, with mixed/per-paper overrides. Original cropper/cleaning code remains unchanged.

The shared paper lock now covers preparation, cropping, attachment and sync. Local work finishes before upload, so network retries reuse completed runs. Export identity and opt-in color settings are immutable; cached artifacts are checked before reuse/sync. Legacy attached manifests are retained for exact matching exports. Batch output and local JSON reports stay outside input folders; failures are isolated and no review tabs open in batch mode.

Codex owns `handoff/codex.json`, both the private root implementation-session note and per-paper notes when Codex actually works on a paper. It never overwrites Claude/ChatGPT notes or the Mac's status to claim deployment. The Mac uploader does not generate assistant notes. Private durable context: `paper-workspace:docs/MODULUS_PROJECT_CONTEXT.md`; read its latest version in future sessions. No app branch or question-bank changes are part of this batch feature.
