// 検索パス ツリー（Lazy Load + UI 改善 + フィルタ）
(function(){
  // ルート要素の data-* からブート情報を取得（インラインJS撤廃）
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

  // 状態
  let savedState = { includes: [], excludes: [] };
  const cache = new Map(); // rel -> nodes[]

  function setLoading(v){
    if(!$loading) return;
    $loading.style.display = v ? '' : 'none';
  }

  function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }

  function relToId(rel){ return rel.replace(/[^a-zA-Z0-9_\-]/g, '_'); }

  function normRel(rel){ return (rel||'').replace(/\\/g,'/').replace(/^\/+|\/+$/g,''); }

  function isChecked(rel){
    rel = normRel(rel);
    if(!rel) return false;
    // excludes に含まれていれば false 優先
    if(savedState.excludes.some(x => x === rel || x.startsWith(rel + '/'))) return false;
    // includes に含まれていれば true
    if(savedState.includes.some(x => x === rel || x.startsWith(rel + '/'))) return true;
    return false;
  }

  function updateCounters(){
    if($selCount) $selCount.textContent = String(savedState.includes.length);
    if($excCount) $excCount.textContent = String(savedState.excludes.length);
  }

  async function fetchJSON(url){
    const res = await fetch(url, { credentials: 'same-origin' });
    if(!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  function nodeTemplate(node){
    const rel = node.rel;
    const id = 'node_' + relToId(rel);
    const checked = isChecked(rel) ? 'checked' : '';
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

  function renderNodes($ul, nodes){
    const html = nodes.map(nodeTemplate).join('');
    $ul.insertAdjacentHTML('beforeend', html);
  }

  async function loadChildren(li){
    const rel = li.dataset.rel || '';
    if(cache.has(rel)) return cache.get(rel);
    const btn = li.querySelector('.btn-toggle');
    const spin = li.querySelector('.spinner-border');
    if(btn) btn.setAttribute('disabled','disabled');
    if(spin) spin.classList.remove('d-none');
    try{
      const url = API.tree + (rel ? ('?rel=' + encodeURIComponent(rel)) : '');
      const data = await fetchJSON(url); // [ {name,rel,has_children}, ... ]
      cache.set(rel, data);
      const ul = li.querySelector(':scope > ul.children');
      renderNodes(ul, data);
      wireNodeEvents(ul);
      // 子へ状態反映（親がチェック済みなら子もチェック）
      if(isChecked(rel)){
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
  }

  function wireNodeEvents(rootEl){
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
          // 折りたたみ時は子ULを非表示（DOMは残す）
          if(ul) ul.classList.add('d-none');
          li.setAttribute('aria-expanded', 'false');
        }
      }
    });

    rootEl.addEventListener('change', (e) => {
      const chk = e.target.closest('input.chk');
      if(!chk) return;
      const li = e.target.closest('li');
      const rel = li.dataset.rel || '';
      const on = chk.checked;
      // 状態集合を更新（重複を避ける）
      if(on){
        savedState.excludes = savedState.excludes.filter(x => !(x === rel || x.startsWith(rel + '/')));
        if(!savedState.includes.includes(rel)) savedState.includes.push(rel);
      } else {
        savedState.includes = savedState.includes.filter(x => !(x === rel || x.startsWith(rel + '/')));
        if(!savedState.excludes.includes(rel)) savedState.excludes.push(rel);
      }
      // 子に伝搬（ロード済みの子のみ）
      const ul = li.querySelector(':scope > ul.children');
      if(ul){ ul.querySelectorAll('input.chk').forEach(ch => ch.checked = on); }
      updateCounters();
      // 行の見た目（state-クラス）を更新
      updateRowState(li);
    });
  }

  function updateRowState(li){
    // 親子のチェック状態から state-include/exclude/mixed を付与（ロード済みの範囲）
    const rel = li?.dataset?.rel || '';
    const row = li?.querySelector(':scope > .tree-row');
    if(!row) return;
    li.classList.remove('state-include','state-exclude','state-mixed');
    const on = isChecked(rel);
    if(on){
      li.classList.add('state-include');
    } else {
      // 子にチェックが残っている場合は mixed とする（簡易）
      const anyChildOn = !!li.querySelector(':scope ul.children input.chk:checked');
      li.classList.add(anyChildOn ? 'state-mixed' : 'state-exclude');
    }
  }

  function updateAllRowStates(){
    document.querySelectorAll('#tree-root li[data-rel]').forEach(li => updateRowState(li));
  }

  function filterTree(q){
    q = (q || '').trim().toLowerCase();
    if(!$root) return;
    if($filterHint) $filterHint.style.display = q ? '' : 'none';
    // すべてのLI
    const items = Array.from($root.querySelectorAll('li[data-rel]'));
    if(!q){
      items.forEach(li => li.classList.remove('d-none'));
      return;
    }
    // 後置きで決めるため一旦すべて非表示
    items.forEach(li => li.classList.add('d-none'));

    // 一致判定と祖先展開
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
        // 表示
        li.classList.remove('d-none');
        // 祖先を展開
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
    // ルート直下のみ起点にして再帰
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
        // Fallback: CSSクラスで簡易表示
        $saveToast.classList.add('show');
        setTimeout(() => { $saveToast.classList.remove('show'); }, 2000);
      }
    } catch(e){
      // 最終手段
      console.log('保存しました');
    }
  }

  async function init(){
    setLoading(true);
    try{
      // 状態を取得
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
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF,
        },
        credentials: 'same-origin',
        body: JSON.stringify({ includes: savedState.includes, excludes: savedState.excludes })
      });
      if(!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      savedState = data; // 返却された正規化後を反映
      updateCounters();
      updateAllRowStates();
      showSavedToast();
    } catch(err){
      console.error(err);
      alert('保存に失敗しました');
    }
  });

  document.getElementById('btn-select-all')?.addEventListener('click', () => {
    // ルート直下を一括ON（未ロード分はサーバ側の正規化に委ねる）
    const roots = cache.get('') || [];
    roots.forEach(n => {
      if(!savedState.includes.includes(n.rel)) savedState.includes.push(n.rel);
      savedState.excludes = savedState.excludes.filter(x => !(x === n.rel || x.startsWith(n.rel + '/')));
      const li = document.querySelector(`li[data-rel="${CSS.escape(n.rel)}"]`);
      const chk = li?.querySelector('input.chk');
      if(chk){ chk.checked = true; }
    });
    updateCounters();
    updateAllRowStates();
  });

  document.getElementById('btn-unselect-all')?.addEventListener('click', () => {
    const roots = cache.get('') || [];
    roots.forEach(n => {
      if(!savedState.excludes.includes(n.rel)) savedState.excludes.push(n.rel);
      savedState.includes = savedState.includes.filter(x => !(x === n.rel || x.startsWith(n.rel + '/')));
      const li = document.querySelector(`li[data-rel="${CSS.escape(n.rel)}"]`);
      const chk = li?.querySelector('input.chk');
      if(chk){ chk.checked = false; }
    });
    updateCounters();
    updateAllRowStates();
  });

  document.getElementById('btn-expand')?.addEventListener('click', async () => {
    // ロード済みだけ開く（DOMにある子ULをすべて表示）
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
  $filter?.addEventListener('input', (e) => {
    filterTree(e.target.value);
  });

  // 起動
  window.addEventListener('DOMContentLoaded', init);
})();
