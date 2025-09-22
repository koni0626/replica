// 検索パス ツリー（Lazy Load + ファイル表示 + フィルタ）
(function(){
  // ルート要素の data-* からブート情報を取得
  const bootRoot = document.getElementById('search-paths-root');
  const projectId = bootRoot?.dataset.projectId;
  const API = {
    tree: bootRoot?.dataset.endpointTree,
    stateGet: bootRoot?.dataset.endpointStateGet,
    statePost: bootRoot?.dataset.endpointStatePost,
  };

  const $root = document.getElementById('tree-root');
  const $loading = document.getElementById('tree-loading');
  const $selCount = document.getElementById('sel-count');
  const $excCount = document.getElementById('exc-count');
  const $filter = document.getElementById('filter-input');
  const $filterHint = document.getElementById('filter-hint');
  const $saveToast = document.getElementById('save-toast');

  // CSRF トークン（Flask-WTF）
  const CSRF = document.querySelector('meta[name="csrf-token"]').getAttribute('content') || '';

  // 状態（includes はファイル/ディレクトリのミックスを暫定保持。保存時にサーバでファイルへ正規化）
  let savedState = { includes: [], excludes: [] };
  const cache = new Map(); // rel -> nodes[]（ロード済み）
  const inFlight = new Map(); // rel -> Promise（ロード中ガード）

  function setLoading(v){ if($loading) $loading.style.display = v ? '' : 'none'; }
  function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  function relToId(rel){ return rel.replace(/[^a-zA-Z0-9_\-]/g, '_'); }
  function normRel(rel){ return (rel||'').replace(/\\/g,'/').replace(/^\/+|\/+$/g,''); }

  function isCheckedTarget(key){
    // key はファイルパス or ディレクトリパスの posix 相対
    const rel = normRel(key);
    if(!rel) return false;
    // excludes に含まれていれば false（優先）
    if(savedState.excludes.some(x => x === rel || x.startsWith(rel + '/'))) return false;
    // includes に含まれていれば true（ディレクトリ指定は祖先一致で true）
    if(savedState.includes.some(x => x === rel || x.startsWith(rel + '/'))) return true;
    return false;
  }

  function updateCounters(){
    // 厳密な「ファイル数」ではなく、選択項目数の目安として現行カウントを維持
    if($selCount) $selCount.textContent = String(savedState.includes.length);
    if($excCount) $excCount.textContent = String(savedState.excludes.length);
  }

  async function fetchJSON(url){
    const res = await fetch(url, { credentials: 'same-origin' });
    if(!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  function dirNodeTemplate(node){
    const rel = node.rel;
    const id = 'node_' + relToId(rel);
    const checked = isCheckedTarget(rel) ? 'checked' : '';
    const hasChildren = !!node.has_children;
    const caret = hasChildren
      ? '<button class="btn btn-sm btn-outline-secondary me-1 btn-toggle" data-state="closed" aria-label="toggle" aria-expanded="false">▸</button>'
      : '<span class="me-1 text-muted" aria-hidden="true">•</span>';
    return `
      <li data-rel="${esc(rel)}" id="${id}" role="treeitem" aria-expanded="${hasChildren ? 'false' : 'true'}">
        <div class="tree-row d-flex align-items-center">
          ${caret}
          <span class="icon-folder" aria-hidden="true"></span>
          <input class="form-check-input me-2 chk" type="checkbox" ${checked} />
          <span class="label" title="${esc(rel)}">${esc(node.name)}</span>
          <span class="spinner-border spinner-border-sm text-primary ms-2 d-none" role="status" aria-hidden="true"></span>
          <span class="badge-count ms-2 d-none"></span>
        </div>
        <ul class="children list-unstyled ms-4" role="group"></ul>
      </li>
    `;
  }

  function fileNodeTemplate(node){
    const path = node.path; // base 相対 posix
    const id = 'node_' + relToId(path);
    const checked = isCheckedTarget(path) ? 'checked' : (node.selected ? 'checked' : '');
    // ファイルはトグルボタンなし
    return `
      <li data-path="${esc(path)}" id="${id}" role="treeitem" aria-expanded="true">
        <div class="tree-row d-flex align-items-center">
          <span class="me-1 text-muted" aria-hidden="true">·</span>
          <span class="icon-file" aria-hidden="true"></span>
          <input class="form-check-input me-2 chk" type="checkbox" ${checked} />
          <span class="label" title="${esc(path)}">${esc(node.name)}</span>
        </div>
      </li>
    `;
  }

  function nodeTemplate(node){
    if(node.type === 'file') return fileNodeTemplate(node);
    return dirNodeTemplate(node);
  }

  function renderNodes($ul, nodes){
    if(!$ul) return;
    // 既に子が存在する場合は重複追加を避ける
    if($ul.children && $ul.children.length > 0) return;
    const html = nodes.map(nodeTemplate).join('');
    $ul.insertAdjacentHTML('beforeend', html);
  }

  async function loadChildren(li){
    const rel = li.dataset.rel || '';
    const ul = li.querySelector(':scope > ul.children');

    // すでにロード済みなら何もしない
    if(cache.has(rel)) return cache.get(rel);

    // ロード中があればそれを待つ（多重リクエスト防止）
    if(inFlight.has(rel)){
      try{ await inFlight.get(rel); } catch(_) {}
      return cache.get(rel) || [];
    }

    const btn = li.querySelector('.btn-toggle');
    const spin = li.querySelector('.spinner-border');

    if(btn) btn.setAttribute('disabled','disabled');
    if(spin) spin.classList.remove('d-none');

    // この rel のロードを登録
    const p = (async () => {
      try{
        const url = API.tree + (rel ? ('?rel=' + encodeURIComponent(rel)) : '');
        const data = await fetchJSON(url); // [ {type: 'dir'|'file', ...}, ... ]
        cache.set(rel, data);
        renderNodes(ul, data);
        wireNodeEvents(ul);
        // 親がチェック済みなら、描画済みの子チェックを合わせる（ファイルにも伝播）
        if(isCheckedTarget(rel)){
          ul.querySelectorAll('input.chk').forEach(ch => ch.checked = true);
        }
        updateCounters();
        return data;
      } finally {
        if(btn){
          btn.removeAttribute('disabled');
          btn.dataset.state = 'open';
          btn.textContent = '▾';
          btn.classList.add('is-open');
          li.setAttribute('aria-expanded', 'true');
        }
        if(spin){ spin.classList.add('d-none'); }
      }
    })();

    inFlight.set(rel, p);
    try{
      const data = await p;
      return data;
    } finally {
      inFlight.delete(rel);
    }
  }

  function updateRowState(li){
    const row = li?.querySelector(':scope > .tree-row');
    if(!row) return;
    li.classList.remove('state-include','state-exclude','state-mixed');
    const key = li.dataset.path || li.dataset.rel || '';
    const on = isCheckedTarget(key);
    if(li.dataset.path){
      // ファイルは include/exclude の2状態のみ
      li.classList.add(on ? 'state-include' : 'state-exclude');
    } else {
      // ディレクトリはロード済みの子の状態を見て tri-state
      if(on){
        li.classList.add('state-include');
      } else {
        const anyChildOn = !!li.querySelector(':scope ul.children input.chk:checked');
        li.classList.add(anyChildOn ? 'state-mixed' : 'state-exclude');
      }
    }
  }

  function updateAllRowStates(){
    document.querySelectorAll('#tree-root li[role="treeitem"]').forEach(li => updateRowState(li));
  }

  function wireNodeEvents(rootEl){
    if(!rootEl) return;

    rootEl.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-toggle');
      if(btn){
        const li = e.target.closest('li');
        const state = btn.dataset.state || 'closed';
        const ul = li.querySelector(':scope > ul.children');
        if(state === 'closed'){
          if(li && ul.children.length === 0){
            await loadChildren(li);
          }
          btn.dataset.state = 'open';
          btn.textContent = '▾';
          btn.classList.add('is-open');
          ul.classList.remove('d-none');
          li.setAttribute('aria-expanded', 'true');
        } else {
          btn.dataset.state = 'closed';
          btn.textContent = '▸';
          btn.classList.remove('is-open');
          if(ul) ul.classList.add('d-none');
          li.setAttribute('aria-expanded', 'false');
        }
      }
    });

    rootEl.addEventListener('change', (e) => {
      const chk = e.target.closest('input.chk');
      if(!chk) return;
      const li = e.target.closest('li');
      const key = li.dataset.path || li.dataset.rel || '';
      const on = chk.checked;

      // 状態集合を更新
      if(on){
        savedState.excludes = savedState.excludes.filter(x => !(x === key || x.startsWith(key + '/')));
        if(!savedState.includes.includes(key)) savedState.includes.push(key);
      } else {
        savedState.includes = savedState.includes.filter(x => !(x === key || x.startsWith(key + '/')));
        if(!savedState.excludes.includes(key)) savedState.excludes.push(key);
      }

      // 子に伝搬（ロード済みの範囲のみ）。ファイルにも適用。
      const ul = li.querySelector(':scope > ul.children');
      if(ul){
        ul.querySelectorAll('li[role="treeitem"] input.chk').forEach(ch2 => {
          ch2.checked = on;
          const li2 = ch2.closest('li');
          const key2 = li2.dataset.path || li2.dataset.rel || '';
          if(on){
            savedState.excludes = savedState.excludes.filter(x => !(x === key2 || x.startsWith(key2 + '/')));
            if(!savedState.includes.includes(key2)) savedState.includes.push(key2);
          } else {
            savedState.includes = savedState.includes.filter(x => !(x === key2 || x.startsWith(key2 + '/')));
            if(!savedState.excludes.includes(key2)) savedState.excludes.push(key2);
          }
        });
      }

      updateCounters();
      updateRowState(li);
    });
  }

  function filterTree(q){
    q = (q || '').trim().toLowerCase();
    if(!$root) return;
    if($filterHint) $filterHint.style.display = q ? '' : 'none';
    const items = Array.from($root.querySelectorAll('li[role="treeitem"]'));
    if(!q){ items.forEach(li => li.classList.remove('d-none')); return; }
    items.forEach(li => li.classList.add('d-none'));

    const matchMap = new Map();
    function matches(li){
      if(matchMap.has(li)) return matchMap.get(li);
      const label = li.querySelector(':scope > .tree-row .label');
      const selfMatch = label && (label.textContent || '').toLowerCase().includes(q);
      let childMatch = false;
      const children = li.querySelectorAll(':scope > ul.children > li');
      children.forEach(c => { if(matches(c)) childMatch = true; });
      const any = selfMatch || childMatch;
      matchMap.set(li, any);
      if(any){
        li.classList.remove('d-none');
        let p = li.parentElement;
        while(p && p !== $root){
          if(p.matches('ul.children')){
            const pli = p.parentElement;
            pli.classList.remove('d-none');
            const btn = pli.querySelector(':scope > .tree-row .btn-toggle');
            const ul = pli.querySelector(':scope > ul.children');
            if(btn && ul){
              btn.dataset.state = 'open';
              btn.textContent = '▾';
              btn.classList.add('is-open');
              ul.classList.remove('d-none');
              pli.setAttribute('aria-expanded', 'true');
            }
          }
          p = p.parentElement;
        }
      }
      return any;
    }
    const roots = $root.querySelectorAll(':scope > ul > li');
    roots.forEach(li => matches(li));
  }

  function showSavedToast(){
    if(!$saveToast) return;
    try{
      if(window.bootstrap && window.bootstrap.Toast){
        const inst = window.bootstrap.Toast.getOrCreateInstance($saveToast, { delay: 2000 });
        inst.show();
      } else {
        $saveToast.classList.add('show');
        setTimeout(() => { $saveToast.classList.remove('show'); }, 2000);
      }
    } catch(e){ console.log('保存しました'); }
  }

  async function init(){
    setLoading(true);
    try{
      savedState = await fetchJSON(API.stateGet);
      updateCounters();
      // ルート直下のみ取得
      const data = await fetchJSON(API.tree);
      cache.set('', data);
      const ul = document.createElement('ul');
      ul.className = 'list-unstyled';
      $root.appendChild(ul);
      renderNodes(ul, data);
      wireNodeEvents(ul);
      updateAllRowStates();
    } catch(err){
      console.error(err);
      $root.innerHTML = '<div class="text-danger">ツリーの読み込みに失敗しました。</div>';
    } finally {
      setLoading(false);
    }
  }

  // 操作用ボタン
  document.getElementById('btn-save')?.addEventListener('click', async () => {
    try{
      const res = await fetch(API.statePost, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        credentials: 'same-origin',
        body: JSON.stringify({ includes: savedState.includes, excludes: savedState.excludes })
      });
      if(!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      savedState = data; // サーバ正規化（v2: ファイルのみ）
      updateCounters();
      updateAllRowStates();
      showSavedToast();
    } catch(err){ console.error(err); alert('保存に失敗しました'); }
  });

  document.getElementById('btn-select-all')?.addEventListener('click', () => {
    // ルート直下を一括ON（未ロード分はサーバの正規化に委ねるため、ディレクトリも includes に追加）
    const roots = cache.get('') || [];
    roots.forEach(n => {
      const key = n.type === 'file' ? (n.path) : n.rel;
      if(!savedState.includes.includes(key)) savedState.includes.push(key);
      savedState.excludes = savedState.excludes.filter(x => !(x === key || x.startsWith(key + '/')));
      const sel = n.type === 'file' ? `li[data-path="${CSS.escape(n.path)}"]` : `li[data-rel="${CSS.escape(n.rel)}"]`;
      const li = document.querySelector(sel);
      const chk = li?.querySelector('input.chk');
      if(chk){ chk.checked = true; }
    });
    updateCounters();
    updateAllRowStates();
  });

  document.getElementById('btn-unselect-all')?.addEventListener('click', () => {
    const roots = cache.get('') || [];
    roots.forEach(n => {
      const key = n.type === 'file' ? (n.path) : n.rel;
      if(!savedState.excludes.includes(key)) savedState.excludes.push(key);
      savedState.includes = savedState.includes.filter(x => !(x === key || x.startsWith(key + '/')));
      const sel = n.type === 'file' ? `li[data-path="${CSS.escape(n.path)}"]` : `li[data-rel="${CSS.escape(n.rel)}"]`;
      const li = document.querySelector(sel);
      const chk = li?.querySelector('input.chk');
      if(chk){ chk.checked = false; }
    });
    updateCounters();
    updateAllRowStates();
  });

  document.getElementById('btn-expand')?.addEventListener('click', async () => {
    document.querySelectorAll('.btn-toggle').forEach(b => { b.dataset.state='open'; b.textContent='▾'; b.classList.add('is-open'); });
    document.querySelectorAll('ul.children').forEach(ul => ul.classList.remove('d-none'));
    document.querySelectorAll('#tree-root li[role="treeitem"]').forEach(li => li.setAttribute('aria-expanded','true'));
  });
  document.getElementById('btn-collapse')?.addEventListener('click', () => {
    document.querySelectorAll('.btn-toggle').forEach(b => { b.dataset.state='closed'; b.textContent='▸'; b.classList.remove('is-open'); });
    document.querySelectorAll('ul.children').forEach(ul => ul.classList.add('d-none'));
    document.querySelectorAll('#tree-root li[role="treeitem"]').forEach(li => li.setAttribute('aria-expanded','false'));
  });

  // フィルタ
  $filter?.addEventListener('input', (e) => { filterTree(e.target.value); });

  // 起動
  window.addEventListener('DOMContentLoaded', init);
})();
