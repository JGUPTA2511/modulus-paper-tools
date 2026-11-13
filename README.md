# Modulus shared paper tools

This public repository contains **code only**. Never upload question papers, answer keys, solutions, student data, crop images, or credentials here.

Claude maintains `question_boundaries.py` (the existing Mac cropper). ChatGPT maintains the original `workspace_client.py` private handoff; Codex added batch orchestration and retry protections around it. The existing `Run_Question_Boundaries.command` launcher is retained. Cropping and watermark reduction are unchanged and run locally, with no paid service or AI API.

Private workspace: `JGUPTA2511/modulus-practice-corner`, branch `paper-workspace`, folder `paper_jobs/<PAPER-ID>/`. Uploading here is **not** deploying an examination. App integration stays on `development`; live `main` is untouched.

## Batch routine on your Mac

1. Select a batch input folder **outside** `~/Modulus Papers`. If every PDF/ZIP is a complete paper, choose `a` when prompted to scan loose files and all nested subfolders. If there are separate keys/solutions or PDF/export pairs, keep their explicit per-paper grouping so associations are not guessed. The earlier grouped format remains supported:

   ```text
   Modulus Batch/
     GTM-02/
       Original question paper.pdf
       Original answer key.pdf
       Original solutions.pdf
     GTM-03/
       Another original paper.pdf
     GTM-04/
       Original result.zip
   ```

2. **Keep your original filenames. No renaming is required.** A single question PDF or a single structurally valid existing export ZIP can be recognised automatically. When several PDFs/ZIPs need association, the launcher lists them and asks you to select the question, separate key and optional solutions by number. Select an existing export to reuse it unchanged without recropping. Unused PDF/ZIPs require explicit acknowledgement; ambiguous roles are never guessed. Choices are remembered with file hashes in `~/Modulus Papers/.batch_selections/`, outside the inputs. Changed files require fresh selections and a new paper ID if originals changed. Canonical names such as `questions.pdf`, `answer_key.pdf`, `solutions.pdf` and `export.zip` still work.
3. Double-click your existing `Run_Question_Boundaries.command`. Let its code update finish, then **press Enter at the paper prompt to open Choose Folder** and select the batch folder. Dragging or pasting a folder/PDF/export path still works. Spaces, including literal trailing spaces in folder names, are preserved; source names do not need changing. Startup reuses this tool's installed Python environment (or an already-working Python); GitHub CLI and browser login are not startup requirements. Missing local cropper dependencies still need their first installation.
4. Complete any first-time file selections, then read the preflight summary. For a mixed Main/Advanced collection select **`x` = Main standard / Advanced from source**. Main title/metadata selects the owner-requested +4/-1/0, 180-minute profile; Advanced keeps source-specific rules unresolved until validated. Select `y` only for a Main-only mock batch, Enter for unspecified marking, or `m` for manual per-paper overrides (`y` Main, `a` Advanced source, `n` unspecified). No profile is inferred from GTM-01 or from answer values.
5. Leave the tool running. Papers process sequentially, without opening review tabs. One failure does not stop the rest. The launcher saves each completed paper for background sync and moves straight to the next paper. Its summary shows **local-only / upload queued**, **already processed**, or **held**, with reasons; a queued upload is not a deployment. Successful background uploads print **SYNCED** with the paper ID. Explicit CLI sync can still report **synced** or **upload failed/local-only**. It prints a local JSON report path under `~/Modulus Papers/.batch_reports/`.
6. Failed uploads are saved in `~/Modulus Papers/.upload_queue/` and retry automatically while the launcher is idle/open. Newly queued work wakes the worker when paper processing finishes; failed attempts back off from about a minute up to an hour. Cropping of other papers continues. Closing the window keeps pending work; the next launcher start resumes retries. Keep `~/Modulus Papers` intact. Completed crops are reused, and successful unchanged repeats create no duplicate run or workspace commit. No background retry runs after you close the tool; reopening it is enough.

Generated jobs live separately in `~/Modulus Papers/<PAPER-ID>/`; input folders are never used for outputs or modified. Paper IDs use uppercase letters, digits, `_` or `-`, up to 80 characters. Folder names are uppercased and spaces become `-`; collisions hold **all** folders with that ID. For any changed original, export, marking or color setting, use a new ID/version. Originals and completed runs are immutable. Existing locks protect processing, attachment and sync.

Solutions PDFs are **stored, not extracted or matched**. Source answers, numeric zero, question metadata, passages and original export bytes are retained. Structural checks do not independently verify answers, crop boundaries, question types or marking. Missing source keys may cause the unchanged cropper to refuse a paper. Transcription and portal promotion are later tasks, not prerequisites for image processing.

## Single-paper routine (preserved)

Run the same Mac launcher and drag one question PDF or existing result ZIP instead of a batch folder. Local work finishes first and its upload is queued, so connection/login waits do not block the next paper. For a PDF, give its code, supply a separate key and optional solutions at their prompts, then explicitly choose marking or leave it unspecified. `run`, `attach`, `sync`, and one-paper-per-iteration `--interactive` remain available.

## Optional command-line settings

```sh
python workspace_client.py batch "/path/Modulus Batch"
python workspace_client.py batch "/path/Modulus Batch" --profile jee-main-mock --paper-profile GTM-03=unspecified
python workspace_client.py batch "/path/Modulus Batch" --offline
python workspace_client.py sync "$HOME/Modulus Papers/GTM-02"
```

`--select-files` enables numbered file selection in the `batch` CLI; otherwise ambiguous input is held unless explicit/saved mappings exist. `--root` selects a separate output folder. `--keep-color` is opt-in; normal cropper cleaning is unchanged. Exit status is 1 if any paper is held or local-only; other papers still finish. Offline mode saves local results without GitHub calls.

For reusable mixed settings, an optional `paper.json` accepts `paper_id`, `profile`, `files` and `ignore`. For example `{"paper_id":"GTM-03","profile":"unspecified","files":{"questions":"Original paper.pdf","answer_key":"Original key.pdf"},"ignore":[]}`. File roles are `questions`, `answer_key`, `solutions` (PDFs) and `export` (ZIP); use basenames only. Every PDF/ZIP must be selected or explicitly ignored. No need to create this file when using the launcher: numbered selections are saved automatically. Supported profiles are `unspecified`, `jee-main-mock` and `jee-advanced-source`. The Advanced profile is a source-validation requirement, not a default scoring scheme. Per-paper settings override the shared selection; explicit CLI `--paper-profile ID=PROFILE` overrides both. Unsupported marking schemes must remain unspecified pending source-backed integration; never substitute the mock profile.

## Collaboration and limits

Read `HANDOFF.md` before changes. Private durable project context is in `paper-workspace:docs/MODULUS_PROJECT_CONTEXT.md`, referenced by that branch's `AGENTS.md`. Claude, ChatGPT and Codex own only their respective handoff notes. Mac processing owns `status.json`; its `ready_for_integration` state never means published. Never merge the whole workspace branch into an app branch.

Each input/export must be at most 24 MiB. Batch preflight parses PDFs and validates export structure before any paper processes. Corrupt/oversized inputs are held without truncation. Export archives also have a 256 MiB expanded limit. Runtime failures can still occur on PDFs that parse successfully. The generic development importer needs source-backed question-type handling before automatic promotion of arbitrary batch papers; this tool does not invoke it.

Local cropping and GitHub API file uploads **do not use GitHub Actions minutes**. An exhausted Actions budget does not block this pipeline. Failed network/auth uploads queue locally; GitHub browser approval may still be needed before private uploads can sync. Type `g` at the paper prompt only when you want to connect. This explicit action uses the browser flow with terminal credential questions disabled. Existing browser login is reused for background uploads; startup never launches login. If GitHub CLI is missing, only the explicit Connect action installs that free helper. No new Actions workflow, AI call or Netlify deployment is created. Skip tags are included on workspace commits. Existing application workflows and schedules are not disabled. See `TEST_REPORT.md` for observed checks and access limitations.

## Tests

Run `python -m unittest discover -s tests -v` with Pillow and pdfplumber installed. Fixtures are synthetic and generated in temporary folders. GitHub writes in tests are simulated. Linux test success does not prove Mac installation, browser authentication, real paper upload or deployment.

## An older Mac window or Word document

A screen saying “Drag your question paper (PDF)” with page-photo options is the older direct-cropper loop. A final results line followed by that prompt means the prior run finished and is waiting for another input, not that it is still working. Close that window and reopen the current launcher. Prefer the already-trusted launcher, which updates its code from this public main branch, instead of downloading another ZIP and repeating macOS security/setup steps. Keep your existing papers/results. The updated prompt includes “Enter: Choose Folder”.

DOC/DOCX is not an accepted processing format. Keep the Word original and supply a PDF copy with any filename; a Word original beside a PDF is left untouched. A Word-only input is held with an explicit reason. A large number of flagged crops is not proof of a usable exam: this uploader performs structural checks, not independent visual/mathematical verification, and it does not publish the examination.

## A window waiting at GitHub login

The earlier launcher started browser login before asking for a paper. It could wait indefinitely at “Authenticate Git with your GitHub credentials?” even though local processing did not need Git. That login gate has been removed. Cancel the old waiting process with Control+C and reopen the same `Run_Question_Boundaries.command`; its updater fetches this fix. No new ZIP, token paste or reinstall is required when the installed dependencies are working.

Drag the batch folder immediately. Papers process locally and uploads queue. You can type `g` later to approve the private GitHub connection in your browser, or keep processing. Private syncing still requires an authorised account; this tool cannot bypass that approval. The normal interactive path performs no synchronous upload between papers. The advanced `run`, `attach`, `batch` and `sync` CLI commands retain their existing explicit sync behaviour.

## Finder path rejected before processing

An earlier input reader trimmed whitespace before decoding Finder escapes. A folder name ending in a space could then appear to end with a dangling backslash and be rejected. The current reader preserves the raw input; a malformed legacy path is recovered only when exactly one existing destination matches. Several dragged paths or ambiguous repairs remain unselected.

For the simplest Mac routine, reopen the same trusted launcher to receive the update, press Enter at the paper prompt, and choose the batch folder in the normal macOS dialog. Cancellation returns quietly to the prompt. No path typing, folder renaming, AppleScript setup, or extra installation is required. Blank separate-key/solutions prompts still mean omitted/embedded as before. The dialog uses the built-in macOS osascript command and does not run a shell or interpolate the chosen path into code.

## Mixed Main/Advanced papers and their existing interface

For a folder of complete paper files, the `a` discovery mode inventories every loose/nested PDF or existing export ZIP, preserving source names and stable path-based paper IDs. It records corrupt, oversized, Word or unsafe sources with held reasons and continues other candidates. A PDF and export sharing the same paper name require explicit association instead of duplicate cropping. Separate companion keys/solutions still need their per-paper association; complete-file mode must not be selected for an ambiguous pile of companion files.

The `x` policy reads actual PDF title text or explicit export metadata, never filenames or answer values, to carry the required existing exam-engine pattern: `jee-main`, `jee-advanced-1`, `jee-advanced-2` or `jee-advanced-both`. An unknown Advanced part stays unresolved. A source identified as Advanced cannot receive a Main profile. Original question/answer/response-format metadata and export bytes remain unchanged; numeric zero and answers1–4 never determine controls. This records integration intent; it does not switch or deploy the portal UI by itself.

Advanced full/partial/negative/unattempted marks and duration must come from its source sections. Source inspection collects unchanged PDF text snippets with page references and document hashes for validation; **it does not yet parse/verify arbitrary Advanced grading rules**. Scanned/unlabelled or conflicting papers remain unresolved; no new OCR/AI service is used. The existing portal's Advanced instructions, controls and scorer differ by pattern, and launch must remain held until question types, scoped numbering, complete source keys and section rules have been validated. New mixed jobs/checks/status explicitly carry that integration hold. Legacy immutable manifests/completed runs are reused rather than rewritten.

Important cropper limit: the unchanged cropper expects mostly option-bearing questions and a simple matching key/number chain. Multiple-correct keys, numerical-heavy Advanced papers and restarted subject/section numbering are not reliably supported; some can refuse, and a structural pass does not establish full paper coverage. Such processing attempts preserve originals and report the limitation. Do not call every Advanced paper a complete usable exam. Separate solutions remain stored, not matched.

Once local work has queued originals/reports, `g` authorises the browser connection so they can reach the private workspace. If sources exist only on the Mac, chat cannot read/validate their actual Advanced instructions before that transfer. No cloud cropping, hosted pipeline or deployment is started.

Optional CLI for a declared collection of complete files:

```sh
python workspace_client.py batch "/path/Mixed Papers" --all-papers --exam-policy main-standard-advanced-source
```
