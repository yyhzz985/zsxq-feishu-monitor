import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from note_renderer import (  # noqa: E402
    ImportedImage,
    NoteImage,
    NoteRenderRequest,
    NoteRenderer,
    NotesRendererSettings,
    PrimaryExport,
)


class NoteRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.storage = self.base / "storage"
        self.storage.mkdir()
        self.lock_file = self.base / "renderer.lock"
        self.source = self.base / "source.png"
        self.source.write_bytes(b"source-image")

    def tearDown(self):
        self.temp_dir.cleanup()

    def settings(self):
        return NotesRendererSettings(
            api_base_url="http://127.0.0.1:18080/api",
            lock_file=str(self.lock_file),
            storage_dir=str(self.storage),
            container_name="hermes-notes",
        )

    def request(self):
        return NoteRenderRequest(
            markdown="2026-06-11 10:00\n\n正文\n\n![image](note-local://image-0)",
            footer_brand="击球区小能手的星球",
            source="zsxq",
            images=(
                NoteImage(
                    marker_url="note-local://image-0",
                    local_path=str(self.source),
                    source_url="https://example.com/source.png",
                ),
            ),
        )

    def test_primary_render_imports_images_rewrites_markdown_and_cleans_storage(self):
        calls = []
        imported_file = self.storage / "imported.png"
        export_file = self.storage / "exported.png"
        imported_file.write_bytes(b"stored-image")
        export_file.write_bytes(b"stored-export")

        def import_image(path):
            calls.append(("import", path))
            return ImportedImage(
                url="http://127.0.0.1:18080/images/imported.png",
                filename="imported.png",
            )

        def export_note(markdown, footer_brand, filename):
            calls.append(("export", markdown, footer_brand, filename))
            return PrimaryExport(data=b"official-png", filename="exported.png")

        renderer = NoteRenderer(
            settings=self.settings(),
            health_check=lambda: True,
            import_image=import_image,
            export_note=export_note,
            restart_container=lambda: calls.append(("restart",)),
            fallback_renderer=lambda *args, **kwargs: self.fail("fallback should not run"),
        )

        result = renderer.render(self.request())

        self.assertEqual(result, b"official-png")
        self.assertEqual(calls[0], ("import", str(self.source)))
        self.assertIn("http://127.0.0.1:18080/images/imported.png", calls[1][1])
        self.assertNotIn("note-local://image-0", calls[1][1])
        self.assertTrue(calls[1][3].startswith("hermes-zsxq-"))
        self.assertTrue(calls[1][3].endswith(".png"))
        self.assertEqual(calls[-1], ("restart",))
        self.assertFalse(imported_file.exists())
        self.assertFalse(export_file.exists())
        self.assertTrue(self.source.exists())

    def test_primary_failure_restarts_then_falls_back_with_same_local_image(self):
        events = []

        def fallback(markdown, footer_brand, image_paths):
            events.append(("fallback", markdown, footer_brand, image_paths))
            return b"fallback-png"

        renderer = NoteRenderer(
            settings=self.settings(),
            health_check=lambda: True,
            import_image=lambda _path: (_ for _ in ()).throw(RuntimeError("import failed")),
            export_note=lambda *_args: self.fail("export should not run"),
            restart_container=lambda: events.append(("restart",)),
            fallback_renderer=fallback,
            on_fallback=lambda message: events.append(("alert", message)),
        )

        result = renderer.render(self.request())

        self.assertEqual(result, b"fallback-png")
        self.assertEqual(events[0], ("restart",))
        self.assertEqual(events[1][0], "alert")
        self.assertIn("import failed", events[1][1])
        self.assertEqual(events[2][0], "fallback")
        self.assertEqual(
            events[2][3],
            {"note-local://image-0": str(self.source)},
        )

    def test_health_check_failure_falls_back_with_same_local_image(self):
        events = []

        renderer = NoteRenderer(
            settings=self.settings(),
            health_check=lambda: False,
            import_image=lambda _path: self.fail("import should not run"),
            export_note=lambda *_args: self.fail("export should not run"),
            restart_container=lambda: events.append(("restart",)),
            fallback_renderer=lambda markdown, footer, images: events.append(
                ("fallback", markdown, footer, images)
            )
            or b"fallback-png",
        )

        self.assertEqual(renderer.render(self.request()), b"fallback-png")
        self.assertEqual(events[0], ("restart",))
        self.assertEqual(events[1][0], "fallback")
        self.assertEqual(events[1][3], {"note-local://image-0": str(self.source)})

    def test_export_failure_falls_back_with_same_local_image(self):
        events = []

        renderer = NoteRenderer(
            settings=self.settings(),
            health_check=lambda: True,
            import_image=lambda _path: ImportedImage(
                url="http://127.0.0.1:18080/images/imported.png",
                filename="",
            ),
            export_note=lambda *_args: (_ for _ in ()).throw(RuntimeError("export failed")),
            restart_container=lambda: events.append(("restart",)),
            fallback_renderer=lambda markdown, footer, images: events.append(
                ("fallback", markdown, footer, images)
            )
            or b"fallback-png",
        )

        self.assertEqual(renderer.render(self.request()), b"fallback-png")
        self.assertEqual(events[0], ("restart",))
        self.assertEqual(events[1][0], "fallback")
        self.assertEqual(events[1][3], {"note-local://image-0": str(self.source)})

    def test_cleanup_never_removes_a_path_outside_storage(self):
        outside = self.base / "outside.png"
        outside.write_bytes(b"keep")

        renderer = NoteRenderer(
            settings=self.settings(),
            health_check=lambda: True,
            import_image=lambda _path: ImportedImage(
                url="http://127.0.0.1:18080/images/outside.png",
                filename="../outside.png",
            ),
            export_note=lambda *_args: PrimaryExport(
                data=b"official-png",
                filename="../outside.png",
            ),
            restart_container=lambda: None,
            fallback_renderer=lambda *_args, **_kwargs: b"fallback",
        )

        self.assertEqual(renderer.render(self.request()), b"official-png")
        self.assertTrue(outside.exists())

    def test_global_lock_serializes_two_renderer_instances(self):
        state = {"active": 0, "maximum": 0}
        state_lock = threading.Lock()

        def export_note(_markdown, _footer_brand, filename):
            with state_lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.08)
            with state_lock:
                state["active"] -= 1
            return PrimaryExport(data=filename.encode("ascii"), filename="")

        def make_renderer():
            return NoteRenderer(
                settings=self.settings(),
                health_check=lambda: True,
                import_image=lambda _path: ImportedImage(
                    url="http://127.0.0.1:18080/images/imported.png",
                    filename="",
                ),
                export_note=export_note,
                restart_container=lambda: None,
                fallback_renderer=lambda *_args, **_kwargs: b"fallback",
            )

        threads = [threading.Thread(target=make_renderer().render, args=(self.request(),)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(state["maximum"], 1)

    def test_default_api_is_loopback_and_never_public_notes_domain(self):
        settings = NotesRendererSettings.from_env({})
        self.assertEqual(settings.api_base_url, "http://127.0.0.1:18080/api")
        self.assertNotIn("fangyuanxiaozhan.com", settings.api_base_url)

    def test_rejects_non_loopback_notes_api_configuration(self):
        with self.assertRaises(ValueError):
            NotesRendererSettings(
                api_base_url="https://notes.example.com/api",
                lock_file=str(self.lock_file),
                storage_dir=str(self.storage),
                container_name="hermes-notes",
            )


if __name__ == "__main__":
    unittest.main()
