import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "C:/Users/long0/Documents/Codex/2026-08-18/to/SapoInvoiceDesktop/test-output/v4-receive.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const inspected = await workbook.inspect({
  kind: "workbook,sheet,table,region,formula",
  sheetId: "Sheet1",
  range: "A1:M10",
  maxChars: 15000,
  tableMaxRows: 14,
  tableMaxCols: 13,
  options: { maxResults: 100 },
});
console.log(inspected.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: "Sheet1", range: "A1:M10", scale: 1.5, format: "png" });
await fs.writeFile(
  "C:/Users/long0/Documents/Codex/2026-08-18/to/SapoInvoiceDesktop/test-output/v4-receive.png",
  new Uint8Array(await preview.arrayBuffer()),
);
