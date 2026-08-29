import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const managedModules = process.env.SYN_STUDIOS_NODE_MODULES;
if (!managedModules) throw new Error("activate docs/DOCUMENT_STACK.md before building");
const artifactTool = await import(pathToFileURL(path.join(managedModules, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href);
const { SpreadsheetFile, Workbook } = artifactTool;

const outputPath = process.argv[2];
if (!outputPath) throw new Error("usage: build_close_template.mjs <output.xlsx>");

const workbook = Workbook.create();
const navy = "#17365D";
const blue = "#D9EAF7";
const input = "#FFF2CC";
const pale = "#F3F6F9";
const green = "#E2F0D9";
const red = "#FCE4D6";

function title(sheet, text, subtitle) {
  sheet.showGridLines = false;
  sheet.getRange("A1:H1").merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange("A1:H1").format = {
    fill: navy,
    font: { bold: true, color: "#FFFFFF", size: 16 },
    rowHeight: 28,
  };
  sheet.getRange("A2:H2").merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange("A2:H2").format = {
    fill: pale,
    font: { italic: true, color: "#44546A" },
    wrapText: true,
    rowHeight: 30,
  };
}

function header(range) {
  range.format = {
    fill: blue,
    font: { bold: true, color: navy },
    borders: { preset: "all", style: "thin", color: "#B4C6E7" },
    wrapText: true,
  };
}

const control = workbook.worksheets.add("Close_Control");
title(control, "Monthly Close & Reconciliation", "Internal working file | supporting analysis | approval is evidenced separately");
control.getRange("A4:B9").values = [
  ["Close file field", "Value"],
  ["Organization", "{{organization_name}}"],
  ["Close period", "{{close_period_end}}"],
  ["Prepared by role", "{{preparer_role}}"],
  ["Reviewed by role", "{{reviewer_role}}"],
  ["File status", "Working"],
];
header(control.getRange("A4:B4"));
control.getRange("B5:B8").format.fill = input;
control.getRange("A11:C16").values = [
  ["Close control", "Status", "Evidence reference"],
  ["Source population loaded", "Not started", null],
  ["Account mapping reviewed", "Not started", null],
  ["Reconciliation exceptions dispositioned", "Not started", null],
  ["Proposed entries balanced", "Not started", null],
  ["Controller review completed", "Not started", null],
];
header(control.getRange("A11:C11"));
control.getRange("B12:B16").dataValidation = { rule: { type: "list", values: ["Not started", "In progress", "Complete", "Not applicable"] } };
control.getRange("B12:C16").format.fill = input;
control.getRange("A:A").format.columnWidth = 34;
control.getRange("B:B").format.columnWidth = 24;
control.getRange("C:C").format.columnWidth = 44;

const source = workbook.worksheets.add("Source_Data");
title(source, "Source Data", "Paste or generate authorized transaction-level rows. Do not place conclusions in this source-system layer.");
source.getRange("A4:H4").values = [["Source Row ID", "Entity Code", "Account Code", "Transaction Date", "Description", "Debit", "Credit", "Status Code"]];
header(source.getRange("A4:H4"));
source.getRange("A5:H29").format.borders = { preset: "inside", style: "thin", color: "#E7E6E6" };
source.getRange("D5:D29").format.numberFormat = "yyyy-mm-dd";
source.getRange("F5:G29").format.numberFormat = '$#,##0.00;[Red]($#,##0.00);-';
source.getRange("A4:H29").format.wrapText = false;
source.freezePanes.freezeRows(4);
source.getRange("A:H").format.columnWidth = 18;
source.getRange("E:E").format.columnWidth = 34;
source.tables.add("A4:H29", true, "SourceDataTable").style = "TableStyleMedium2";

const map = workbook.worksheets.add("Account_Map");
title(map, "Account Mapping", "Map source codes to the reporting structure. Mapping fields are inputs, not inferred authority.");
map.getRange("A4:E4").values = [["Account Code", "Account Name", "Statement Group", "Reconciliation Owner Role", "Active?"]];
header(map.getRange("A4:E4"));
map.getRange("A5:E29").format.fill = input;
map.getRange("E5:E29").dataValidation = { rule: { type: "list", values: ["Yes", "No"] } };
map.freezePanes.freezeRows(4);
map.getRange("A:E").format.columnWidth = 24;
map.tables.add("A4:E29", true, "AccountMapTable").style = "TableStyleMedium2";

const recon = workbook.worksheets.add("Reconciliation");
title(recon, "Reconciliation", "Formula-driven comparison of source activity and ledger balance by mapped account.");
recon.getRange("A4:H4").values = [["Account Code", "Account Name", "Source Net", "Ledger Balance", "Difference", "Threshold", "Status", "Explanation / Action"]];
header(recon.getRange("A4:H4"));
recon.getRange("A5:B24").format.fill = input;
recon.getRange("D5:D24").format.fill = input;
recon.getRange("F5:F24").format.fill = input;
recon.getRange("H5:H24").format.fill = input;
recon.getRange("C5").formulas = [["=IF(A5=\"\",\"\",SUMIFS('Source_Data'!$F$5:$F$29,'Source_Data'!$C$5:$C$29,A5)-SUMIFS('Source_Data'!$G$5:$G$29,'Source_Data'!$C$5:$C$29,A5))"]];
recon.getRange("C5:C24").fillDown();
recon.getRange("E5").formulas = [["=IF(A5=\"\",\"\",C5-D5)"]];
recon.getRange("E5:E24").fillDown();
recon.getRange("G5").formulas = [["=IF(A5=\"\",\"\",IF(OR(D5=\"\",F5=\"\"),\"NOT READY\",IF(ABS(E5)<=F5,\"PASS\",\"REVIEW\")))"]];
recon.getRange("G5:G24").fillDown();
recon.getRange("C5:F24").format.numberFormat = '$#,##0.00;[Red]($#,##0.00);-';
recon.getRange("G5:G24").conditionalFormats.add("containsText", { text: "PASS", format: { fill: green, font: { color: "#375623" } } });
recon.getRange("G5:G24").conditionalFormats.add("containsText", { text: "REVIEW", format: { fill: red, font: { color: "#9C0006" } } });
recon.freezePanes.freezeRows(4);
recon.getRange("A:G").format.columnWidth = 18;
recon.getRange("H:H").format.columnWidth = 38;
recon.tables.add("A4:H24", true, "ReconciliationTable").style = "TableStyleMedium2";

const entries = workbook.worksheets.add("Proposed_Entries");
title(entries, "Proposed Entries", "Build entries only from resolved reconciliation differences and authorized adjustments.");
entries.getRange("A4:H4").values = [["Entry ID", "Account Code", "Description", "Debit", "Credit", "Source Reference", "Preparation Status", "Review Note"]];
header(entries.getRange("A4:H4"));
entries.getRange("A5:H19").format.fill = input;
entries.getRange("D5:E19").format.numberFormat = '$#,##0.00;[Red]($#,##0.00);-';
entries.getRange("G5:G19").dataValidation = { rule: { type: "list", values: ["Draft", "Ready for review", "Hold"] } };
entries.getRange("C:C").format.columnWidth = 32;
entries.getRange("A:B").format.columnWidth = 18;
entries.getRange("D:H").format.columnWidth = 20;
entries.freezePanes.freezeRows(4);
entries.tables.add("A4:H19", true, "ProposedEntriesTable").style = "TableStyleMedium2";

const prior = workbook.worksheets.add("Prior_Period");
title(prior, "Prior Period Comparison", "Optional comparison layer; prior-period data is contextual and must not override current authority.");
prior.getRange("A4:E4").values = [["Account Code", "Current Balance", "Prior Balance", "Change", "Context Note"]];
header(prior.getRange("A4:E4"));
prior.getRange("A5:C24").format.fill = input;
prior.getRange("E5:E24").format.fill = input;
prior.getRange("D5").formulas = [["=B5-C5"]];
prior.getRange("D5:D24").fillDown();
prior.getRange("B5:D24").format.numberFormat = '$#,##0.00;[Red]($#,##0.00);-';
prior.getRange("A:D").format.columnWidth = 20;
prior.getRange("E:E").format.columnWidth = 40;
prior.freezePanes.freezeRows(4);
prior.tables.add("A4:E24", true, "PriorPeriodTable").style = "TableStyleMedium2";

const exceptions = workbook.worksheets.add("Exceptions");
title(exceptions, "Exception Log", "Record only workflow-supported exceptions. Questions do not supply their own resolution.");
exceptions.getRange("A4:H4").values = [["Exception ID", "Detected In", "Question / Condition", "Authority Class", "Owner Role", "Due Date", "Status", "Resolution Reference"]];
header(exceptions.getRange("A4:H4"));
exceptions.getRange("A5:H19").format.fill = input;
exceptions.getRange("D5:D19").dataValidation = { rule: { type: "list", values: ["supporting", "contextual", "question-only", "superseded"] } };
exceptions.getRange("G5:G19").dataValidation = { rule: { type: "list", values: ["Open", "Resolved", "Retained"] } };
exceptions.getRange("F5:F19").format.numberFormat = "yyyy-mm-dd";
exceptions.getRange("A:B").format.columnWidth = 18;
exceptions.getRange("C:C").format.columnWidth = 38;
exceptions.getRange("D:H").format.columnWidth = 20;
exceptions.freezePanes.freezeRows(4);
exceptions.tables.add("A4:H19", true, "ExceptionsTable").style = "TableStyleMedium2";

const checks = workbook.worksheets.add("Checks");
title(checks, "Control Checks", "PASS indicates internal workbook checks only; it is not evidence of approval or release.");
checks.getRange("A4:D4").values = [["Check", "Result", "Delta / Count", "Where to fix"]];
header(checks.getRange("A4:D4"));
checks.getRange("A5:A11").values = [["Source population loaded"], ["Source debits equal source credits"], ["Account mapping loaded"], ["Reconciliation rows complete and passing"], ["Proposed entries balanced when present"], ["No open exceptions"], ["Required close fields populated"]];
checks.getRange("C5").formulas = [["=COUNTA('Source_Data'!$A$5:$A$29)"]];
checks.getRange("B5").formulas = [["=IF(C5=0,\"NOT READY\",\"PASS\")"]];
checks.getRange("C6").formulas = [["=SUM('Source_Data'!$F$5:$F$29)-SUM('Source_Data'!$G$5:$G$29)"]];
checks.getRange("B6").formulas = [["=IF(C5=0,\"NOT READY\",IF(ABS(C6)<0.005,\"PASS\",\"FAIL\"))"]];
checks.getRange("C7").formulas = [["=COUNTA('Account_Map'!$A$5:$A$29)"]];
checks.getRange("B7").formulas = [["=IF(C7=0,\"NOT READY\",\"PASS\")"]];
checks.getRange("C8").formulas = [["=COUNTA('Reconciliation'!$A$5:$A$24)-COUNTIF('Reconciliation'!$G$5:$G$24,\"PASS\")"]];
checks.getRange("B8").formulas = [["=IF(COUNTA('Reconciliation'!$A$5:$A$24)=0,\"NOT READY\",IF(C8=0,\"PASS\",\"FAIL\"))"]];
checks.getRange("C9").formulas = [["=SUM('Proposed_Entries'!$D$5:$D$19)-SUM('Proposed_Entries'!$E$5:$E$19)"]];
checks.getRange("B9").formulas = [["=IF(COUNTA('Proposed_Entries'!$A$5:$A$19)=0,\"NOT APPLICABLE\",IF(ABS(C9)<0.005,\"PASS\",\"FAIL\"))"]];
checks.getRange("C10").formulas = [["=COUNTIF('Exceptions'!$G$5:$G$19,\"Open\")"]];
checks.getRange("B10").formulas = [["=IF(COUNTA('Reconciliation'!$A$5:$A$24)=0,\"NOT READY\",IF(C10=0,\"PASS\",\"FAIL\"))"]];
checks.getRange("C11").formulas = [["=IF(OR(COUNTBLANK('Close_Control'!B5:B8)>0,LEFT('Close_Control'!B5,1)=\"{\",LEFT('Close_Control'!B6,1)=\"{\",LEFT('Close_Control'!B7,1)=\"{\",LEFT('Close_Control'!B8,1)=\"{\"),1,0)"]];
checks.getRange("B11").formulas = [["=IF(C11=0,\"PASS\",\"NOT READY\")"]];
checks.getRange("D5:D11").values = [["Source_Data"], ["Source_Data"], ["Account_Map"], ["Reconciliation"], ["Proposed_Entries"], ["Exceptions"], ["Close_Control"]];
checks.getRange("B5:B11").conditionalFormats.add("containsText", { text: "PASS", format: { fill: green, font: { color: "#375623", bold: true } } });
checks.getRange("B5:B11").conditionalFormats.add("containsText", { text: "FAIL", format: { fill: red, font: { color: "#9C0006", bold: true } } });
checks.getRange("B5:B11").conditionalFormats.add("containsText", { text: "NOT READY", format: { fill: input, font: { color: "#7F6000", bold: true } } });
checks.getRange("A13:B14").values = [["MODEL STATUS", "Formula"], ["Status", null]];
header(checks.getRange("A13:B13"));
checks.getRange("B14").formulas = [["=IF(AND(COUNTIF(B5:B11,\"NOT READY\")=0,COUNTIF(B5:B11,\"FAIL\")=0),\"PASS\",\"NOT READY\")"]];
checks.getRange("B14").format = { font: { bold: true, size: 14 }, fill: pale };
checks.getRange("A:A").format.columnWidth = 42;
checks.getRange("B:D").format.columnWidth = 22;
checks.freezePanes.freezeRows(4);

for (const sheet of workbook.worksheets.items) {
  const used = sheet.getUsedRange();
  if (used) used.format.font = { name: "Aptos", size: 10 };
}

const outputDir = path.dirname(outputPath);
await fs.mkdir(outputDir, { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
