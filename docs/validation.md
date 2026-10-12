# Release validation — v0.1.0

Validated on 2026-10-12 on Apple Silicon macOS, Docker Desktop 29.4.2, Linux ARM64 containers.

Passed locally:

- Fresh Docker image pulls, builds, all six services running, health checks passing, and only the companion's loopback port published.
- Three import unit tests: dataset-ID isolation, ZIP manifest/image loading, rejection of parent path traversal.
- Integration smoke: ZIP+JSONL/image and two-page PDF imports; duplicate dataset-name protection; Persian edit save/readback; isolation between datasets sharing source IDs; isolation between two annotators; denial of access before workspace membership; review submission; export of original labels, images, edited boxes, contributor IDs and submitted review status.
- Browser: local owner sign-in, import through the file chooser, image/box rendering, manual Persian text correction, persisted text after service restart and browser reload, removal of hosted OCR buttons, radial review submission, and submitted-record view.
- Full backup creation; restoration to separate, empty Docker volumes; search reindex; restored image and Persian-edit readback. Only the disposable restore-check stack was removed.
- Shell syntax and Compose configuration validation. Generated local credentials are excluded from Git.

Not manually validated: Linux or Windows/WSL2 host installation, large book imports, online Hugging Face imports, every interaction of the inherited editor (including table markup and every resize/rotation shortcut), or equivalence to the current live PersianVLM deployment. An optional GitHub Actions workflow is provided at `docs/github-actions.yml`; copy it to `.github/workflows/test.yml` to enable Ubuntu CI. It was not enabled because the publishing login lacks GitHub’s workflow scope.

The upstream v2.8.0 server image returned a validation error at `/api/v1/status` because its Elasticsearch client returned `ObjectApiResponse`. The derived server image unwraps `.body` to a dictionary. The companion requests uncompressed upstream content to avoid a browser/server compression mismatch. The inherited editor also needed deferred cached-image initialization before its state variables exist.
