/*!
 * knowledge_index.js
 * ナレッジ一覧ページ専用スクリプト。
 * 各項目に用意された「要約」と「全文」の切替（もっと見る/閉じる）だけを担当します。
 *
 * 前提のDOM構造（1アイテムあたり）:
 *   <div id="kc-<ID>">
 *     <div class="kc-summary"> ...要約テキスト... </div>
 *     <div class="kc-full d-none"> ...全文テキスト... </div>
 *     <a href="#" class="kc-toggle" id="toggle-<ID>">もっと見る</a>
 *   </div>
 *
 * ポイント:
 * - 文字列（コンテンツ）をJavaScript側に渡さない設計。JSは表示/非表示の切替のみ行う。
 * - クリックイベントはイベント委譲で1箇所に集約（.kc-toggle のみを対象）。
 * - 表示の切替は Bootstrapの d-none クラスの付け外しで実現。
 * - 改行の表示はCSS側で white-space: pre-wrap; を指定（テンプレート側）。
 *
 * メリット:
 * - 文字列の埋め込み/エスケープ/JSON化が不要 → 構文エラーやXSSのリスク低減。
 * - DOM構造とクラス名が分かれば挙動を理解しやすい。
 *
 * カスタマイズ例:
 * - ラベル文言（もっと見る/閉じる）を変更したい場合は、下の link.textContent の値を調整。
 * - DOM構造やクラス名を変える場合は、querySelector のセレクタと id 規約（kc-<ID> / toggle-<ID>）を合わせて変更。
 */
(function(){
  'use strict';

  // ドキュメント単位でクリックを監視し、.kc-toggle がクリックされたときだけ処理する
  document.addEventListener('click', function (e) {
    // クリックされた要素から、.kc-toggle に最も近い祖先を取得（ボタン内の子要素クリックにも対応）
    const link = e.target.closest('.kc-toggle');
    if (!link) return; // .kc-toggle 以外のクリックは無視

    e.preventDefault(); // a要素のデフォルト遷移（# への移動）を抑止

    // リンクIDは "toggle-<ID>" という規約。ここから <ID> を取り出す
    const id = link.id.replace('toggle-', '');

    // 対応するルート要素は "kc-<ID>" という規約
    const root = document.getElementById(`kc-${id}`);
    if (!root) return; // 想定外だが、対応要素が無ければ処理しない

    // ルート配下から要約表示と全文表示の要素を取得
    const summary = root.querySelector('.kc-summary');
    const full    = root.querySelector('.kc-full');
    if (!summary || !full) return; // 必須要素が無ければ何もしない

    // d-none クラスの有無で、現在の表示状態を判断
    const isShowingFull = !full.classList.contains('d-none');

    if (isShowingFull) {
      // 現在は「全文」を表示中 → 「要約」に切り替える
      full.classList.add('d-none');
      summary.classList.remove('d-none');
      link.textContent = 'もっと見る';
    } else {
      // 現在は「要約」を表示中 → 「全文」に切り替える
      summary.classList.add('d-none');
      full.classList.remove('d-none');
      link.textContent = '閉じる';
    }
  });
})();
