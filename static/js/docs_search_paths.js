(function(){
  function run(){
    const BOOT = window.SEARCH_PATHS_BOOT || {};
    if(!BOOT.endpoints){
      console.error('[search-paths] BOOT.endpoints が未定義です。テンプレート内で window.SEARCH_PATHS_BOOT を先に定義してください。');
      return;
    }
    const treeRoot = document.getElementById('tree-root');
    const selCountEl = document.getElementById('sel-count');
    const excCountEl = document.getElementById('exc-count');
    const spinner = document.getElementById('tree-loading');

    const showSpinner = () => spinner && spinner.classList.remove('d-none');
    const hideSpinner = () => spinner && spinner.classList.add('d-none');

    function getCsrfTokenFromMeta(){
      const m = document.querySelector('meta[name="csrf-token"]');
      return m ? m.getAttribute('content') : '';
    }

    function fetchJSON(url, opts){
      const token = getCsrfTokenFromMeta();
      const init = Object.assign({
        headers: {'Content-Type':'application/json', 'X-CSRFToken': token},
        credentials: 'same-origin'
      }, opts||{});
      return fetch(url, init).then(async (r)=>{
        if(!r.ok){
          throw new Error(`HTTP ${r.status}`);
        }
        const ct = (r.headers.get('content-type')||'').toLowerCase();
        if(ct.includes('application/json')){
          return r.json();
        }
        const text = await r.text();
        throw new Error(`Unexpected content-type: ${ct}; body: ${text.slice(0,200)}`);
      });
    }

    function renderTree(nodes){
      const ul = document.createElement('ul');
      ul.className = 'list-unstyled';
      for(const n of nodes){
        const li = document.createElement('li');
        const row = document.createElement('div');
        row.className = 'd-flex align-items-center gap-2 py-1';

        const toggler = document.createElement('button');
        toggler.type = 'button';
        toggler.className = 'btn btn-sm btn-link text-decoration-none px-1';
        toggler.textContent = n.children && n.children.length ? '▸' : '·';
        toggler.dataset.state = 'closed';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.rel = n.rel;
        cb.className = 'form-check-input';

        const label = document.createElement('span');
        label.textContent = n.name;

        row.appendChild(toggler);
        row.appendChild(cb);
        row.appendChild(label);
        li.appendChild(row);

        if(n.children && n.children.length){
          const childWrap = document.createElement('div');
          childWrap.style.display = 'none';
          childWrap.style.marginLeft = '16px';
          childWrap.appendChild(renderTree(n.children));
          li.appendChild(childWrap);
          toggler.addEventListener('click', ()=>{
            const open = toggler.dataset.state === 'open';
            toggler.dataset.state = open? 'closed':'open';
            toggler.textContent = open? '▸':'▾';
            childWrap.style.display = open? 'none':'block';
          });
        } else {
          // 子が無い場合はトグル無効化（見た目は中点）
          toggler.disabled = true;
          toggler.classList.add('disabled');
        }

        ul.appendChild(li);
      }
      return ul;
    }

    function collectState(){
      const includes = new Set();
      const excludes = new Set();

      function dfs(nodeEl){
        const row = nodeEl.querySelector(':scope > div');
        const cb = row && row.querySelector('input[type="checkbox"]');
        const rel = cb ? cb.dataset.rel : '';
        const childUl = nodeEl.querySelector(':scope > div + div ul');

        if(cb){
          const checked = cb.checked;
          const indeterminate = cb.indeterminate;
          if(checked && !indeterminate){
            includes.add(rel);
            return; // 子孫は省略（圧縮）
          }
          if(!checked && !indeterminate){
            // 完全OFFのとき、親のONからのオーバーライドという意味で除外登録
            excludes.add(rel);
            return;
          }
        }
        if(childUl){
          for(const li of childUl.children){
            dfs(li);
          }
        }
      }

      const tree = treeRoot.querySelector('ul');
      if(tree){
        for(const li of tree.children){ dfs(li); }
      }
      return {includes:Array.from(includes), excludes:Array.from(excludes)};
    }

    function applyStateToTree(state){
      const includeSet = new Set(state.includes||[]);
      const excludeSet = new Set(state.excludes||[]);

      function dfs(nodeEl){
        const row = nodeEl.querySelector(':scope > div');
        const cb = row && row.querySelector('input[type="checkbox"]');
        const rel = cb ? cb.dataset.rel : '';
        const childWrap = nodeEl.querySelector(':scope > div + div');
        const childUl = childWrap && childWrap.querySelector('ul');

        let checked = false, ind = false;
        if(includeSet.has(rel)){
          checked = true; ind = false;
        }else if(excludeSet.has(rel)){
          checked = false; ind = false;
        }else if(childUl){
          let anyOn=false, anyOff=false;
          for(const li of childUl.children){
            const r = dfs(li);
            anyOn = anyOn || r.checked || r.indeterminate;
            anyOff = anyOff || (!r.checked && !r.indeterminate);
          }
          if(anyOn && anyOff){ ind = true; }
          else if(anyOn){ checked = true; }
        }
        if(cb){
          cb.checked = checked;
          cb.indeterminate = ind;
        }
        return {checked, indeterminate: ind};
      }

      const tree = treeRoot.querySelector('ul');
      if(tree){ for(const li of tree.children){ dfs(li); } }
      updateCounters();
    }

    function setAll(checked){
      treeRoot.querySelectorAll('input[type="checkbox"]').forEach(cb=>{
        cb.checked = !!checked; cb.indeterminate = false;
      });
      updateCounters();
    }

    function updateCounters(){
      const {includes, excludes} = collectState();
      if(selCountEl) selCountEl.textContent = includes.length;
      if(excCountEl) excCountEl.textContent = excludes.length;
    }

    function wireCheckboxCascade(){
      treeRoot.addEventListener('change', (e)=>{
        const cb = e.target;
        if(!(cb instanceof HTMLInputElement) || cb.type!== 'checkbox') return;
        // 親→子へ伝播
        const li = cb.closest('li');
        const subtree = li && li.querySelector(':scope > div + div');
        if(subtree){
          subtree.querySelectorAll('input[type="checkbox"]').forEach(k=>{
            k.checked = cb.checked; k.indeterminate = false;
          });
        }
        // 子→親の indeterminate 更新
        let p = li && li.parentElement && li.parentElement.closest('li');
        while(p){
          const parentCb = p.querySelector(':scope > div input[type="checkbox"]');
          const childUl = p.querySelector(':scope > div + div ul');
          if(parentCb && childUl){
            let anyOn=false, anyOff=false;
            childUl.querySelectorAll(':scope > li > div input[type="checkbox"]').forEach(x=>{
              if(x.checked || x.indeterminate) anyOn=true; else anyOff=true;
            });
            parentCb.indeterminate = anyOn && anyOff;
            parentCb.checked = !parentCb.indeterminate && anyOn;
          }
          p = p.parentElement && p.parentElement.closest('li');
        }
        updateCounters();
      });
    }

    function applyProfile(profile){
      // 簡易。includes をプロファイルのトップディレクトリに設定
      let inc=[];
      if(profile==='flask'){
        inc=["controllers","forms","services","models","Models","templates","tools"];
      }else if(profile==='cakephp4'){
        inc=["src/Controller","src/Model","src/View","templates","config","webroot","plugins"];
      }
      const state = {includes:inc, excludes:[]};
      applyStateToTree(state);
    }

    // ここから初期化（スピナー制御を統合）
    showSpinner();
    Promise.all([
      fetchJSON(BOOT.endpoints.tree),
      fetchJSON(BOOT.endpoints.stateGet)
    ]).then(([tree, state])=>{
      treeRoot.innerHTML = '';
      treeRoot.appendChild(renderTree(tree));
      wireCheckboxCascade();
      applyStateToTree(state);
    }).catch(err=>{
      console.error('[search-paths] 初期化でエラー:', err);
      treeRoot.innerHTML = '<div class="text-danger small">ツリーの読み込みに失敗しました。再読み込みしてください。</div>';
    }).finally(()=>{
      hideSpinner();
      // フェールセーフ（もしスピナーが残ってしまった場合の保険）
      setTimeout(hideSpinner, 0);
    });

    // 操作ボタン
    document.getElementById('btn-expand')?.addEventListener('click', ()=>{
      treeRoot.querySelectorAll('button[data-state]').forEach(btn=>{
        const childWrap = btn.parentElement.nextElementSibling;
        if(childWrap){ btn.dataset.state='open'; btn.textContent='▾'; childWrap.style.display='block'; }
      });
    });
    document.getElementById('btn-collapse')?.addEventListener('click', ()=>{
      treeRoot.querySelectorAll('button[data-state]').forEach(btn=>{
        const childWrap = btn.parentElement.nextElementSibling;
        if(childWrap){ btn.dataset.state='closed'; btn.textContent='▸'; childWrap.style.display='none'; }
      });
    });
    document.getElementById('btn-select-all')?.addEventListener('click', ()=> setAll(true));
    document.getElementById('btn-unselect-all')?.addEventListener('click', ()=> setAll(false));
    document.getElementById('btn-apply-flask')?.addEventListener('click', ()=> applyProfile('flask'));
    document.getElementById('btn-apply-cake')?.addEventListener('click', ()=> applyProfile('cakephp4'));
    document.getElementById('btn-save')?.addEventListener('click', ()=>{
      const state = collectState();
      showSpinner();
      fetchJSON(BOOT.endpoints.statePost, {method:'POST', body: JSON.stringify(state)}).then(r=>{
        if(r && r.ok!==false){ alert('保存しました'); }
        else{ alert('保存に失敗しました'); }
      }).catch(()=> alert('保存エラー'))
        .finally(()=> hideSpinner());
    });
  }

  // DOM 準備完了で初期化（読み込み済みなら即実行）
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', run);
  }else{
    run();
  }
})();