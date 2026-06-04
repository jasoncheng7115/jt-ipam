/**
 * 表格匯出（零相依）：CSV / Markdown / PDF(列印) / ODS / ODT。
 *
 * ODS/ODT 是真正的 OpenDocument（zip 容器）：自己手刻 STORE 模式 zip + CRC32，
 * 不引入 JSZip 等相依。PDF 走瀏覽器列印（開新視窗 → 列印 → 另存 PDF），同樣零相依。
 */

export type ExportFormat = "csv" | "txt" | "md" | "pdf" | "ods" | "odt" | "xlsx";

export interface ExportColumn {
  key: string;
  label: string;
}

// ── 下載 ──
function download(filename: string, data: Blob | Uint8Array, mime: string) {
  const blob = data instanceof Blob ? data : new Blob([data as BlobPart], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function cell(row: Record<string, any>, key: string): string {
  const v = row[key];
  if (v == null) return "";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function xmlEscape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ── CSV ──
function toCSV(cols: ExportColumn[], rows: Record<string, any>[]): string {
  const esc = (s: string) => `"${s.replace(/"/g, '""')}"`;
  const head = cols.map((c) => esc(c.label)).join(",");
  const body = rows.map((r) => cols.map((c) => esc(cell(r, c.key))).join(",")).join("\r\n");
  return "﻿" + head + "\r\n" + body;   // BOM → Excel 正確辨識 UTF-8
}

// ── Markdown ──
function toMarkdown(cols: ExportColumn[], rows: Record<string, any>[]): string {
  const esc = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
  const head = "| " + cols.map((c) => esc(c.label)).join(" | ") + " |";
  const sep = "| " + cols.map(() => "---").join(" | ") + " |";
  const body = rows.map((r) => "| " + cols.map((c) => esc(cell(r, c.key))).join(" | ") + " |").join("\n");
  return [head, sep, body].join("\n") + "\n";
}

// ── TXT（tab 分隔） ──
function toTXT(cols: ExportColumn[], rows: Record<string, any>[]): string {
  const clean = (s: string) => s.replace(/[\t\n\r]/g, " ");
  const head = cols.map((c) => clean(c.label)).join("\t");
  const body = rows.map((r) => cols.map((c) => clean(cell(r, c.key))).join("\t")).join("\n");
  return head + "\n" + body + "\n";
}

// ── XLSX（最小 OOXML，零相依；字串走 inlineStr） ──
function exportXLSX(filename: string, cols: ExportColumn[], rows: Record<string, any>[]) {
  const enc = new TextEncoder();
  const cellXml = (v: string) => `<c t="inlineStr"><is><t xml:space="preserve">${xmlEscape(v)}</t></is></c>`;
  const rowXml = (vals: string[]) => `<row>${vals.map(cellXml).join("")}</row>`;
  const sheetRows = [
    rowXml(cols.map((c) => c.label)),
    ...rows.map((r) => rowXml(cols.map((c) => cell(r, c.key)))),
  ].join("");
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + `<sheetData>${sheetRows}</sheetData></worksheet>`;
  const workbook = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"`
    + ` xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`
    + `<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>`;
  const wbRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>`
    + `</Relationships>`;
  const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>`
    + `</Relationships>`;
  const contentTypes = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
    + `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`
    + `<Default Extension="xml" ContentType="application/xml"/>`
    + `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>`
    + `<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
    + `</Types>`;
  const zip = zipStore([
    { name: "[Content_Types].xml", data: enc.encode(contentTypes) },
    { name: "_rels/.rels", data: enc.encode(rootRels) },
    { name: "xl/workbook.xml", data: enc.encode(workbook) },
    { name: "xl/_rels/workbook.xml.rels", data: enc.encode(wbRels) },
    { name: "xl/worksheets/sheet1.xml", data: enc.encode(sheet) },
  ]);
  download(filename, zip, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
}

// ── PDF（瀏覽器列印） ──
function exportPDF(title: string, cols: ExportColumn[], rows: Record<string, any>[]) {
  const head = cols.map((c) => `<th>${xmlEscape(c.label)}</th>`).join("");
  const body = rows.map((r) =>
    "<tr>" + cols.map((c) => `<td>${xmlEscape(cell(r, c.key))}</td>`).join("") + "</tr>",
  ).join("");
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${xmlEscape(title)}</title>
    <style>
      body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;padding:16px;font-size:12px}
      h1{font-size:16px}
      table{border-collapse:collapse;width:100%}
      th,td{border:1px solid #999;padding:4px 6px;text-align:left;word-break:break-all}
      th{background:#eee}
      @media print{@page{margin:12mm}}
    </style></head><body>
    <h1>${xmlEscape(title)}</h1>
    <table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    <script>window.onload=function(){window.print();}<\/script>
    </body></html>`;
  const w = window.open("", "_blank");
  if (!w) { throw new Error("popup blocked"); }
  w.document.write(html);
  w.document.close();
}

// ── 手刻 ZIP（STORE 模式）給 ODF 用 ──
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? (0xEDB88320 ^ (c >>> 1)) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf: Uint8Array): number {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
function zipStore(files: { name: string; data: Uint8Array }[]): Uint8Array {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const f of files) {
    const nameB = enc.encode(f.name);
    const crc = crc32(f.data);
    const size = f.data.length;
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(4, 20, true);
    lh.setUint16(8, 0, true);           // method = store
    lh.setUint16(10, 0, true); lh.setUint16(12, 0x21, true);  // 1980-01-01
    lh.setUint32(14, crc, true);
    lh.setUint32(18, size, true);
    lh.setUint32(22, size, true);
    lh.setUint16(26, nameB.length, true);
    parts.push(new Uint8Array(lh.buffer), nameB, f.data);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true);
    ch.setUint16(4, 20, true); ch.setUint16(6, 20, true);
    ch.setUint16(8, 0, true);           // method = store
    ch.setUint16(10, 0, true); ch.setUint16(12, 0x21, true);
    ch.setUint32(16, crc, true);
    ch.setUint32(20, size, true);
    ch.setUint32(24, size, true);
    ch.setUint16(28, nameB.length, true);
    ch.setUint32(42, offset, true);
    central.push(new Uint8Array(ch.buffer), nameB);
    offset += 30 + nameB.length + size;
  }
  const centralStart = offset;
  let centralSize = 0;
  for (const c of central) centralSize += c.length;
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, files.length, true);
  eocd.setUint16(10, files.length, true);
  eocd.setUint32(12, centralSize, true);
  eocd.setUint32(16, centralStart, true);
  const all = [...parts, ...central, new Uint8Array(eocd.buffer)];
  let total = 0;
  for (const a of all) total += a.length;
  const out = new Uint8Array(total);
  let p = 0;
  for (const a of all) { out.set(a, p); p += a.length; }
  return out;
}

const ODF_MANIFEST = (media: string) =>
  `<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="${media}"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>`;

function odfRows(cols: ExportColumn[], rows: Record<string, any>[]): string {
  const tcell = (s: string) =>
    `<table:table-cell office:value-type="string"><text:p>${xmlEscape(s)}</text:p></table:table-cell>`;
  const headRow = `<table:table-row>${cols.map((c) => tcell(c.label)).join("")}</table:table-row>`;
  const bodyRows = rows.map((r) =>
    `<table:table-row>${cols.map((c) => tcell(cell(r, c.key))).join("")}</table:table-row>`,
  ).join("");
  return headRow + bodyRows;
}

function odsContent(cols: ExportColumn[], rows: Record<string, any>[]): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body><office:spreadsheet><table:table table:name="Sheet1">
  ${odfRows(cols, rows)}
 </table:table></office:spreadsheet></office:body>
</office:document-content>`;
}

function odtContent(title: string, cols: ExportColumn[], rows: Record<string, any>[]): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body><office:text>
  <text:h>${xmlEscape(title)}</text:h>
  <table:table table:name="T1">
   <table:table-column table:number-columns-repeated="${cols.length}"/>
   ${odfRows(cols, rows)}
  </table:table>
 </office:text></office:body>
</office:document-content>`;
}

function exportODF(kind: "ods" | "odt", filename: string, title: string,
                   cols: ExportColumn[], rows: Record<string, any>[]) {
  const enc = new TextEncoder();
  const media = kind === "ods"
    ? "application/vnd.oasis.opendocument.spreadsheet"
    : "application/vnd.oasis.opendocument.text";
  const content = kind === "ods" ? odsContent(cols, rows) : odtContent(title, cols, rows);
  // mimetype 必須是第一個 entry 且 STORE（手刻 zip 全部 STORE，滿足此要求）
  const zip = zipStore([
    { name: "mimetype", data: enc.encode(media) },
    { name: "content.xml", data: enc.encode(content) },
    { name: "META-INF/manifest.xml", data: enc.encode(ODF_MANIFEST(media)) },
  ]);
  download(filename, zip, media);
}

/** 主入口：依格式匯出。filename 不含副檔名。 */
export function exportTable(
  format: ExportFormat,
  filenameBase: string,
  cols: ExportColumn[],
  rows: Record<string, any>[],
  title?: string,
) {
  const ttl = title || filenameBase;
  switch (format) {
    case "csv":
      download(`${filenameBase}.csv`, toCSVBlob(cols, rows), "text/csv;charset=utf-8");
      return;
    case "txt":
      download(`${filenameBase}.txt`, new TextEncoder().encode(toTXT(cols, rows)), "text/plain;charset=utf-8");
      return;
    case "xlsx":
      exportXLSX(`${filenameBase}.xlsx`, cols, rows);
      return;
    case "md":
      download(`${filenameBase}.md`, new TextEncoder().encode(toMarkdown(cols, rows)), "text/markdown");
      return;
    case "pdf":
      exportPDF(ttl, cols, rows);
      return;
    case "ods":
      exportODF("ods", `${filenameBase}.ods`, ttl, cols, rows);
      return;
    case "odt":
      exportODF("odt", `${filenameBase}.odt`, ttl, cols, rows);
      return;
  }
}

function toCSVBlob(cols: ExportColumn[], rows: Record<string, any>[]): Blob {
  return new Blob([toCSV(cols, rows)], { type: "text/csv;charset=utf-8" });
}

/** 從 naive DataTable columns 萃取可匯出的 {key,label}（略過 selection / 操作欄）。 */
export function columnsForExport(tableColumns: any[]): ExportColumn[] {
  const out: ExportColumn[] = [];
  for (const c of tableColumns) {
    if (!c || c.type === "selection" || c.type === "expand") continue;
    if (!c.key || c.key === "actions") continue;
    // title 可能是字串或 () => string（i18n 欄位多為函式）→ 呼叫取翻譯後標題，失敗才退回 key
    let label: unknown = c.title;
    if (typeof c.title === "function") {
      try { label = c.title(); } catch { label = c.key; }
    }
    if (label == null || typeof label === "object") label = c.key;
    out.push({ key: String(c.key), label: String(label) });
  }
  return out;
}
