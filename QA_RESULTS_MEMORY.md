# TAOS Memory QA Results

- Mode: `mock`
- Passed: **5/5**
- Pass rate: **1.0**

| Case | Passed | Checks |
| --- | ---: | --- |
| `explicit_remember` | yes | memory_saved_matches_expected=True |
| `secret_not_saved` | yes | memory_saved_matches_expected=True |
| `forget_memory` | yes | memory_deleted=True |
| `recall_project` | yes | project_memory_used=True |
| `false_memory_guard` | yes | no_fake_memory=True |
