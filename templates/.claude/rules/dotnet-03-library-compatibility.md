# .NET Library Compatibility

Before changing a public class-library contract, identify:

- public API surface and consumer call sites;
- nullable annotations and null/empty behavior;
- exception types and message/diagnostic contracts where relied upon;
- package and assembly versioning implications;
- serialization, configuration, or DI registration compatibility.

Avoid breaking changes and dependency expansion unless the user explicitly
approves them. When a breaking change is required, state the migration impact,
consumer verification, and release risk before implementation.
