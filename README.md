# Modulus shared paper tools

This public repository contains **code only**. Never upload question papers, answer keys, solutions, student data, crop images, or credentials here.

Claude maintains `question_boundaries.py` (the existing Mac cropper). ChatGPT maintains `workspace_client.py` (the private GitHub handoff). The existing `Run_Question_Boundaries.command` launcher is retained.

Private workspace: `JGUPTA2511/modulus-practice-corner`, branch `paper-workspace`, folder `paper_jobs/<PAPER-ID>/`.

The Mac does the cropping. The uploader copies the paper, separate key and optional solutions to the private workspace, then uploads the original crop output and a readable processing report. It does not deploy Netlify, run GitHub Actions, transcribe with an AI API, or invent solutions/marking rules.

## User routine
1. Run the existing Mac launcher. The first shared-workspace run may install GitHub CLI and asks you to sign in to GitHub in your browser.
2. Drag in the question PDF. Give the paper a short code such as `GTM-02`.
3. At the next prompts, drag in a separate answer-key PDF and optional solution PDF. Press Enter when a file is not separate or is unavailable.
4. Select JEE Main mock rules only when appropriate; otherwise leave the marking unspecified.
5. The local cropper runs and the results sync to the **private** workspace. Tell either assistant `Continue GTM-02 in paper-workspace`.

An already-cropped result ZIP can be supplied instead of a question PDF. It is validated and uploaded unchanged, not recropped. Separate solutions are preserved for matching; solution extraction is not implemented by this uploader.

Local papers live in `~/Modulus Papers/<PAPER-ID>/`. No GitHub Desktop, full repository clone, or scan of the existing question-bank files is required. Inputs and completed runs are immutable; changed input needs a new paper ID. Successful repeat runs with unchanged inputs and cropper reuse the saved output.

## Collaboration
Read `HANDOFF.md` before making changes. ChatGPT and Claude update their own handoff notes; neither may claim that an exam is deployed merely because files were uploaded. The existing portal integration remains on `development`; `main` is not modified by the workspace.

## Limits
The sync client caps each file at 24 MiB. Cropping and answer extraction have the limitations of Claude's existing cropper. Structural validation is not independent mathematical verification. This is a shared upload/handoff system, not a guarantee of a complete exam from every arbitrary PDF.

## Tests
Run `python -m unittest discover -s tests -v` with Pillow installed. Tests do not require a GitHub login and do not spend Actions minutes.
