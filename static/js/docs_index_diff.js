/*!
 * docs_index_diff.js
 * 最新の生成結果カードに「差分の表示」ボタンからGitHub風の差分を表示する機能。
 * - API: GET /docs/<project_id>/diff/latest → { files: [{ path, status, patch }] }
 * - レンダリング: diff2html を使用（unified diff → HTML）
 */
(function(){
  'use strict';

  // diff2html の表示モード（"line-by-line" or "side-by-side"）
  let viewMode = 'line-by-line';

  function getProjectId(){
    const root = document.getElementById('docs-root');
    if(!root) return null;
    // docs/index.html のフォームに project_id は直接埋め込んでいないため、URLから拾う
    const m = location.pathname.match(/\/(\d+)(?:$|\/?)/);
    return m ? m[1] : null;
  }

  async function fetchLatestDiff(){
    const pid = getProjectId();
    if(!pid) throw new Error('project_id not found in URL');
    const res = await fetch(`/docs/${pid}/diff/latest`, {credentials: 'same-origin'});
    if(!res.ok) throw new Error('failed to fetch latest diff');
    return await res.json();
  }

  function ensureModal(){
    let modal = document.getElementById('diffModal');
    if(modal) return modal;

    // モーダルのDOMを動的生成（Bootstrap用）
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
<div class="modal fade" id="diffModal" tabindex="-1" aria-labelledby="diffModalLabel" aria-hidden="true">
  <div class="modal-dialog modal-xl modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="diffModalLabel">差分の表示</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="閉じる"></button>
      </div>
      <div class="modal-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <div>
            <button class="btn btn-sm btn-outline-secondary" id="diff-view-inline">Inline</button>
            <button class="btn btn-sm btn-outline-secondary" id="diff-view-sbs">Side-by-Side</button>
          </div>
          <div class="text-muted small" id="diff-summary"></div>
        </div>
        <div id="diff-filelist" class="list-group mb-3"></div>
        <div id="diff-container"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" data-bs-dismiss="modal">閉じる</button>
      </div>
    </div>
  </div>
</div>`;
    document.body.appendChild(wrapper.firstElementChild);
    return document.getElementById('diffModal');
  }

  function renderDiffHtml(patch){
    // diff2html のグローバル（CDN読み込みが必要）
    if(typeof Diff2Html === 'undefined'){
      return '<div class="text-danger">diff2html が読み込まれていません。</div>';
    }
    try{
      // unified diff テキストを直接 HTML に変換（inputFormat: 'diff'）
      return Diff2Html.html(patch, {
        inputFormat: 'diff',
        drawFileList: false,
        matching: 'lines',
        outputFormat: viewMode
      });
    }catch(e){
      console.error(e);
      return '<div class="text-danger">差分の描画に失敗しました。</div>';
    }
  }

  function openModalWithDiff(payload){
    const modalEl = ensureModal();

    const files = payload.files || [];
    const fileList = document.getElementById('diff-filelist');
    const container = document.getElementById('diff-container');
    const summary = document.getElementById('diff-summary');

    fileList.innerHTML = '';
    container.innerHTML = '';

    summary.textContent = files.length ? `${files.length}個の変更ファイル` : '差分はありません。';

    // ファイル一覧を作る
    files.forEach((f, idx) => {
      const a = document.createElement('a');
      a.href = '#';
      a.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
      a.dataset.index = String(idx);
      a.innerHTML = `<span>${f.path}</span><span class="badge bg-secondary">${f.status}</span>`;
      if(idx === 0) a.classList.add('active');
      fileList.appendChild(a);
    });

    function renderIndex(i){
      const f = files[i];
      if(!f){ container.innerHTML = '<div class="text-muted">表示する差分がありません。</div>'; return; }
      const html = renderDiffHtml(f.patch);
      container.innerHTML = html;
    }

    // 先頭を表示
    renderIndex(0);

    // クリックで切替
    fileList.addEventListener('click', function(e){
      const item = e.target.closest('a.list-group-item');
      if(!item) return;
      e.preventDefault();
      // active の付け替え
      [...fileList.querySelectorAll('a.list-group-item')].forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      const i = Number(item.dataset.index || '0');
      renderIndex(i);
    });

    // 表示モード切替
    document.getElementById('diff-view-inline').onclick = function(){
      viewMode = 'line-by-line';
      const i = Number(fileList.querySelector('a.list-group-item.active')?.dataset.index || 0);
      renderIndex(i);
    };
    document.getElementById('diff-view-sbs').onclick = function(){
      viewMode = 'side-by-side';
      const i = Number(fileList.querySelector('a.list-group-item.active')?.dataset.index || 0);
      renderIndex(i);
    };

    // Bootstrap モーダルを開く
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
  }

  // 「差分の表示」ボタンクリックハンドラを委譲で1箇所に
  document.addEventListener('click', async function(e){
    const btn = e.target.closest('[data-action="show-diff"]');
    if(!btn) return;
    e.preventDefault();
    try{
      const payload = await fetchLatestDiff();
      openModalWithDiff(payload);
    }catch(err){
      console.error(err);
      alert('差分の取得に失敗しました。');
    }
  });
})();
