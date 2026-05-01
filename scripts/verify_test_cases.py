"""Quick verification of Backend Report mandatory test cases."""
from taos.core.semantic.intent_classifier import IntentClassifier

c = IntentClassifier()

tests = [
    ("vite version", "simple_lookup", "fast"),
    ("React vs Vue performance", "comparison", "standard"),
    ("fix module not found error", "task", "standard"),
    ("latest AI models", "news", "standard"),
    ("give timeline", "transform", "fast"),
    ("run python code factorial", "task", "standard"),
]

print("=" * 60)
print("BACKEND REPORT - 5 MANDATORY TEST CASES")
print("=" * 60)
all_pass = True
for query, expected_intent, expected_mode in tests:
    r = c.classify(query)
    ok = r.intent.value == expected_intent
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"{status} | {query:40s} | intent={r.intent.value:15s} (expect: {expected_intent:15s}) | domain={r.domain.value:12s} | mode={r.suggested_mode}")

print("=" * 60)
if all_pass:
    print("Result: ALL PASS")
else:
    print("Result: SOME FAILED")
