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
The machine-readable description of how an external workflow discovers, recommends, selects, instantiates, and validates Syn Studios resources while preserving authority boundaries.
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

**Submission Profile**:
A public-safe metadata registry that groups reviewed release lineages by the authorized synthetic submission that informed them. It binds Foundation Cards, Blueprints, Released Templates, and evidence; it does not retain submission artifacts or package facts.
_Avoid_: Submission copy, source archive, template folder

**Pattern Invariant**:
A reviewed structural meaning identified by a controlled semantic ID and bound to an exact Foundation Card pattern. It lets structurally equivalent patterns retain one identity without trusting arbitrary relabeling.
_Avoid_: Tag, paraphrase, pattern wording

**Diversity Fingerprint**:
A stable structural identity derived from an artifact's structure, visual language, producer workflow, population shape, medium, handling history, and unique reviewed semantic Pattern Invariant IDs. It identifies material similarity without depending on prose wording or encoding submission, Foundation Card, or template identity.
_Avoid_: File hash, style hash

**Recent Usage**:
The package-local, oldest-to-newest sequence of exact Released Template identities used to avoid repetitive template selection. It is not a global usage ledger.
_Avoid_: Popularity score, repository history

**Material Transformation**:
A target-bound change to producer workflow, source-system structure, substantive footprint, calculation model, authority or lifecycle, or document composition. Renaming, recoloring, typography, filenames, token substitution, and layout polish alone are cosmetic.
_Avoid_: Restyle, variation token

**Transformation-qualified Facet**:
A producer-role or lifecycle applicability value outside a blueprint's reviewed direct binding. Recommendation may use it only when the submission profile declares its material-change obligation and the caller supplies a matching target-bound Material Transformation plan.
_Avoid_: Broad tag, implied compatibility
