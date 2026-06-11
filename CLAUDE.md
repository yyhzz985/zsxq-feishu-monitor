# Hermes Engineering Rules

## Architecture

- View: `src/local_notes_fallback.py` only rasterizes note content.
- Controller: `src/note_renderer.py` owns HTTP calls to the local Notes API.
- Service: `src/zsxq_monitor.py` and `qq-feishu-bridge/bridge_wu2198.py` assemble content and orchestrate delivery.
- Utils: locking, safe path handling, image sizing, and markdown marker helpers stay free of Feishu or ZSXQ business rules.
- Data: existing SQLite state and archive storage remain unchanged.
- Dependencies flow from service to renderer/controller to view or utilities. Do not call Feishu, ZSXQ, or SQLite from the renderer.

## Note Rendering

- Production rendering uses the loopback-only self-hosted Notes API. Do not call `notes.fangyuanxiaozhan.com`.
- Every local image remains available until rendering completes.
- A failed image import must fail the primary render. Never silently send a note with missing images.
- The Pillow renderer is the immediate fallback and must receive the same local image mapping.
- All renderer storage cleanup must verify that the resolved target is inside the configured storage root.
- All render attempts share one host-wide lock. The Notes container is restarted after each primary attempt to release Chromium memory.

## Temporary Files

- Callers own downloaded source images and delete them in `finally` after `render_note` returns or raises.
- `note_renderer.py` owns files created in the Notes storage mount and deletes only the exact imported/exported basenames.
- Archive images and SQLite files are never temporary and must not be deleted by rendering cleanup.

## Validation

Run from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s qq-feishu-bridge\tests -v
python -m py_compile src\note_renderer.py src\local_notes_fallback.py src\zsxq_monitor.py qq-feishu-bridge\bridge_wu2198.py
python deploy\validate_notes_renderer.py
```

## Deployment Red Lines

- Do not edit production `.env`, credentials, tokens, CI/CD, databases, or system configuration without explicit approval.
- Do not start or replace the production container, restart systemd services, send Feishu test messages, or deploy to production without explicit approval.
- Deployment files may contain placeholders and documented commands, never secrets.
