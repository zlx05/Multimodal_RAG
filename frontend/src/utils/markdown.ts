import { marked } from "marked";
import katex from "katex";
import DOMPurify from "dompurify";
import "katex/dist/katex.min.css";

// 私有区字符作占位符边界：正文中几乎不可能出现，避免与真实内容碰撞。
const MATH_START = "";
const MATH_END = "";

function renderTex(source: string, displayMode: boolean): string {
  try {
    return katex.renderToString(source, {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    return source;
  }
}

/** 把回答/来源里的 LaTeX（$..$ / $$..$$ / \\(..\\) / \\[..\\]）与 Markdown 渲染成安全 HTML。 */
export function renderMarkdown(raw: string): string {
  if (!raw) return "";

  // 1. 先提取数学公式，避免 marked 误处理 $ 与反斜杠
  const math: string[] = [];
  let text = raw;
  const stash = (tex: string, displayMode: boolean) => {
    math.push(renderTex(tex.trim(), displayMode));
    return `${MATH_START}${math.length - 1}${MATH_END}`;
  };

  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, tex) => stash(tex, true)); // 行间 $$...$$
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_m, tex) => stash(tex, true)); // 行间 \[...\]
  // 行内 $...$：内容不以空格开头/结尾，避免误伤普通美元金额
  text = text.replace(/(?<!\$)\$([^\s$][^$\n]*?[^\s$])\$(?!\$)/g, (_m, tex) => stash(tex, false));
  text = text.replace(/\\\(([\s\S]+?)\\\)/g, (_m, tex) => stash(tex, false)); // 行内 \(...\)

  // 2. Markdown → HTML（gfm 表格 / 自动链接；breaks 让单个换行转 <br>，贴合聊天场景）
  const html = marked.parse(text, { gfm: true, breaks: true, async: false }) as string;

  // 3. 净化：回答/来源可能含原始 HTML（来源本身就是 HTML 文档），防 XSS
  const clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });

  // 4. 还原公式（katex 输出本身安全，无需再净化）
  return clean.replace(new RegExp(`${MATH_START}(\\d+)${MATH_END}`, "g"), (_m, i) => math[Number(i)] ?? "");
}
