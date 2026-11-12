# Rules for this public tools repository

Read HANDOFF.md. This repository contains code only. Never add private papers, answer keys, solutions, student data, secrets or cropped question images. The .gitignore is defensive only; API writes must also obey this rule.

Keep Claude's cropper stable unless asked to change cropping. ChatGPT maintains the workspace client and portal integration. Do not replace the shared workflow with a separate app. Run the local tests before changing uploader behaviour. Do not add per-paper Actions workflows. All ordinary tool commits use [skip ci] [skip netlify].

Workspace data is on JGUPTA2511/modulus-practice-corner branch paper-workspace. App integration is on development, not main. Never merge the entire paper-workspace branch into the app branches.
