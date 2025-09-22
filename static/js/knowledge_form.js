(function(){
  'use strict';
  // marked の基本設定
  if (window.marked && typeof window.marked.setOptions === 'function') {
    window.marked.setOptions({ gfm: true, breaks: true });
  }
  function renderMarkdownTo(targetEl, srcText){
    const md = (srcText || '');
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
  document.addEventListener('DOMContentLoaded', () => {
    const contentInput = document.getElementById('content-input');
    const contentPreview = document.getElementById('content-preview');
    if (contentInput && contentPreview) {
      const updatePreview = () => renderMarkdownTo(contentPreview, contentInput.value);
      contentInput.addEventListener('input', updatePreview);
      updatePreview();
    }
  });
})();
