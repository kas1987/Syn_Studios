# Syn Studios Domain Language

Syn Studios is a source-first library for selecting and safely reusing the structure of authorized synthetic business artifacts without retaining package-specific facts.

## Language

**Foundation Card**:
A reviewed record of a source artifact's reusable structural patterns, prohibited content, known defects, and required proof. It is not a reusable artifact.
_Avoid_: Source copy, template

**Blueprint**:
A fact-free description of an artifact's producer, purpose, medium, lifecycle, authority, footprint, complexity, prohibitions, and proof needs.
_Avoid_: Manifest, template

**Released Template**:
A sanitized, independently validated artifact package approved for reuse under a specific blueprint and version.
_Avoid_: Example file, prior submission

**Consumer Profile**:
The machine-readable description of how an external workflow discovers, selects, instantiates, and validates Syn Studios resources while preserving authority boundaries.
_Avoid_: Workflow owner, approval record

**Template Binding**:
A package-local association between one released template version, one blueprint, and the inputs supplied by a synthetic world.
_Avoid_: Template mutation, world template

**World Facts**:
Package-owned entities, people, dates, values, identifiers, relationships, and task constraints used to instantiate artifacts.
_Avoid_: Template facts, library facts

**Evidence Record**:
A machine-readable result from a named validation check against identified artifact bytes.
_Avoid_: Approval, assurance
