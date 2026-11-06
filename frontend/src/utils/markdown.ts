/**
 * 極簡、零相依的 Markdown → HTML(給 AI chat 本地 LLM 回應用)。
 *
 * 安全性：先把 & < > 跳脫，之後才插入我們自己產生的標籤，因此不會有 HTML 注入。
 * 只支援聊天常見語法：標題 / 粗體 / 斜體 / 行內 code / code fence / 連結 /
 * 有序與無序清單 / 段落與換行。不是完整 CommonMark，夠用即可。
 */

function escapeHtml(s: string): string {
  // 連引號一起跳脫，避免惡意 LLM/工具回應用 [x](https://a"onx=...) 之類做屬性注入 XSS
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * 模型常把箭頭寫成 LaTeX（「進階 $\rightarrow$ 電路」，使用者 2026-11-06 回報），我們不渲染數學式，
 * 原樣顯示就是一串怪字。只轉 `$\命令$` 這種單一個認得的符號；不認得的、一般的錢字號都不動。
 */
const LATEX_SYMBOLS: Record<string, string> = {
  rightarrow: "→", to: "→", leftarrow: "←", gets: "←", leftrightarrow: "↔", longrightarrow: "⟶",
  Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", implies: "⇒", mapsto: "↦",
  uparrow: "↑", downarrow: "↓", times: "×", cdot: "·", div: "÷", pm: "±",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", approx: "≈", infty: "∞",
  checkmark: "✓", ldots: "…", dots: "…", bullet: "•", degree: "°",
};
function latexSymbols(s: string): string {
  return s.replace(/\$\s*\\([A-Za-z]+)\s*(?:\{\})?\s*\$/g, (m, cmd: string) => LATEX_SYMBOLS[cmd] ?? m);
}

function inline(s: string): string {
  // 行內 code(先處理，避免內部被其它規則動到)
  s = s.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  // LaTeX 符號：程式碼裡的不動
  s = s.split(/(<code>[\s\S]*?<\/code>)/).map((part, i) => (i % 2 ? part : latexSymbols(part))).join("");
  // 連結 [text](http...)
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_m, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  // 粗體 **x**
  s = s.replace(/\*\*([^*]+)\*\*/g, (_m, c) => `<strong>${c}</strong>`);
  // 斜體 *x*
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, (_m, p, c) => `${p}<em>${c}</em>`);
  // 斜體 _x_ —— **字中間的底線不算強調**（前後都要是非文字字元）。
  // CommonMark 就是為了 snake_case 這種識別字才這樣訂：少了這個條件，
  // AI 回應裡的 `recent_ip_changes` 會被渲染成 recent<em>ip</em>changes（客戶回報）。
  s = s.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, (_m, p, c) => `${p}<em>${c}</em>`);
  return s;
}

/**
 * 只做行內語法（code / 連結 / 粗體 / 斜體），不產生段落、清單這些區塊標籤。
 *
 * 給「模型寫的一句話要放進既有 <h3>／<p> 裡」的地方用 —— 例如 AI 巡檢的標題、
 * 說明與建議。用區塊版的話會生出巢狀 <p>，那是無效 HTML，瀏覽器會自行拆開，
 * 版面就跑掉了。
 */
export function renderInlineMarkdown(src: string): string {
  if (!src) return "";
  return inline(escapeHtml(src.replace(/\r\n/g, "\n")));
}

export function renderMarkdown(src: string): string {
  if (!src) return "";
  const text = escapeHtml(src.replace(/\r\n/g, "\n"));
  const lines = text.split("\n");
  const out: string[] = [];

  let inCode = false;
  let codeBuf: string[] = [];
  // 用堆疊追蹤巢狀清單（依縮排深度），讓 AI 回應的多層清單保留縮排
  const stack: { type: "ul" | "ol"; indent: number }[] = [];
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    }
  };
  const closeList = () => { while (stack.length) out.push(`</${stack.pop()!.type}>`); };
  const indentOf = (s: string): number => (/^(\s*)/.exec(s)?.[1] ?? "").replace(/\t/g, "  ").length;
  // GFM 表格：拆列成 cells（去頭尾 | 後以 | 切，trim 每格）；分隔列只由 | - : 空白組成且含 -
  const splitRow = (s: string): string[] =>
    s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const isTableSep = (s: string): boolean => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(s) && s.includes("-") && s.includes("|");
  const listItem = (indent: number, type: "ul" | "ol", content: string) => {
    flushPara();
    while (stack.length && stack[stack.length - 1].indent > indent) {
      out.push(`</${stack.pop()!.type}>`);
    }
    const top = stack[stack.length - 1];
    if (!top || top.indent < indent) {
      out.push(`<${type}>`); stack.push({ type, indent });
    } else if (top.indent === indent && top.type !== type) {
      out.push(`</${stack.pop()!.type}>`); out.push(`<${type}>`); stack.push({ type, indent });
    }
    out.push(`<li>${inline(content)}</li>`);
  };

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];

    // code fence
    if (/^\s*```/.test(line)) {
      if (inCode) {
        out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
        codeBuf = []; inCode = false;
      } else {
        flushPara(); closeList(); inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // GFM 表格：含 | 的表頭列 + 下一列是分隔列（|---|---|）→ 整段吃成 <table>
    if (line.includes("|") && li + 1 < lines.length && isTableSep(lines[li + 1])) {
      flushPara(); closeList();
      const headers = splitRow(line);
      li += 2; // 跳過表頭與分隔列
      const bodyRows: string[][] = [];
      while (li < lines.length && lines[li].trim() && lines[li].includes("|")) {
        bodyRows.push(splitRow(lines[li]));
        li++;
      }
      li--; // 修正：for 迴圈會再 ++
      const th = headers.map((c) => `<th>${inline(c)}</th>`).join("");
      const body = bodyRows.map((cells) => {
        const tds = headers.map((_, ci) => `<td>${inline(cells[ci] ?? "")}</td>`).join("");
        return `<tr>${tds}</tr>`;
      }).join("");
      out.push(`<table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`);
      continue;
    }

    // 空行 → 段落 / 清單分界
    if (!line.trim()) { flushPara(); closeList(); continue; }

    // 標題
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flushPara(); closeList();
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      continue;
    }

    // 有序清單（依縮排巢狀）
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ol) { listItem(indentOf(line), "ol", ol[1]); continue; }
    // 無序清單（依縮排巢狀）
    const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (ul) { listItem(indentOf(line), "ul", ul[1]); continue; }

    // 一般段落行
    closeList();
    para.push(line.trim());
  }

  if (inCode) out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
  flushPara();
  closeList();
  return out.join("\n");
}
