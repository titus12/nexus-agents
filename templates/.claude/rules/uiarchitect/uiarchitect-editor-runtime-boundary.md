# UIArchitect Editor/Runtime Boundary

- Editor-only pipeline code must stay under an Editor assembly or path.
- Runtime UIArchitect code must not depend on `UnityEditor`.
- Generated scripts must compile in the intended runtime or editor context.
- Host-project business code must not be required for reusable UIArchitect plugin code to compile.
