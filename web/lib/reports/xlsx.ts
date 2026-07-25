// A dependency-free .xlsx writer. An xlsx is a ZIP of OOXML parts; rather than
// pull a heavy library into a public repo, we emit the handful of parts Excel
// needs and pack them with a minimal stored (uncompressed) ZIP + real CRC-32.
//
// Design choices that keep it robust:
//  - inline strings (`t="inlineStr"`), so there is no shared-strings table to
//    keep in sync — every cell is self-contained.
//  - metric values written as numeric cells, so Excel sorts and sums them.
//  - the header row frozen and bold via a tiny styles part.
// Only what Excel actually requires is included; the parts and relationship
// URIs follow ECMA-376 / the Open Packaging Conventions.

import { type ExportMatrix } from "./export";

const enc = new TextEncoder();

// ── CRC-32 (IEEE 802.3), table-driven ───────────────────────────────────────
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++)
    c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// ── Stored (method 0) ZIP container ──────────────────────────────────────────
// Returns an ArrayBuffer-backed view so it satisfies the fetch `BodyInit` type.
function zip(files: { name: string; data: Uint8Array }[]): Uint8Array<ArrayBuffer> {
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const f of files) {
    const nameBytes = enc.encode(f.name);
    const crc = crc32(f.data);
    const size = f.data.length;

    const lh = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(lh.buffer);
    lv.setUint32(0, 0x04034b50, true); // local file header signature
    lv.setUint16(4, 20, true); // version needed
    lv.setUint16(6, 0, true); // flags
    lv.setUint16(8, 0, true); // method: stored
    lv.setUint16(10, 0, true); // mod time (fixed — deterministic output)
    lv.setUint16(12, 0x21, true); // mod date (1980-01-01)
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true); // compressed size == size (stored)
    lv.setUint32(22, size, true); // uncompressed size
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true); // extra length
    lh.set(nameBytes, 30);
    chunks.push(lh, f.data);

    const ch = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(ch.buffer);
    cv.setUint32(0, 0x02014b50, true); // central dir header signature
    cv.setUint16(4, 20, true); // version made by
    cv.setUint16(6, 20, true); // version needed
    cv.setUint16(8, 0, true); // flags
    cv.setUint16(10, 0, true); // method
    cv.setUint16(12, 0, true); // time
    cv.setUint16(14, 0x21, true); // date
    cv.setUint32(16, crc, true);
    cv.setUint32(20, size, true);
    cv.setUint32(24, size, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true); // extra length
    cv.setUint16(32, 0, true); // comment length
    cv.setUint16(34, 0, true); // disk number start
    cv.setUint16(36, 0, true); // internal attrs
    cv.setUint32(38, 0, true); // external attrs
    cv.setUint32(42, offset, true); // relative offset of local header
    ch.set(nameBytes, 46);
    central.push(ch);

    offset += lh.length + size;
  }

  const centralSize = central.reduce((a, c) => a + c.length, 0);
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true); // end of central dir signature
  ev.setUint16(4, 0, true); // disk number
  ev.setUint16(6, 0, true); // disk with central dir
  ev.setUint16(8, files.length, true); // entries this disk
  ev.setUint16(10, files.length, true); // total entries
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true); // central dir offset
  ev.setUint16(20, 0, true); // comment length

  const all = [...chunks, ...central, eocd];
  const total = all.reduce((a, c) => a + c.length, 0);
  const out = new Uint8Array(total);
  let p = 0;
  for (const c of all) {
    out.set(c, p);
    p += c.length;
  }
  return out;
}

// ── OOXML parts ──────────────────────────────────────────────────────────────
function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 0 → A, 25 → Z, 26 → AA … */
function colName(i: number): string {
  let s = "";
  let n = i + 1;
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function cellXml(
  col: number,
  rowNum: number,
  v: string | number | null,
  bold: boolean,
): string {
  if (v === null || v === undefined) return "";
  const ref = `${colName(col)}${rowNum}`;
  const s = bold ? ' s="1"' : "";
  if (typeof v === "number" && Number.isFinite(v)) {
    return `<c r="${ref}"${s}><v>${v}</v></c>`;
  }
  return `<c r="${ref}"${s} t="inlineStr"><is><t xml:space="preserve">${xmlEscape(String(v))}</t></is></c>`;
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>`;

const ROOT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`;

const WORKBOOK = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets>
</workbook>`;

const WORKBOOK_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;

// Two cell formats: 0 = default, 1 = bold (the header). The empty fills/borders
// and the gray125 fill at index 1 are the entries Excel expects to exist.
const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>`;

function sheetXml(matrix: ExportMatrix): string {
  const nCols = matrix.headers.length;
  const nRows = matrix.rows.length + 1; // + header row
  const dimension = `A1:${colName(Math.max(nCols - 1, 0))}${Math.max(nRows, 1)}`;

  const rowsXml: string[] = [];
  // Header row (bold), frozen below.
  rowsXml.push(
    `<row r="1">${matrix.headers.map((h, c) => cellXml(c, 1, h, true)).join("")}</row>`,
  );
  matrix.rows.forEach((row, i) => {
    const rowNum = i + 2;
    rowsXml.push(
      `<row r="${rowNum}">${row.map((v, c) => cellXml(c, rowNum, v, false)).join("")}</row>`,
    );
  });

  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<dimension ref="${dimension}"/>
<sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>
<sheetFormatPr defaultRowHeight="15"/>
<sheetData>${rowsXml.join("")}</sheetData>
</worksheet>`;
}

/** Build a single-sheet .xlsx workbook from the export matrix. */
export function buildXlsx(matrix: ExportMatrix): Uint8Array<ArrayBuffer> {
  const files = [
    { name: "[Content_Types].xml", data: enc.encode(CONTENT_TYPES) },
    { name: "_rels/.rels", data: enc.encode(ROOT_RELS) },
    { name: "xl/workbook.xml", data: enc.encode(WORKBOOK) },
    { name: "xl/_rels/workbook.xml.rels", data: enc.encode(WORKBOOK_RELS) },
    { name: "xl/styles.xml", data: enc.encode(STYLES) },
    { name: "xl/worksheets/sheet1.xml", data: enc.encode(sheetXml(matrix)) },
  ];
  return zip(files);
}
