/*!
 * docs_index_diff.js
 * 差分表示モーダル（git diff ベース）。
 * - 変更（modified/added/deleted/renamed）と 未追跡（untracked）をタブで分けて表示
 * - 表示は diff2html を利用
 */
(function(){
  'use strict';

  // diff2html の表示モード（"line-by-line" or "side-by-side"）
  let viewMode = 'line-by-line';

  function getProjectId(){
    const m = location.pathname.match(/\/(\d+)(?:$|\/?)/);
    return m ? m[1] : null;
  }

  async function fetchDiff(staged){
    const pid = getProjectId();
    if(!pid) throw new Error('project_id not found in URL');
    const url = `/docs/${pid}/diff/latest${staged ? '?staged=1' : ''}`;
    const res = await fetch(url, { credentials: 'same-origin' });
    let data = null;
    try { data = await res.json(); } catch(_){ /* ignore */ }
    if(!res.ok || !data || data.ok === false){
      const msg = (data && data.message) ? data.message : '差分の取得に失敗しました。';
      const err = new Error(msg);
      err.payload = data;
      throw err;
    }
    return data; // { ok: true, files: [...] }
  }

  function ensureModal(){
    let modal = document.getElementById('diffModal');
    if(modal) return modal;

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

        <ul class="nav nav-tabs" id="diffTabs" role="tablist">
          <li class="nav-item" role="presentation">
            <button class="nav-link active" id="tab-modified" data-bs-toggle="tab" data-bs-target="#pane-modified" type="button" role="tab" aria-controls="pane-modified" aria-selected="true">変更（modified）</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="tab-untracked" data-bs-toggle="tab" data-bs-target="#pane-untracked" type="button" role="tab" aria-controls="pane-untracked" aria-selected="false">未追跡（untracked）</button>
          </li>
        </ul>
        <div class="tab-content pt-3">
          <div class="tab-pane fade show active" id="pane-modified" role="tabpanel" aria-labelledby="tab-modified">
            <div id="diff-filelist-modified" class="list-group mb-3"></div>
            <div id="diff-container-modified"></div>
          </div>
          <div class="tab-pane fade" id="pane-untracked" role="tabpanel" aria-labelledby="tab-untracked">
            <div id="diff-filelist-untracked" class="list-group mb-3"></div>
            <div id="diff-container-untracked"></div>
          </div>
        </div>
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
    if(typeof Diff2Html === 'undefined'){
      return '<div class="text-danger">diff2html が読み込まれていません。</div>';
    }
    try{
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

  function renderDataset(listEl, containerEl, files){
    listEl.innerHTML = '';
    containerEl.innerHTML = '';

    if(!files.length){
      containerEl.innerHTML = '<div class="text-muted">差分はありません。</div>';
      return;
    }

    files.forEach((f, idx) => {
      const a = document.createElement('a');
      a.href = '#';
      a.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
      a.dataset.index = String(idx);
      const badgeColor = (f.status === 'added') ? 'success' : (f.status === 'deleted') ? 'danger' : (f.status === 'untracked') ? 'warning' : 'secondary';
      a.innerHTML = `<span>${f.path}</span><span class="badge bg-${badgeColor}">${f.status}</span>`;
      if(idx === 0) a.classList.add('active');
      listEl.appendChild(a);
    });

    function renderIndex(i){
      const f = files[i];
      if(!f){ containerEl.innerHTML = '<div class="text-muted">表示する差分がありません。</div>'; return; }
      containerEl.innerHTML = renderDiffHtml(f.patch);
    }

    renderIndex(0);

    listEl.addEventListener('click', function onClick(e){
      const item = e.target.closest('a.list-group-item');
      if(!item) return;
      e.preventDefault();
      [...listEl.querySelectorAll('a.list-group-item')].forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      const i = Number(item.dataset.index || '0');
      renderIndex(i);
    });

    // 再描画用に関数を返す（表示モード変更時に利用）
    return function reRenderActive(){
      const i = Number(listEl.querySelector('a.list-group-item.active')?.dataset.index || 0);
      if(files.length){ containerEl.innerHTML = renderDiffHtml(files[i].patch); }
    };
  }

  function openModalWithAll(allPayload){
    const modalEl = ensureModal();

    const allFiles = (allPayload && allPayload.files) ? allPayload.files : [];
    // 分割: modified（＝untracked 以外）と untracked
    const filesModified = allFiles.filter(f => f.status !== 'untracked');
    const filesUntracked = allFiles.filter(f => f.status === 'untracked');

    const listModified = document.getElementById('diff-filelist-modified');
    const contModified = document.getElementById('diff-container-modified');
    const listUntracked = document.getElementById('diff-filelist-untracked');
    const contUntracked = document.getElementById('diff-container-untracked');
    const summary = document.getElementById('diff-summary');

    const modReRender = renderDataset(listModified, contModified, filesModified) || function(){};
    const untrackReRender = renderDataset(listUntracked, contUntracked, filesUntracked) || function(){};

    summary.textContent = `変更 ${filesModified.length} 件 / 未追跡 ${filesUntracked.length} 件`;

    // 表示モード切替時、アクティブなタブを再描画
    document.getElementById('diff-view-inline').onclick = function(){
      viewMode = 'line-by-line';
      const activePane = document.querySelector('#diffTabs .nav-link.active')?.id;
      if(activePane === 'tab-modified') modReRender(); else untrackReRender();
    };
    document.getElementById('diff-view-sbs').onclick = function(){
      viewMode = 'side-by-side';
      const activePane = document.querySelector('#diffTabs .nav-link.active')?.id;
      if(activePane === 'tab-modified') modReRender(); else untrackReRender();
    };

    // タブ切替時も現在のモードで再描画
    const tabElList = document.querySelectorAll('#diffTabs button[data-bs-toggle="tab"]');
    tabElList.forEach(btn => {
      btn.addEventListener('shown.bs.tab', function(e){
        if(e.target.id === 'tab-modified') modReRender(); else untrackReRender();
      });
    });

    const modal = new bootstrap.Modal(modalEl);
    // 変更が 0 件で未追跡がある場合は未追跡側を初期表示に
    if(!filesModified.length && filesUntracked.length){
      const tabUntracked = document.getElementById('tab-untracked');
      const bsTab = new bootstrap.Tab(tabUntracked);
      bsTab.show();
    }
    modal.show();
  }

  // クリックでモーダルを開き、作業ツリーの差分を取得（未ステージの tracked 変更＋未追跡）
  document.addEventListener('click', async function(e){
    const btn = e.target.closest('[data-action="show-diff"]');
    if(!btn) return;
    e.preventDefault();
    try{
      const all = await fetchDiff(false); // staged=0: 作業ツリーの差分
      openModalWithAll(all);
    }catch(err){
      console.error(err);
      alert(err && err.message ? err.message : '差分の取得に失敗しました。');
    }
  });
})();
