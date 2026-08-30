# Commit message guidelines

Use this header format:

```text
<emoji> [type] (scope) concise imperative subject
```

The scope is optional. Keep the header at or below 120 characters and use a
body when the behavior or rationale is not obvious from the subject.

## Allowed types

- `👷 [build]` — packaging, dependencies, or build configuration
- `🧹 [chore]` — repository maintenance with no runtime behavior change
- `👷 [ci]` — GitHub Actions and other continuous-integration changes
- `📝 [docs]` — documentation-only changes
- `✨ [feat]` — user-visible functionality
- `🐛 [fix]` — bug fixes
- `⚡️ [perf]` — performance improvements
- `🚜 [refactor]` — behavior-preserving source restructuring
- `⏪ [revert]` — reverts
- `🎨 [style]` — formatting-only changes
- `🧪 [test]` — test-only changes

## Project scopes

Prefer a narrow scope when it adds useful context: `ant`, `ble`, `ui`,
`settings`, `packaging`, `deps`, `ci`, or `docs`.

## Commit bodies

- Explain user-visible behavior and safety constraints before implementation
  details.
- Keep tests, typing, packaging, and documentation with the behavior they
  support when they form one coherent change.
- Do not claim real ANT+ battery telemetry was verified unless an awake device
  produced Common Data Page 82 during a documented hardware test.
