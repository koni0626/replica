(function(){
  'use strict';
  document.addEventListener('DOMContentLoaded', () => {
    const listRoot = document.body; // デリゲーション

    // 複製ボタン（data-action="duplicate"）
    listRoot.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action="duplicate"][data-project-id]');
      if(!btn) return;
      const pid = btn.getAttribute('data-project-id');
      const form = document.getElementById('duplicate-form');
      if(form){
        form.action = `/projects/duplicate/${pid}`;
        const modal = new bootstrap.Modal(document.getElementById('duplicate-dialog'));
        modal.show();
      }
    });

    // 削除フォームの confirm（data-confirm 属性）
    listRoot.addEventListener('submit', (e) => {
      const form = e.target.closest('form[data-confirm]');
      if(!form) return;
      const msg = form.getAttribute('data-confirm') || '削除しますか？';
      if(!confirm(msg)){
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });
})();
