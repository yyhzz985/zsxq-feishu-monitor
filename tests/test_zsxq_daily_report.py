import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import zsxq_daily_report as daily  # noqa: E402


class FeishuDocumentUploadVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.args = argparse.Namespace(
            lark_cli="lark-cli",
            lark_as="bot",
            feishu_doc="https://example.feishu.cn/docx/test",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def output_for(self, day):
        title = daily.report_title("Group", day)
        pdf_path = self.base / daily.safe_filename(title + ".pdf")
        pdf_path.write_bytes(b"same-size")
        return daily.DayOutput(day, title, self.base / "report.md", pdf_path, 1, 1, 0, {})

    def document_content(self, token, size, mime="application/pdf"):
        return f'<figure><source mime="{mime}" size="{size}" token="{token}"/></figure>'

    def metadata_content(self, token, title):
        return json.dumps(
            {
                "code": 0,
                "data": {
                    "metas": [
                        {
                            "doc_token": token,
                            "doc_type": "file",
                            "title": title,
                        }
                    ]
                },
            }
        )

    def test_verify_feishu_order_rejects_same_size_wrong_date_pdf(self):
        output = self.output_for(dt.date(2026, 6, 11))
        wrong_title = daily.report_title("Group", dt.date(2026, 6, 10))
        wrong_name = daily.safe_filename(wrong_title + ".pdf")
        wrong_token = "wrong-token"
        wrong_content = self.document_content(wrong_token, output.pdf_path.stat().st_size)

        original_run_lark = daily.run_lark

        def fake_run_lark(_lark_cli, args, _cwd):
            if "+fetch" in args:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=wrong_content, stderr="")
            if "batch_query" in args:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=self.metadata_content(wrong_token, wrong_name),
                    stderr="",
                )
            raise AssertionError(args)

        daily.run_lark = fake_run_lark
        try:
            with self.assertRaisesRegex(RuntimeError, "expected PDF file"):
                daily.verify_feishu_order(self.args, [output])
        finally:
            daily.run_lark = original_run_lark

    def test_verify_feishu_order_accepts_expected_pdf_name_and_size(self):
        output = self.output_for(dt.date(2026, 6, 11))
        token = "expected-token"
        content = self.document_content(token, output.pdf_path.stat().st_size)

        original_run_lark = daily.run_lark

        def fake_run_lark(_lark_cli, args, _cwd):
            if "+fetch" in args:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=content, stderr="")
            if "batch_query" in args:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=self.metadata_content(token, output.pdf_path.name),
                    stderr="",
                )
            raise AssertionError(args)

        daily.run_lark = fake_run_lark
        try:
            daily.verify_feishu_order(self.args, [output])
        finally:
            daily.run_lark = original_run_lark

    def test_upload_outputs_skips_existing_pdf_without_reinserting(self):
        output = self.output_for(dt.date(2026, 6, 11))
        token = "existing-token"
        content = self.document_content(token, output.pdf_path.stat().st_size, mime="file")
        self.args.no_upload = False
        self.args.insert_before_date = ""

        calls = []
        original_run_lark = daily.run_lark

        def fake_run_lark(_lark_cli, args, _cwd):
            calls.append(args)
            if "+media-insert" in args:
                raise AssertionError("existing PDF must not be inserted again")
            if "+fetch" in args:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=content, stderr="")
            if "batch_query" in args:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=self.metadata_content(token, output.pdf_path.name),
                    stderr="",
                )
            raise AssertionError(args)

        daily.run_lark = fake_run_lark
        try:
            daily.upload_outputs(self.args, [output])
        finally:
            daily.run_lark = original_run_lark

        self.assertTrue(output.uploaded)
        self.assertEqual(sum(1 for args in calls if "+fetch" in args), 2)
        self.assertFalse(any("+media-insert" in args for args in calls))


if __name__ == "__main__":
    unittest.main()
