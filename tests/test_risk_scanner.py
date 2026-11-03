from src.agent.scanner import scan_diff


DIFF_SQL = '''
diff --git a/app.py b/app.py
+query = "SELECT * FROM users WHERE id=" + user_id
'''

DIFF_SECRET = '''
diff --git a/cfg.py b/cfg.py
+API_KEY = "sk-live-1234567890abcdef"
'''

DIFF_CLEAN = '''
diff --git a/util.py b/util.py
+def add(a, b):
+    return a + b
'''


def test_detects_sql_injection():
    findings = scan_diff(DIFF_SQL)
    assert any(f.rule == "sql_injection" for f in findings)
    assert findings[0].severity == "high"


def test_detects_hardcoded_secret():
    findings = scan_diff(DIFF_SECRET)
    assert any(f.rule == "hardcoded_secret" for f in findings)


def test_clean_diff_has_no_findings():
    assert scan_diff(DIFF_CLEAN) == []
