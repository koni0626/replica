(function(){
  const btnOpen = document.getElementById('open-theme-modal');
  const modalEl = document.getElementById('theme-modal');
  const btnSave = document.getElementById('btn-save-theme');
  const body = document.body;
  let selected = null;
  // 現在のテーマ（body のクラスから判定）。ダーク系は廃止のため正規化不要
  let current = Array.from(body.classList).find(c => c.startsWith('theme-')) || 'theme-sky';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  function showToast(msg){
    try{
      const wrap = document.createElement('div');
      wrap.className = 'position-fixed bottom-0 end-0 p-3';
      wrap.style.zIndex = 1080;
      wrap.innerHTML = `<div class="toast align-items-center text-bg-success border-0" role="status" aria-live="polite" aria-atomic="true">
          <div class="d-flex"><div class="toast-body">${msg}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>
        </div>`;
      document.body.appendChild(wrap);
      const toastEl = wrap.querySelector('.toast');
      const inst = window.bootstrap?.Toast?.getOrCreateInstance?.(toastEl, { delay: 1800 });
      if(inst){ inst.show(); setTimeout(()=>wrap.remove(), 2200); }
      else { toastEl.classList.add('show'); setTimeout(()=>wrap.remove(), 2200); }
    }catch(e){ console.log(msg); }
  }

  function applyThemePreview(theme){
    // 画面全体のテーマを切り替える（モーダル内カードの見た目は、カード自身のインラインCSS変数で固定する）
    const prev = Array.from(body.classList).find(c => c.startsWith('theme-'));
    if(prev) body.classList.remove(prev);
    body.classList.add(theme);
    current = theme;
  }

  function initCards(){
    document.querySelectorAll('#theme-modal .card').forEach(card => {
      const theme = card.getAttribute('data-theme');
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => {
        selected = theme;
        applyThemePreview(theme);
        document.querySelectorAll('#theme-modal .card').forEach(c=>c.classList.remove('border-accent'));
        card.classList.add('border-accent');
      });
    });
  }

  function saveTheme(){
    // projectId 初期化（navbar の data-project-id から取得）
    if(!window.THEME_BOOT) window.THEME_BOOT = {};
    if(!window.THEME_BOOT.projectId){
      const el = document.querySelector('[data-project-id]');
      if(el) window.THEME_BOOT.projectId = el.getAttribute('data-project-id');
    }

    const projectId = window.THEME_BOOT?.projectId;
    if(!projectId){ showToast('プロジェクトIDが取得できません'); return; }
    const theme = selected || current || 'theme-sky';
    fetch(`/projects/${projectId}/theme`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(csrf ? {'X-CSRFToken': csrf} : {}) },
      credentials: 'same-origin',
      body: JSON.stringify({ theme })
    }).then(r => {
      if(!r.ok) throw new Error('保存に失敗しました');
      return r.json();
    }).then(json => {
      if(json.ok){
        showToast('色の設定を保存しました');
        const m = window.bootstrap?.Modal?.getOrCreateInstance?.(modalEl);
        m?.hide();
      } else {
        showToast('保存エラー: ' + (json.error || ''));
      }
    }).catch(e => showToast(e.message));
  }

  document.addEventListener('DOMContentLoaded', () => {
    // window.THEME_BOOT.projectId を navbar の data-project-id から初期化（インラインJS撤廃のため）
    if(!window.THEME_BOOT) window.THEME_BOOT = {};
    if(!window.THEME_BOOT.projectId){
      const el = document.querySelector('[data-project-id]');
      if(el) window.THEME_BOOT.projectId = el.getAttribute('data-project-id');
    }

    if(btnOpen && modalEl){
      btnOpen.addEventListener('click', () => {
        // 初期選択を反映
        selected = current;
        document.querySelectorAll('#theme-modal .card').forEach(c=>c.classList.remove('border-accent'));
        const sel = document.querySelector(`#theme-modal .card[data-theme="${current}"]`);
        if(sel) sel.classList.add('border-accent');
        const m = window.bootstrap?.Modal?.getOrCreateInstance?.(modalEl);
        m?.show();
      });
      btnSave?.addEventListener('click', saveTheme);
      initCards();
    }
  });
})();
