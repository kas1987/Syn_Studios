import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);

try {
  const artifactTool = require("@oai/artifact-tool");
  let packageDirectory = dirname(require.resolve("@oai/artifact-tool"));
  let packageVersion = null;
  for (let depth = 0; depth < 8; depth += 1) {
    try {
      const manifest = JSON.parse(readFileSync(join(packageDirectory, "package.json"), "utf8"));
      if (manifest.name === "@oai/artifact-tool") {
        packageVersion = manifest.version;
        break;
      }
    } catch {}
    packageDirectory = dirname(packageDirectory);
  }
  const result = {
    available: true,
    spreadsheetFile: typeof artifactTool.SpreadsheetFile === "function",
    exportCount: Object.keys(artifactTool).length,
    version: packageVersion,
  };
  console.log(JSON.stringify(result));
  process.exit(result.spreadsheetFile && result.version ? 0 : 1);
} catch (error) {
  console.error(JSON.stringify({ available: false, error: String(error) }));
  process.exit(1);
}
