/*!
 * VeroRun Shared Markdown Renderer (markdown-view.js)
 * --------------------------------------------------
 * Exposes window.VeroMarkdown with:
 *   render(markdownText) -> safe HTML string
 *   runMermaid(container) -> Promise (renders .mermaid nodes into SVG)
 * Supports: fenced code blocks, Mermaid diagrams, GFM tables, headings,
 *           blockquotes, nested ordered/unordered lists, hr, images, links,
 *           bold/italic/strikethrough, inline code, paragraphs with hard breaks.
 * Security: HTML is escaped before conversion; Mermaid runs securityLevel 'strict';
 *           link protocols are whitelisted (http/https/mailto/relative anchors).
 * No external network requests. Safe for intranet/offline deployment.
 */
(function () {
  'use strict';

  var V = {};

  /* ── HTML escaping ── */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  V.esc = esc;

  /* ── Inline markdown (input must already be HTML-escaped) ── */
  var INLINE_CODE = /`([^`]+?)`/g;

  function inline(text) {
    var codeStore = [];
    // 1. inline code first (content kept literal, no further parsing)
    text = text.replace(INLINE_CODE, function (_m, code) {
      codeStore.push('<code class="vp-code">' + code + '</code>');
      return '\u0000IC' + (codeStore.length - 1) + '\u0000';
    });
    // 2. images
    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      function (_m, alt, url, title) {
        if (!safeUrl(url)) return '';
        var t = title ? ' title="' + title + '"' : '';
        return '<img class="vp-img" src="' + url + '" alt="' + alt + '"' + t + '>';
      });
    // 3. links
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      function (_m, label, url, title) {
        if (!safeUrl(url)) return label;
        var t = title ? ' title="' + title + '"' : '';
        var ext = /^https?:/.test(url) ? ' target="_blank" rel="noopener noreferrer"' : '';
        return '<a class="vp-link" href="' + url + '"' + t + ext + '>' + label + '</a>';
      });
    // 4. bold / italic / strikethrough
    text = text.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__([^_]+?)__/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
    text = text.replace(/~~([^~]+?)~~/g, '<del>$1</del>');
    // restore inline code
    text = text.replace(/\u0000IC(\d+)\u0000/g, function (_m, i) { return codeStore[+i]; });
    return text;
  }

  function safeUrl(url) {
    if (!url) return false;
    if (/^(https?:|mailto:|\/|#|\.\/|\.\.\/)/.test(url)) return true;
    return false; // blocks javascript:, data:, vbscript:, etc.
  }

  /* ── Table parsing ── */
  function splitRow(line) {
    var t = line.trim();
    if (t.charAt(0) === '|') t = t.slice(1);
    if (t.charAt(t.length - 1) === '|') t = t.slice(0, -1);
    return t.split('|').map(function (c) { return c.trim(); });
  }
  function isSepRow(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
  }
  function renderTable(rows, start) {
    var header = splitRow(rows[start]);
    var aligns = splitRow(rows[start + 1]).map(function (c) {
      var l = c.charAt(0) === ':', r = c.charAt(c.length - 1) === ':';
      return l && r ? 'center' : (r ? 'right' : (l ? 'left' : ''));
    });
    var h = '<div class="vp-table-wrap"><table class="vp-table"><thead><tr>';
    header.forEach(function (c, idx) {
      var a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
      h += '<th' + a + '>' + inline(esc(c)) + '</th>';
    });
    h += '</tr></thead><tbody>';
    var r = start + 2;
    while (r < rows.length && /\|\s*$|^\s*\|/.test(rows[r]) && rows[r].trim() !== '') {
      var cells = splitRow(rows[r]);
      h += '<tr>';
      cells.forEach(function (c, idx) {
        var a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
        h += '<td' + a + '>' + inline(esc(c)) + '</td>';
      });
      h += '</tr>';
      r++;
    }
    h += '</tbody></table></div>';
    return { html: h, next: r };
  }

  /* ── List parsing (supports nesting by indentation) ── */
  var ITEM_RE = /^(\s*)([-*+]|\d+\.)\s+(.*)$/;
  function indentOf(line) {
    var m = line.match(/^(\s*)/);
    return m ? m[1].length : 0;
  }

  function parseList(lines, start, baseIndent) {
    var first = ITEM_RE.exec(lines[start]);
    var ordered = /\d+\./.test(first[2]);
    var tag = ordered ? 'ol' : 'ul';
    var html = '<' + tag + ' class="vp-list">';
    var i = start;
    while (i < lines.length) {
      var line = lines[i];
      if (line.trim() === '') { i++; continue; }
      var m = ITEM_RE.exec(line);
      if (!m) break;
      var ind = m[1].length;
      if (ind < baseIndent) break;
      if (ind > baseIndent) break; // deeper level handled as child block
      html += '<li>' + inline(esc(m[3]));
      i++;
      // collect this item's block (lines indented beyond baseIndent)
      var block = [];
      while (i < lines.length) {
        var l2 = lines[i];
        if (l2.trim() === '') { block.push(l2); i++; continue; }
        if (indentOf(l2) > baseIndent) { block.push(l2); i++; } else break;
      }
      while (block.length && block[0].trim() === '') block.shift();
      while (block.length && block[block.length - 1].trim() === '') block.pop();
      if (block.length) {
        if (ITEM_RE.exec(block[findFirstNonBlank(block)] || '')) {
          html += renderListRegion(block);
        } else {
          var dedent = Math.min.apply(null, block.filter(function (b) { return b.trim() !== ''; }).map(indentOf));
          var text = block.map(function (b) { return b.slice(dedent); }).join('<br>');
          html += '<div class="vp-li-cont">' + inline(esc(text)) + '</div>';
        }
      }
      html += '</li>';
    }
    html += '</' + tag + '>';
    return { html: html, next: i };
  }
  function findFirstNonBlank(arr) {
    for (var k = 0; k < arr.length; k++) if (arr[k].trim() !== '') return k;
    return -1;
  }
  function renderListRegion(lines) {
    var minIndent = Infinity;
    lines.forEach(function (l) {
      var m = ITEM_RE.exec(l);
      if (m) minIndent = Math.min(minIndent, m[1].length);
    });
    if (!isFinite(minIndent)) return '';
    var html = '';
    var i = 0;
    while (i < lines.length) {
      if (lines[i].trim() === '') { i++; continue; }
      var res = parseList(lines, i, minIndent);
      html += res.html;
      i = res.next;
    }
    return html;
  }

  /* ── Block parser ── */
  function render(md) {
    if (!md) return '';
    var lines = String(md).replace(/\r\n?/g, '\n').split('\n');
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];

      // blank
      if (line.trim() === '') { i++; continue; }

      // fenced code / mermaid
      var fence = line.match(/^(\s*)```([\w-]*)\s*$/);
      if (fence) {
        var lang = fence[2] || '';
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // consume closing fence
        var code = buf.join('\n');
        if (lang === 'mermaid') {
          out.push('<div class="vp-mermaid mermaid">' + esc(code) + '</div>');
        } else {
          var label = lang ? '<span class="vp-pre-lang">' + esc(lang) + '</span>' : '';
          out.push('<pre class="vp-pre">' + label + '<code>' + esc(code) + '</code></pre>');
        }
        continue;
      }

      // table
      if (i + 1 < lines.length && line.indexOf('|') !== -1 && isSepRow(lines[i + 1])) {
        var t = renderTable(lines, i);
        out.push(t.html);
        i = t.next;
        continue;
      }

      // heading
      var hd = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (hd) {
        var lvl = hd[1].length;
        out.push('<h' + lvl + ' class="vp-h vp-h' + lvl + '">' + inline(esc(hd[2])) + '</h' + lvl + '>');
        i++;
        continue;
      }

      // hr
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
        out.push('<hr class="vp-hr">');
        i++;
        continue;
      }

      // blockquote
      if (/^\s*>\s?/.test(line)) {
        var qb = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          qb.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        out.push('<blockquote class="vp-quote">' + render(qb.join('\n')) + '</blockquote>');
        continue;
      }

      // list region
      if (ITEM_RE.exec(line)) {
        var region = [];
        while (i < lines.length) {
          var lx = lines[i];
          if (lx.trim() === '') {
            // blank ends list only if next non-blank is not indented/item
            var j = i + 1;
            while (j < lines.length && lines[j].trim() === '') j++;
            if (j < lines.length) {
              var nx = lines[j];
              var nm = ITEM_RE.exec(nx);
              if (nm || indentOf(nx) > 0) { region.push(lx); i++; continue; }
            }
            break;
          }
          if (ITEM_RE.exec(lx) || indentOf(lx) > 0) { region.push(lx); i++; } else break;
        }
        out.push(renderListRegion(region));
        continue;
      }

      // paragraph (gather consecutive plain lines)
      var para = [line];
      i++;
      while (i < lines.length) {
        var pl = lines[i];
        if (pl.trim() === '') break;
        if (/^(#{1,6})\s/.test(pl) || /^\s*>\s?/.test(pl) || ITEM_RE.exec(pl) ||
            /^\s*```/.test(pl) || /^\s*([-*_])\s*(\1\s*){2,}$/.test(pl)) break;
        para.push(pl);
        i++;
      }
      out.push('<p class="vp-p">' + inline(esc(para.join('\n'))).replace(/\n/g, '<br>') + '</p>');
    }
    return '<div class="vp-doc">' + out.join('\n') + '</div>';
  }
  V.render = render;

  /* ── Mermaid lifecycle ── */
  var mermaidReady = false;
  function currentTheme() {
    var dt = document.documentElement.getAttribute('data-theme') || '';
    if (dt === 'dark' || dt === 'slate' || document.body.classList.contains('dark') ||
        document.documentElement.classList.contains('dark')) return 'dark';
    return 'neutral';
  }
  function whenMermaid() {
    return new Promise(function (resolve) {
      var tries = 0;
      (function check() {
        if (window.mermaid) {
          if (!mermaidReady) {
            window.mermaid.initialize({
              startOnLoad: false,
              securityLevel: 'strict',
              theme: currentTheme(),
              flowchart: { htmlLabels: true, useMaxWidth: true },
              sequence: { useMaxWidth: true }
            });
            mermaidReady = true;
          }
          resolve(window.mermaid);
        } else if (tries++ < 200) {
          setTimeout(check, 50);
        } else {
          resolve(null);
        }
      })();
    });
  }
  function runMermaid(container) {
    var root = container || document;
    var nodes = root.querySelectorAll('.vp-mermaid');
    if (!nodes.length) return Promise.resolve();
    return whenMermaid().then(function (mmd) {
      if (!mmd) {
        // mermaid library unavailable: degrade to visible code blocks
        nodes.forEach(function (n) {
          n.outerHTML = '<pre class="vp-pre"><code>' + n.textContent + '</code></pre>';
        });
        return;
      }
      var tasks = [];
      nodes.forEach(function (n, idx) {
        var id = 'vp-mm-' + Date.now() + '-' + idx;
        tasks.push(
          mmd.render(id, n.textContent).then(function (result) {
            n.innerHTML = result.svg;
            n.classList.remove('vp-mermaid');
            if (typeof result.bindFunctions === 'function') result.bindFunctions(n);
          }).catch(function () {
            n.outerHTML = '<pre class="vp-pre"><code>' + esc(n.textContent) + '</code></pre>';
          })
        );
      });
      return Promise.all(tasks);
    });
  }
  V.runMermaid = runMermaid;

  /* ── One-time stylesheet (uses platform CSS vars with fallbacks) ── */
  function injectStyles() {
    if (document.getElementById('vp-styles')) return;
    var css =
      '.vp-doc{line-height:1.7;font-size:0.92rem;word-wrap:break-word}' +
      '.vp-h{margin:1.2em 0 .5em;line-height:1.3;font-weight:600}' +
      '.vp-h1{font-size:1.4em}.vp-h2{font-size:1.25em}.vp-h3{font-size:1.1em}' +
      '.vp-h4{font-size:1.02em}.vp-h5,.vp-h6{font-size:.95em}' +
      '.vp-p{margin:.55em 0}' +
      '.vp-table-wrap{overflow-x:auto;margin:.8em 0}' +
      '.vp-table{border-collapse:collapse;width:100%;font-size:.88rem}' +
      '.vp-table th,.vp-table td{border:1px solid var(--bd,#d0d7de);padding:6px 10px;text-align:left}' +
      '.vp-table th{background:var(--bg-secondary,rgba(127,127,127,.10));font-weight:600}' +
      '.vp-pre{position:relative;background:var(--bg-secondary,rgba(127,127,127,.10));padding:12px;border-radius:6px;overflow-x:auto;margin:.8em 0;font-size:.82rem;line-height:1.5}' +
      '.vp-pre code{font-family:ui-monospace,Consolas,Menlo,monospace}' +
      '.vp-pre-lang{position:absolute;top:4px;right:8px;font-size:.7rem;opacity:.6;text-transform:uppercase}' +
      '.vp-code{background:var(--bg-secondary,rgba(127,127,127,.10));padding:.1em .35em;border-radius:3px;font-size:.88em;font-family:ui-monospace,Consolas,Menlo,monospace}' +
      '.vp-quote{border-left:3px solid var(--accent,#58a6ff);margin:.8em 0;padding:.2em 12px;color:var(--dim,#656d76)}' +
      '.vp-list{margin:.5em 0;padding-left:1.6em}.vp-list li{margin:.25em 0}.vp-list .vp-list{margin:.25em 0}' +
      '.vp-li-cont{margin:.2em 0}' +
      '.vp-img{max-width:100%;border-radius:6px;margin:.5em 0}' +
      '.vp-link{color:var(--accent,#58a6ff);text-decoration:none}.vp-link:hover{text-decoration:underline}' +
      '.vp-hr{border:none;border-top:1px solid var(--bd,#d0d7de);margin:1em 0}' +
      '.vp-mermaid{margin:.9em 0;overflow-x:auto;text-align:center}' +
      '.vp-mermaid svg{max-width:100%;height:auto}';
    var style = document.createElement('style');
    style.id = 'vp-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectStyles);
  } else {
    injectStyles();
  }

  window.VeroMarkdown = V;
})();
