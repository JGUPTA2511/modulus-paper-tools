# Modulus shared paper tools

This public repository contains **code only**. Never upload question papers, answer keys, solutions, student data, crop images, or credentials here.

Claude maintains `question_boundaries.py` (the existing Mac cropper). ChatGPT maintains the original `workspace_client.py` private handoff; Codex added batch orchestration and retry protections around it. The existing `Run_Question_Boundaries.command` launcher is retained. Cropping and watermark reduction are unchanged and run locally, with no paid service or AI API.

Private workspace: `JGUPTA2511/modulus-practice-corner`, branch `paper-workspace`, folder `paper_jobs/<PAPER-ID>/`. Uploading here is **not** deploying an examination. App integration stays on `development`; live `main` is untouched.

## Batch routine on your Mac

1. Make a batch input folder **outside** `~/Modulus Papers`. Put each paper in its own subfolder, named with its stable code:

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
3. Double-click your existing `Run_Question_Boundaries.command`. Let its code update finish, then drag **the batch folder** into the prompt and press Enter. Finder paths with spaces work. First use may install GitHub CLI and ask for browser login; never paste a token into chat.
4. Complete any first-time file selections, then read the preflight summary. Select `y` only for an explicitly chosen JEE Main **mock** profile (+4/-1/0, 180 minutes), press Enter to leave marking unspecified, or select `m` for mixed papers and choose each paper's profile. No profile is inferred from GTM-01 or from answer values.
5. Leave the tool running. Papers process sequentially, without opening review tabs. One failure does not stop the rest. The final summary says **synced**, **already processed**, **upload failed/local-only**, or **held**, with reasons. It prints a local JSON report path under `~/Modulus Papers/.batch_reports/`.
6. Failed uploads are saved in `~/Modulus Papers/.upload_queue/` and retry automatically while the launcher is idle/open, initially after about a minute with backoff up to an hour. Cropping of other papers continues. Closing the window keeps pending work; the next launcher start resumes retries. Keep `~/Modulus Papers` intact. Completed crops are reused, and successful unchanged repeats create no duplicate run or workspace commit. No background retry runs after you close the tool; reopening it is enough.

Generated jobs live separately in `~/Modulus Papers/<PAPER-ID>/`; input folders are never used for outputs or modified. Paper IDs use uppercase letters, digits, `_` or `-`, up to 80 characters. Folder names are uppercased and spaces become `-`; collisions hold **all** folders with that ID. For any changed original, export, marking or color setting, use a new ID/version. Originals and completed runs are immutable. Existing locks protect processing, attachment and sync.

Solutions PDFs are **stored, not extracted or matched**. Source answers, numeric zero, question metadata, passages and original export bytes are retained. Structural checks do not independently verify answers, crop boundaries, question types or marking. Missing source keys may cause the unchanged cropper to refuse a paper. Transcription and portal promotion are later tasks, not prerequisites for image processing.

## Single-paper routine (preserved)

Run the same Mac launcher and drag one question PDF or existing result ZIP instead of a batch folder. For a PDF, give its code, supply a separate key and optional solutions at their prompts, then explicitly choose marking or leave it unspecified. `run`, `attach`, `sync`, and one-paper-per-iteration `--interactive` remain available.

## Optional command-line settings

```sh
python workspace_client.py batch "/path/Modulus Batch"
python workspace_client.py batch "/path/Modulus Batch" --profile jee-main-mock --paper-profile GTM-03=unspecified
python workspace_client.py batch "/path/Modulus Batch" --offline
python workspace_client.py sync "$HOME/Modulus Papers/GTM-02"
```

`--select-files` enables numbered file selection in the `batch` CLI; otherwise ambiguous input is held unless explicit/saved mappings exist. `--root` selects a separate output folder. `--keep-color` is opt-in; normal cropper cleaning is unchanged. Exit status is 1 if any paper is held or local-only; other papers still finish. Offline mode saves local results without GitHub calls.

For reusable mixed settings, an optional `paper.json` accepts `paper_id`, `profile`, `files` and `ignore`. For example `{"paper_id":"GTM-03","profile":"unspecified","files":{"questions":"Original paper.pdf","answer_key":"Original key.pdf"},"ignore":[]}`. File roles are `questions`, `answer_key`, `solutions` (PDFs) and `export` (ZIP); use basenames only. Every PDF/ZIP must be selected or explicitly ignored. No need to create this file when using the launcher: numbered selections are saved automatically. Supported profiles are `unspecified` and `jee-main-mock`. Per-paper settings override the shared selection; explicit CLI `--paper-profile ID=PROFILE` overrides both. Unsupported marking schemes must remain unspecified pending source-backed integration; never substitute the mock profile.

## Collaboration and limits

Read `HANDOFF.md` before changes. Private durable project context is in `paper-workspace:docs/MODULUS_PROJECT_CONTEXT.md`, referenced by that branch's `AGENTS.md`. Claude, ChatGPT and Codex own only their respective handoff notes. Mac processing owns `status.json`; its `ready_for_integration` state never means published. Never merge the whole workspace branch into an app branch.

Each input/export must be at most 24 MiB. Batch preflight parses PDFs and validates export structure before any paper processes. Corrupt/oversized inputs are held without truncation. Export archives also have a 256 MiB expanded limit. Runtime failures can still occur on PDFs that parse successfully. The generic development importer needs source-backed question-type handling before automatic promotion of arbitrary batch papers; this tool does not invoke it.

Local cropping and GitHub API file uploads **do not use GitHub Actions minutes**. An exhausted Actions budget does not block this pipeline. Failed network/auth uploads queue locally; GitHub browser login may still be needed before they can sync. Login/connection failure no longer exits the launcher before local processing. No new Actions workflow, AI call or Netlify deployment is created. Skip tags are included on workspace commits. Existing application workflows and schedules are not disabled. See `TEST_REPORT.md` for observed checks and access limitations.

## Tests

Run `python -m unittest discover -s tests -v` with Pillow and pdfplumber installed. Fixtures are synthetic and generated in temporary folders. GitHub writes in tests are simulated. Linux test success does not prove Mac installation, browser authentication, real paper upload or deployment.

## An older Mac window or Word document

A screen saying “Drag your question paper (PDF)” with page-photo options is the older direct-cropper loop. A final results line followed by that prompt means the prior run finished and is waiting for another input, not that it is still working. Close that window and reopen the current launcher. If it still shows the old prompt, [download the current tool folder](https://github.com/JGUPTA2511/modulus-paper-tools/archive/refs/heads/main.zip), unzip it and double-click `Run_Question_Boundaries.command` inside. Keep your existing papers/results. The updated prompt says “Batch folder / question PDF / result ZIP”.

DOC/DOCX is not an accepted processing format. Keep the Word original and supply a PDF copy with any filename; a Word original beside a PDF is left untouched. A Word-only input is held with an explicit reason. A large number of flagged crops is not proof of a usable exam: this uploader performs structural checks, not independent visual/mathematical verification, and it does not publish the examination.
