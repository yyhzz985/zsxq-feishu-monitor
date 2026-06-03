
"""OSS integration helpers for zsxq_monitor."""
import os
import re

# These will be set by the main script via module-level variables
OSS_ENABLED = False
OSS_BUCKET = ""
OSS_ENDPOINT = "oss-cn-hangzhou-internal.aliyuncs.com"
OSS_ACCESS_KEY_ID = ""
OSS_ACCESS_KEY_SECRET = ""

_oss_auth = None
_oss_bucket = None


def _get_oss_bucket():
    global _oss_auth, _oss_bucket
    if not OSS_ENABLED or not OSS_BUCKET:
        return None
    if _oss_bucket is None and OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET:
        import oss2
        _oss_auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
        _oss_bucket = oss2.Bucket(_oss_auth, OSS_ENDPOINT, OSS_BUCKET)
        _oss_bucket.timeout = 60
    return _oss_bucket


def oss_upload(local_path, oss_key, log_func=None):
    bucket = _get_oss_bucket()
    if not bucket:
        return False
    try:
        bucket.put_object_from_file(oss_key, local_path)
        msg = f"OSS_UPLOAD: {oss_key} ({os.path.getsize(local_path)/(1024*1024):.1f}MB)"
        if log_func:
            log_func(msg)
        return True
    except Exception as exc:
        msg = f"OSS_UPLOAD_FAIL: {oss_key} {exc}"
        if log_func:
            log_func(msg)
        return False


def oss_download(oss_key, local_path, log_func=None):
    bucket = _get_oss_bucket()
    if not bucket:
        return False
    try:
        bucket.get_object_to_file(oss_key, local_path)
        return True
    except Exception as exc:
        msg = f"OSS_DOWNLOAD_FAIL: {oss_key} {exc}"
        if log_func:
            log_func(msg)
        return False


def oss_key_for_archive(group_name, date_str, filename):
    safe_group = re.sub(r'[<>:"/\\|?*]', "_", group_name or "unknown")
    return f"{safe_group}/{date_str}/{filename}"


def check_oss_health():
    bucket = _get_oss_bucket()
    if not bucket:
        return None
    try:
        bucket.get_bucket_info()
        return True
    except Exception:
        return False
