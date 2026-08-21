import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "C:/Users/long0/Downloads/receive_inventories_import_template_with_lot_date-abwn5zz4.xlsx";
const outputDir = "C:/Users/long0/Documents/Codex/2026-08-18/to/SapoInvoiceDesktop/template-inspection";
await fs.mkdir(outputDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const overview = await workbook.inspect({
  kind: "workbook,sheet,table,definedName",
  maxChars: 12000,
  tableMaxRows: 15,
  tableMaxCols: 20,
  tableMaxCellChars: 200,
});
console.log(overview.ndjson);
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 5000 });
console.log(sheets.ndjson);
for (const sheet of workbook.worksheets.items) {
  const region = await workbook.inspect({
    kind: "region,computedStyle,formula",
    sheetId: sheet.name,
    range: "A1:Z30",
    maxChars: 15000,
    options: { maxResults: 100 },
  });
  console.log(region.ndjson);
  const preview = await workbook.render({ sheetName: sheet.name, range: "A1:Z30", scale: 1.5, format: "png" });
  await fs.writeFile(`${outputDir}/${sheet.name.replaceAll(/[\\/:*?\"<>|]/g, "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
}
