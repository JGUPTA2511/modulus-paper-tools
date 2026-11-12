# Local verification — 12 November 2026

24 tests passed using `python -m unittest discover -s tests -v` in the assistant's Linux workspace. They cover sequential numbers, missing/invalid answers, readable PNG references, corrupt archives, path traversal, refused results, numeric zero preservation, separate PDF inputs, immutable inputs, privacy checks before blob upload, changed-files-only sync, idempotent retries, same-path conflicts, preservation of concurrent work, refusal report uploads, and reuse of an existing export without invoking the cropper.

Both shell launchers passed `bash -n`; Python modules passed compilation. The published cropper Git blob 5aa3dd7882ec8bc6f49e3800b296911b871f66c3 matches the exact original code from the owner's supplied bundle. The published workspace client blob f5623045da3d646902fe3e3139139dc147b90ae3 matches the locally tested client.

The owner's real supplied 75-question export passed structural validation: all 75 PNGs decoded, numbering and references matched, and all 75 source answer entries were present. No original question pixels or answers were changed. These checks are NOT independent mathematical verification or a fresh visual audit of every boundary.

GitHub operations in the unit tests use an in-memory API simulator. This does not establish a successful real Mac authentication/transfer. The first signed-in Mac upload remains to be verified. The workspace is not a deployed examination.
