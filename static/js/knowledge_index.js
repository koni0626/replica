/*!
 * knowledge_index.js
 * ナレッジ一覧ページ: アコーディオン表示 + Markdownレンダリング。
 * - 閉じている時はタイトルのみ
 * - 開くと content を Markdown として安全に表示
 * - タイトル右に「編集」「削除」ボタン（テンプレート側実装）
 *
 * 依存: marked, DOMPurify, Bootstrap( Collapse )
 */
(function(){
  'use strict';

  // marked の基本設定（GFM + 改行を <br>）
  if (window.marked && typeof window.marked.setOptions === 'function') {
    window.marked.setOptions({ gfm: true, breaks: true });
  }

  /**
   * テキストを Markdown → サニタイズ済み HTML に変換して target へ流し込む
   */
  function renderMarkdownTo(targetEl, srcText){
    const md = (srcText || '');
    // 危険なタグ開始を事前エスケープ（生HTML解釈を抑止）
    const safeMd = md.replace(/<(?=\/?(script|style|iframe)\b)/gi, '&lt;');
    const dirty = window.marked ? window.marked.parse(safeMd) : safeMd;
    if (window.DOMPurify) {
      const sanitizeOptions = {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ['form','input','button','select','option','textarea','iframe','script','style'],
        FORBID_ATTR: ['onerror','onload','onclick','style']
      };
      targetEl.innerHTML = window.DOMPurify.sanitize(dirty, sanitizeOptions);
    } else {
      targetEl.innerHTML = dirty;
    }
  }

  // アコーディオンの開閉時に Markdown を初回レンダリング（負荷削減のため初回のみ）
  document.addEventListener('shown.bs.collapse', function(e){
    const panel = e.target; // .accordion-collapse
    const body  = panel.querySelector('.accordion-body');
    if (!body) return;

    const mdView = body.querySelector('.markdown-body');
    const mdSrc  = body.querySelector('textarea[id^="mdsrc-"]');
    if (!mdView || !mdSrc) return;

    // 既に描画済みならスキップ
    if (mdView.getAttribute('data-rendered') === '1') return;

    renderMarkdownTo(mdView, mdSrc.value);
    mdView.setAttribute('data-rendered','1');
  });

  // 削除フォームの confirm（data-confirm 属性）
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('form[data-confirm]');
    if(!form) return;
    const msg = form.getAttribute('data-confirm') || '削除しますか？';
    if(!confirm(msg)){
      e.preventDefault();
      e.stopPropagation();
    }
  });
})();
