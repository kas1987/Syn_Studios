import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

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
  range: "Checks!A1:D12",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
});

await fs.writeFile(
  outputPath,
  ["# OVERVIEW", overview.ndjson, "# FORMULA_ERRORS", errors.ndjson, "# CHECKS", checks.ndjson, ""].join("\n"),
  "utf8",
);
