import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const managedModules = process.env.SYN_STUDIOS_NODE_MODULES;
if (!managedModules) throw new Error("activate docs/DOCUMENT_STACK.md before verifying");
const artifactTool = await import(pathToFileURL(path.join(managedModules, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href);
const { FileBlob, SpreadsheetFile } = artifactTool;

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: verify_close_template.mjs <input.xlsx> <output.ndjson>");

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const overview = await workbook.inspect({
  kind: "sheet,formula",
  maxChars: 16000,
  options: { maxResults: 150 },
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
const checks = await workbook.inspect({
  kind: "table",
  range: "Checks!A1:D16",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
});

await fs.writeFile(
  outputPath,
  ["# OVERVIEW", overview.ndjson, "# FORMULA_ERRORS", errors.ndjson, "# CHECKS", checks.ndjson, ""].join("\n"),
  "utf8",
);
await fs.rm(`${inputPath}.inspect.ndjson`, { force: true });
