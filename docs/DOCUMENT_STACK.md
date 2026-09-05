# Local document stack

Syn Studios keeps repository validation portable while making document generation and rendered QA reproducible on a capable local workstation. Large applications, managed runtimes, and private packages remain outside the repository.

## Capability order

1. Use the Codex managed runtime for Python, Node, pnpm, Git, Poppler, and `@oai/artifact-tool` when it is available.
2. Install only packages missing from the managed runtime into the ignored `.tooling/python-fallbacks` directory. `toolchain.toml` records accepted versions and capability profiles.
3. Use installed LibreOffice headlessly for deterministic DOCX, XLSX, and PPTX conversion with a disposable user profile.
4. Treat installed Microsoft Office and COM as an optional compatibility fallback. They are not required by CI and are never launched by the stack checker.

The Python packages in `requirements-analysis.txt` are analysis and verification fallbacks. They do not supersede a task's approved native-object generation method. The managed `@oai/artifact-tool` package is private runtime infrastructure: do not copy it, publish it, or add it to a package manifest.

## Bootstrap and activation

From PowerShell at the repository root:

```powershell
.\scripts\bootstrap_document_stack.ps1 -InstallOfficeComFallback
. .\scripts\activate_document_stack.ps1
& $env:SYN_STUDIOS_PYTHON .\scripts\check_document_stack.py --profile all --json
```

The bootstrap installs the core `jsonschema` dependency only when it is missing from the managed Python. `-InstallOfficeComFallback` adds the Windows COM bridge when needed; `-InstallAnalysisFallbacks` installs the analysis profile only if the managed runtime does not already satisfy it. The activation script scopes environment variables to the current PowerShell process. Explicit path parameters take priority, followed by the managed runtime, PATH, and standard Windows install locations.

The checker exposes `core`, `analysis`, `render`, `generation`, `dev`, `office`, and `all` profiles. Missing native or optional tooling returns `CANNOT_CHECK`, never a false pass; use the profile required by the work being performed.

## Render validation

Use a new or disposable output directory:

```powershell
& $env:SYN_STUDIOS_PYTHON .\scripts\render_validate.py `
  --input <authorized-synthetic-file> `
  --output-dir .tmp\render-proof
```

PDF inputs are inspected and rendered directly. DOCX, XLSX, PPTX, ODT, ODS, and ODP inputs are converted to PDF through LibreOffice with an isolated profile, then every PDF page is rendered through Poppler. The validator hashes the source before and after and fails if the source changes.

## Boundaries

- Never commit `.venv`, `.tooling`, converted outputs, runtime binaries, application installers, private Node modules, or submission artifacts.
- Never use unqualified Node, pnpm, or Poppler commands for artifact work after activation; use the exported `SYN_STUDIOS_*` paths.
- `NODE_PATH` supports the CommonJS bridge only. Static ESM imports do not resolve managed packages from `NODE_PATH`.
- A successful capability check proves tool availability, not artifact quality. Binary release still requires structural, computational, provenance, and visual review against the artifact's own authority.
