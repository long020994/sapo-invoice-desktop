import fs from "node:fs/promises";
import { Workbook } from "@oai/artifact-tool";

const source = "C:/Users/long0/Documents/Codex/2026-08-18/to/SapoInvoiceDesktop/test-output/v6-bulk-sku.csv";
const workbook = await Workbook.fromCSV(await fs.readFile(source, "utf8"), { sheetName: "Sapo" });
const checked = await workbook.inspect({
  kind: "table",
  range: "A1:AF6",
  include: "values,formulas",
  tableMaxRows: 6,
  tableMaxCols: 32,
});
console.log(checked.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 20 },
});
console.log(errors.ndjson);
