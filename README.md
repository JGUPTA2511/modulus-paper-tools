# Modulus shared paper tools

This public repository contains **code only**. Never upload question papers, answer keys, solutions, student data, crop images, or credentials here.

Claude maintains `question_boundaries.py` (the existing Mac cropper). ChatGPT maintains the original `workspace_client.py` private handoff; Codex added batch orchestration and retry protections around it. The existing `Run_Question_Boundaries.command` launcher is retained. Cropping and watermark reduction are unchanged and run locally, with no paid service or AI API.

Private workspace: `JGUPTA2511/modulus-practice-corner`, branch `paper-workspace`, folder `paper_jobs/<PAPER-ID>/`. Uploading here is **not** deploying an examination. App integration stays on `development`; live `main` is untouched.

## Batch routine on your Mac

1. Make a batch input folder **outside** `~/Modulus Papers`. Put each paper in its own subfolder, named with its stable code:

   ```text
   Modulus Batch/
     GTM-02/
       questions.pdf
       answer_key.pdf
       solutions.pdf
     GTM-03/
       questions.pdf
     GTM-04/
       export.zip
   ```

2. Use these exact role names (case-insensitive). A key embedded in `questions.pdf` needs no separate key file. `solutions.pdf` is optional. `export.zip` takes precedence when supplied and is copied unchanged, with no recropping; PDFs beside it are preserved as originals. Unknown PDF/ZIP names, duplicate roles, symlinks and nested input folders are held rather than guessed.
3. Double-click your existing `Run_Question_Boundaries.command`. Let its code update finish, then drag **the batch folder** into the prompt and press Enter. Finder paths with spaces work. First use may install GitHub CLI and ask for browser login; never paste a token into chat.
4. Read the one preflight summary. Select `y` only for an explicitly chosen JEE Main **mock** profile (+4/-1/0, 180 minutes), press Enter to leave marking unspecified, or select `m` for mixed papers and choose each paper's profile. No profile is inferred from GTM-01 or from answer values.
5. Leave the tool running. Papers process sequentially, without opening review tabs. One failure does not stop the rest. The final summary says **synced**, **already processed**, **upload failed/local-only**, or **held**, with reasons. It prints a local JSON report path under `~/Modulus Papers/.batch_reports/`.
6. If an upload fails, run the launcher again with the same batch folder and settings. Completed crops are reused and only missing/different remote files are uploaded. A successful unchanged repeat creates no duplicate run or workspace commit. Keep the local `~/Modulus Papers` folder for retries.

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

`--root` selects a separate output folder. `--keep-color` is opt-in; normal cropper cleaning is unchanged. Exit status is 1 if any paper is held or local-only; other papers still finish. Offline mode saves local results without GitHub calls.

For reusable mixed settings, an optional `paper.json` in an input subfolder accepts only `paper_id` and `profile`, for example `{"paper_id":"GTM-03","profile":"unspecified"}`. Supported profiles are `unspecified` and `jee-main-mock`. Per-paper settings override the shared selection; explicit CLI `--paper-profile ID=PROFILE` overrides both. Unsupported marking schemes must remain unspecified pending source-backed integration; never substitute the mock profile.

## Collaboration and limits

Read `HANDOFF.md` before changes. Private durable project context is in `paper-workspace:docs/MODULUS_PROJECT_CONTEXT.md`, referenced by that branch's `AGENTS.md`. Claude, ChatGPT and Codex own only their respective handoff notes. Mac processing owns `status.json`; its `ready_for_integration` state never means published. Never merge the whole workspace branch into an app branch.

Each input/export must be at most 24 MiB. Batch preflight parses PDFs and validates export structure before any paper processes. Corrupt/oversized inputs are held without truncation. Export archives also have a 256 MiB expanded limit. Runtime failures can still occur on PDFs that parse successfully. The generic development importer needs source-backed question-type handling before automatic promotion of arbitrary batch papers; this tool does not invoke it.

No new Actions workflow, AI call or Netlify deployment is created. Skip tags are included on workspace commits. Existing application workflows and schedules are not disabled. See `TEST_REPORT.md` for observed checks and access limitations.

## Tests

Run `python -m unittest discover -s tests -v` with Pillow and pdfplumber installed. Fixtures are synthetic and generated in temporary folders. GitHub writes in tests are simulated. Linux test success does not prove Mac installation, browser authentication, real paper upload or deployment.
