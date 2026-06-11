#!/usr/bin/env python3
"""Fix wu2198 bridge: add error handling to notes_export and notes_import_image."""
import sys

with open('/opt/qq-feishu-bridge-wu2198/bridge_wu2198.py', 'r') as f:
    content = f.read()

# Fix 1: notes_export - add try/except with local fallback
old_export = 'def notes_export(markdown):\n    data = json.dumps({"markdown": markdown, "theme": "default",\n                       "footerBrand": FOOTER_BRAND, "footerVia": ""}).encode()\n    req = urllib.request.Request(f"{NOTES_API}/export", data=data,\n                                 headers={"Content-Type": "application/json"})\n    with urllib.request.urlopen(req, timeout=60, context=ctx_ssl) as r:\n        return r.read()'

new_export = 'def notes_export(markdown):\n    try:\n        data = json.dumps({"markdown": markdown, "theme": "default",\n                           "footerBrand": FOOTER_BRAND, "footerVia": ""}).encode()\n        req = urllib.request.Request(f"{NOTES_API}/export", data=data,\n                                     headers={"Content-Type": "application/json"})\n        with urllib.request.urlopen(req, timeout=15, context=ctx_ssl) as r:\n            return r.read()\n    except Exception:\n        pass\n    # Fallback: local Pillow renderer\n    try:\n        from local_notes_fallback import local_notes_export\n        return local_notes_export(markdown, FOOTER_BRAND)\n    except ImportError:\n        raise RuntimeError("NOTE_API_ERR: remote notes unavailable and local fallback not found")'

if old_export in content:
    content = content.replace(old_export, new_export)
    print('notes_export: PATCHED')
else:
    print('notes_export: PATTERN NOT FOUND!')
    # Try to find what's actually there
    idx = content.find('def notes_export')
    if idx >= 0:
        print('Found at:', idx, repr(content[idx:idx+300]))
    sys.exit(1)

# Fix 2: notes_import_image - add try/except
old_import = '''def notes_import_image(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    boundary = "----Notes" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    CRLF = "\\r\\n"
    parts = [
        ("--" + boundary + CRLF).encode(),
        ('Content-Disposition: form-data; name="image"; filename="' + fname + '"' + CRLF + 'Content-Type: image/png' + CRLF + CRLF).encode(),
        data,
        (CRLF + "--" + boundary + "--" + CRLF).encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(f"{NOTES_API}/images/import", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
        return json.loads(r.read()).get("url", "")'''

new_import = '''def notes_import_image(filepath):
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        boundary = "----Notes" + os.urandom(8).hex()
        fname = os.path.basename(filepath)
        CRLF = "\\r\\n"
        parts = [
            ("--" + boundary + CRLF).encode(),
            ('Content-Disposition: form-data; name="image"; filename="' + fname + '"' + CRLF + 'Content-Type: image/png' + CRLF + CRLF).encode(),
            data,
            (CRLF + "--" + boundary + "--" + CRLF).encode(),
        ]
        body = b"".join(parts)
        req = urllib.request.Request(f"{NOTES_API}/images/import", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
            return json.loads(r.read()).get("url", "")
    except Exception:
        pass
    return ""'''

if old_import in content:
    content = content.replace(old_import, new_import)
    print('notes_import_image: PATCHED')
else:
    print('notes_import_image: PATTERN NOT FOUND!')
    idx = content.find('def notes_import_image')
    if idx >= 0:
        print('Found at:', idx, repr(content[idx:idx+450]))
    sys.exit(1)

with open('/opt/qq-feishu-bridge-wu2198/bridge_wu2198.py', 'w') as f:
    f.write(content)
print('File saved successfully')
