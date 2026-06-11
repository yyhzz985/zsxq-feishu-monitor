#!/usr/bin/env python3
"""Unified local Smartisan Notes renderer with an immediate Pillow fallback."""

import json
import mimetypes
import os
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager


DEFAULT_API_BASE_URL = "http://127.0.0.1:18080/api"
DEFAULT_LOCK_FILE = (
    os.path.join(tempfile.gettempdir(), "hermes-notes-renderer.lock")
    if os.name == "nt"
    else "/run/lock/hermes-notes-renderer.lock"
)
DEFAULT_STORAGE_DIR = "/opt/notes-renderer/storage/images"
DEFAULT_CONTAINER_NAME = "hermes-notes"


class NoteImage(object):
    __slots__ = ("marker_url", "local_path", "source_url")

    def __init__(self, marker_url, local_path, source_url=""):
        self.marker_url = marker_url
        self.local_path = local_path
        self.source_url = source_url


class NoteRenderRequest(object):
    __slots__ = ("markdown", "footer_brand", "source", "images")

    def __init__(self, markdown, footer_brand, source, images=()):
        self.markdown = markdown
        self.footer_brand = footer_brand
        self.source = source
        self.images = tuple(images)


class ImportedImage(object):
    __slots__ = ("url", "filename")

    def __init__(self, url, filename):
        self.url = url
        self.filename = filename


class PrimaryExport(object):
    __slots__ = ("data", "filename")

    def __init__(self, data, filename):
        self.data = data
        self.filename = filename


class NotesRendererSettings(object):
    __slots__ = ("api_base_url", "lock_file", "storage_dir", "container_name")

    def __init__(self, api_base_url, lock_file, storage_dir, container_name):
        parsed_url = urllib.parse.urlparse(api_base_url)
        if parsed_url.scheme != "http" or parsed_url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Notes API must use a loopback HTTP address")
        self.api_base_url = api_base_url.rstrip("/")
        self.lock_file = lock_file
        self.storage_dir = storage_dir
        self.container_name = container_name

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(
            api_base_url=environ.get("NOTES_API_BASE_URL", DEFAULT_API_BASE_URL),
            lock_file=environ.get("NOTES_RENDER_LOCK_FILE", DEFAULT_LOCK_FILE),
            storage_dir=environ.get("NOTES_RENDER_STORAGE_DIR", DEFAULT_STORAGE_DIR),
            container_name=environ.get("NOTES_RENDERER_CONTAINER", DEFAULT_CONTAINER_NAME),
        )


_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock_for(path):
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[path] = lock
        return lock


@contextmanager
def renderer_lock(path):
    """Serialize all renderers in this process and across Linux processes."""
    process_lock = _process_lock_for(os.path.abspath(path))
    with process_lock:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        handle = open(path, "a+b")
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class LocalNotesApiClient(object):
    def __init__(self, settings):
        self.settings = settings

    def health_check(self):
        request = urllib.request.Request(self.settings.api_base_url + "/health")
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.getcode() != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("ok") is True

    def import_image(self, filepath):
        boundary = "----HermesNotes" + os.urandom(8).hex()
        filename = os.path.basename(filepath)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(filepath, "rb") as source:
            image_data = source.read()
        body = (
            ("--%s\r\n" % boundary).encode("ascii")
            + ('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % filename).encode("utf-8")
            + ("Content-Type: %s\r\n\r\n" % content_type).encode("ascii")
            + image_data
            + ("\r\n--%s--\r\n" % boundary).encode("ascii")
        )
        request = urllib.request.Request(
            self.settings.api_base_url + "/images/import",
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.getcode() != 200:
                raise RuntimeError("notes image import returned HTTP %s" % response.getcode())
            payload = json.loads(response.read().decode("utf-8"))
        url = payload.get("url", "")
        if not url:
            raise RuntimeError("notes image import returned no URL")
        filename = os.path.basename(urllib.parse.urlparse(url).path)
        return ImportedImage(url=url, filename=filename)

    def export_note(self, markdown, footer_brand, filename):
        payload = json.dumps(
            {
                "markdown": markdown,
                "theme": "default",
                "footerBrand": footer_brand,
                "footerVia": "",
                "filename": filename,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.settings.api_base_url + "/export",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.getcode() != 200:
                raise RuntimeError("notes export returned HTTP %s" % response.getcode())
            data = response.read()
            export_path = response.headers.get("X-Export-Path", "")
        if not data:
            raise RuntimeError("notes export returned an empty image")
        return PrimaryExport(data=data, filename=os.path.basename(export_path))

    def restart_container(self):
        completed = subprocess.run(
            ["docker", "restart", self.settings.container_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
        )
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError("notes container restart failed: %s" % error)


class NoteRenderer(object):
    def __init__(
        self,
        settings=None,
        health_check=None,
        import_image=None,
        export_note=None,
        restart_container=None,
        fallback_renderer=None,
        logger=None,
        on_fallback=None,
    ):
        self.settings = settings or NotesRendererSettings.from_env()
        client = LocalNotesApiClient(self.settings)
        self.health_check = health_check or client.health_check
        self.import_image = import_image or client.import_image
        self.export_note = export_note or client.export_note
        self.restart_container = restart_container or client.restart_container
        self.fallback_renderer = fallback_renderer or self._default_fallback_renderer
        self.logger = logger or (lambda _message: None)
        self.on_fallback = on_fallback or (lambda _message: None)

    @staticmethod
    def _default_fallback_renderer(markdown, footer_brand, image_paths):
        from local_notes_fallback import local_notes_export

        return local_notes_export(markdown, footer_brand, image_paths=image_paths)

    def _cleanup_storage_file(self, filename):
        if not filename or filename != os.path.basename(filename):
            return
        storage_root = os.path.realpath(self.settings.storage_dir)
        target = os.path.realpath(os.path.join(storage_root, filename))
        if os.path.commonpath([storage_root, target]) != storage_root:
            return
        try:
            os.remove(target)
        except FileNotFoundError:
            pass

    def _restart_after_attempt(self):
        try:
            self.restart_container()
        except Exception as exc:
            self.logger("RENDERER_RESTART_FAILED: %s" % exc)

    def render(self, request):
        image_paths = {item.marker_url: item.local_path for item in request.images}
        for item in request.images:
            if item.marker_url not in request.markdown:
                raise RuntimeError("note image marker missing from markdown: %s" % item.marker_url)
            if not os.path.isfile(item.local_path):
                raise RuntimeError("note image file missing: %s" % item.local_path)

        with renderer_lock(self.settings.lock_file):
            cleanup_names = []
            primary_error = None
            try:
                if not self.health_check():
                    raise RuntimeError("local notes health check failed")

                primary_markdown = request.markdown
                for item in request.images:
                    imported = self.import_image(item.local_path)
                    if not imported.url:
                        raise RuntimeError("local notes image import returned no URL")
                    primary_markdown = primary_markdown.replace(item.marker_url, imported.url)
                    if imported.filename:
                        cleanup_names.append(imported.filename)

                filename = "hermes-%s-%s-%s.png" % (
                    request.source or "note",
                    time.strftime("%Y%m%d%H%M%S"),
                    os.urandom(4).hex(),
                )
                exported = self.export_note(primary_markdown, request.footer_brand, filename)
                if exported.filename:
                    cleanup_names.append(exported.filename)
                return exported.data
            except Exception as exc:
                primary_error = exc
            finally:
                for filename in set(cleanup_names):
                    try:
                        self._cleanup_storage_file(filename)
                    except Exception as exc:
                        self.logger("RENDERER_CLEANUP_FAILED: %s" % exc)
                self._restart_after_attempt()

            message = "RENDERER_FALLBACK source=%s error=%s" % (request.source, primary_error)
            self.logger(message)
            try:
                self.on_fallback(message)
            except Exception as exc:
                self.logger("RENDERER_FALLBACK_ALERT_FAILED: %s" % exc)
            return self.fallback_renderer(request.markdown, request.footer_brand, image_paths)


def render_note(request, logger=None, on_fallback=None):
    return NoteRenderer(logger=logger, on_fallback=on_fallback).render(request)
