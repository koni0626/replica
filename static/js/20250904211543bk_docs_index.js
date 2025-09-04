/* docs_index.js
 * ドキュメント作成ページ用のクライアントスクリプト。
 * - Markdownのプレビュー（入力/出力/コミット表示）
 * - ストリーミング生成（通常/ツール）
 * - コード生成モーダル（ZIPダウンロード/要約表示）
 * - クリップボードコピー、トースト表示
 * - メモ保存
 *
 * テンプレート側（Jinja）で提供される要素ID・構造を前提とする。
 * 依存: marked, DOMPurify, highlight.js, highlightjs-line-numbers, htmx（任意）, Bootstrap（モーダル）
 */
(function(){
  'use strict';

  // すべての初期化は DOMContentLoaded 後に実行（外部スクリプトの読み込み完了後を担保）
  window.addEventListener('DOMContentLoaded', () => {
    // marked のグローバル設定（GFM + 改行を <br> に変換）
    if (window.marked && typeof window.marked.setOptions === 'function') {
      window.marked.setOptions({ gfm: true, breaks: true });
    }

    /**
     * コードブロックのハイライト（安全ラッパー）
     * highlight.js が読み込まれていない場合でも例外を投げない。
     * @param {HTMLElement} codeEl 対象の <code> 要素
     * @param {boolean} [addLineNumbers=true] 行番号を付与するか（highlightjs-line-numbers.js が前提）
     */
    function safeHighlight(codeEl, addLineNumbers = true) {
      // hljs 本体が無い／初期化前でも落ちないようガード
      if (typeof window !== 'undefined' &&
          window.hljs &&
          typeof window.hljs.highlightElement === 'function') {
        window.hljs.highlightElement(codeEl);
        if (addLineNumbers && typeof window.hljs.lineNumbersBlock === 'function') {
          window.hljs.lineNumbersBlock(codeEl);
        }
      }
    }

    /**
     * Markdown文字列を HTML にレンダリングし、指定ノードに挿入する。
     * - XSS対策として DOMPurify でサニタイズ（利用可能な場合）
     * - 表とコードに対して補助クラスを付与
     * - 各コードブロック先頭に「コピー」ボタンを付与
     *
     * @param {HTMLElement} targetEl 出力先要素
     * @param {string} srcText Markdown テキスト
     * @param {boolean} [addLineNumbers=true] コードブロックに行番号を付与するか
     */
    function renderMarkdownTo(targetEl, srcText, addLineNumbers = true) {
      const md = (srcText || '');
      // Markdown -> HTML（marked が無い場合は素通し）
      const dirty = window.marked ? window.marked.parse(md) : md;
      // サニタイズ（DOMPurify が無い場合は素通し）
      const html  = window.DOMPurify ? window.DOMPurify.sanitize(dirty) : dirty;
      targetEl.innerHTML = html;

      // スタイル補助クラス付与（見た目向上）
      targetEl.querySelectorAll('table').forEach(t => t.classList.add('table','table-sm','table-bordered','align-middle'));
      targetEl.querySelectorAll('code').forEach(c => c.classList.add('bg-light','px-1','rounded'));

      // コードブロック整形＋コピー
      targetEl.querySelectorAll('pre > code').forEach(c => {
        // pre 要素に装飾
        c.parentElement.classList.add('p-3','bg-light','rounded');

        // シンタックスハイライト＋行番号（安全に）
        safeHighlight(c, addLineNumbers);

        // コピー・ボタンを先頭に挿入
        const button = document.createElement('button');
        button.textContent = 'ソースコードをコピーする';
        button.className = 'copy-button';
        button.onclick = () => {
          const text = c.innerText
            .replace(/^\t+/gm, '')  // 各行の先頭のタブを削除（スペース置換はしない）
            .replace(/\n{2,}/g, '\n'); // 連続する空行を1つに
          copyToClipboard(text);
        };
        c.parentElement.prepend(button);
      });
    }

    /**
     * 文字列をクリップボードへコピーし、トースト通知を表示する。
     * Clipboard API が使用できない場合はフォールバックを行う。
     * @param {string} text コピーしたいテキスト
     */
    async function copyToClipboard(text) {
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          // 非HTTPS等の環境向けフォールバック
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.top = '-1000px';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        showToast('ソースコードをコピーしました');
      } catch (err) {
        console.error('コピーに失敗しました', err);
        showToast('コピーに失敗しました');
      }
    }

    // 入出力プレビュー要素の参照
    const promptInput   = document.getElementById('prompt-input');
    const promptPreview = document.getElementById('prompt-preview');
    const outputInput   = document.getElementById('output-input');
    const outputPreview = document.getElementById('output-preview');

    // 入力側プレビュー（キータイプで都度レンダリング）
    if (promptInput && promptPreview) {
      const updatePrompt = () => renderMarkdownTo(promptPreview, promptInput.value, false); // 行番号ナシ
      promptInput.addEventListener('input', updatePrompt);
      updatePrompt();
    }

    // 出力側プレビュー（ストリーミング中は行番号ナシ→完了後に付け直す）
    if (outputInput && outputPreview) {
      const updateOutput = () => renderMarkdownTo(outputPreview, outputInput.value, false);
      outputInput.addEventListener('input', updateOutput);
      updateOutput();
    }

    // ルート要素（テンプレートから data-* を受け取る）
    const root = document.getElementById('docs-root');

    // ストリーミング生成（通常/ツール）を内包するスコープ
    (function(){
      // 主要UIの参照
      const form = document.querySelector("form[method='post']");
      const btnGenerate = form?.querySelector('#btn-generate');
      const btnGenerateTool = form?.querySelector('#btn-generate_tool');
      const btnCommit   = form?.querySelector('#btn-commit');

      const promptInput = document.getElementById('prompt-input');
      const outputInput = document.getElementById('output-input');
      const outputPreview = document.getElementById('output-preview');
      const spinner = document.getElementById('loading-spinner');

      // URL はテンプレート側で data 属性に埋め、ここで参照する
      const streamUrl = root?.getAttribute('data-stream-url');
      const streamUrlTool = root?.getAttribute('data-stream-url-tool');

      /**
       * 通常のストリーミング生成（/docs/stream_generate）
       * - 入力プロンプトを送信し、レスポンスBodyを逐次読み取り
       * - 出力テキストエリアとプレビューを随時更新
       */
      async function streamGenerate(e) {
        e.preventDefault();

        // 初期化
        outputInput.value = '';
        renderMarkdownTo(outputPreview, '', false);

        // UIロック
        spinner?.classList.remove('d-none');
        btnGenerate?.setAttribute('disabled', 'disabled');
        btnGenerateTool?.setAttribute('disabled', 'disabled');
        btnCommit?.setAttribute('disabled', 'disabled');

        const csrftoken = document.querySelector("input[name='csrf_token']")?.value;

        try {
          const resp = await fetch(streamUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(csrftoken ? { 'X-CSRFToken': csrftoken } : {})
            },
            body: JSON.stringify({ prompt: promptInput.value || '' })
          });

          if (!resp.ok || !resp.body) {
            outputInput.value = `（エラー）${resp.status} ${resp.statusText}`;
            renderMarkdownTo(outputPreview, outputInput.value, true);
            return;
          }

          // ReadableStream を逐次読み取り
          const reader = resp.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let acc = '';
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            if (chunk) {
              acc += chunk;
              outputInput.value = acc;
              renderMarkdownTo(outputPreview, acc, false); // 途中は行番号ナシ
            }
          }
        } catch (err) {
          console.error(err);
          outputInput.value = '（エラー）ストリーミング中に問題が発生しました。';
          renderMarkdownTo(outputPreview, outputInput.value, true);
        } finally {
          // UIアンロック
          spinner?.classList.add('d-none');
          btnGenerate?.removeAttribute('disabled');
          btnGenerateTool?.removeAttribute('disabled');
          btnCommit?.removeAttribute('disabled');
          // 完了後に最終描画（行番号あり）
          renderMarkdownTo(outputPreview, outputInput.value, true);
        }
      }

      /**
       * ツール連携ありのストリーミング生成（/docs/stream_generate_tool）
       * - 挙動は streamGenerate と同等、呼び出しURLが異なる
       */
      async function streamGenerateTool(e) {
        e.preventDefault();

        outputInput.value = '';
        renderMarkdownTo(outputPreview, '', false);

        spinner?.classList.remove('d-none');
        btnGenerate?.setAttribute('disabled', 'disabled');
        btnGenerateTool?.setAttribute('disabled', 'disabled');
        btnCommit?.setAttribute('disabled', 'disabled');

        const csrftoken = document.querySelector("input[name='csrf_token']")?.value;

        try {
          const resp = await fetch(streamUrlTool, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(csrftoken ? { 'X-CSRFToken': csrftoken } : {})
            },
            body: JSON.stringify({ prompt: promptInput.value || '' })
          });

          if (!resp.ok || !resp.body) {
            outputInput.value = `（エラー）${resp.status} ${resp.statusText}`;
            renderMarkdownTo(outputPreview, outputInput.value, true);
            return;
          }

          const reader = resp.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let acc = '';
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            if (chunk) {
              acc += chunk;
              outputInput.value = acc;
              renderMarkdownTo(outputPreview, acc, false);
            }
          }
        } catch (err) {
          console.error(err);
          outputInput.value = '（エラー）ストリーミング中に問題が発生しました。';
          renderMarkdownTo(outputPreview, outputInput.value, true);
        } finally {
          spinner?.classList.add('d-none');
          btnGenerate?.removeAttribute('disabled');
          btnGenerateTool?.removeAttribute('disabled');
          btnCommit?.removeAttribute('disabled');
          renderMarkdownTo(outputPreview, outputInput.value, true);
        }
      }

      // ボタンにハンドラを付与
      if (btnGenerate) btnGenerate.addEventListener('click', streamGenerate);
      if (btnGenerateTool) btnGenerateTool.addEventListener('click', streamGenerateTool);
    })();

    /**
     * コミット欄（左右）の Markdown を初期描画/htmx更新後に再描画する。
     * - #commit-left-src / #commit-right-src: textarea（Markdownソース）
     * - #commit-left / #commit-right: 表示先要素
     */
    function renderCommitsSection() {
      const leftSrc  = document.getElementById('commit-left-src');
      const leftDst  = document.getElementById('commit-left');
      const rightSrc = document.getElementById('commit-right-src');
      const rightDst = document.getElementById('commit-right');
      if (leftSrc && leftDst)   renderMarkdownTo(leftDst,  leftSrc.value, true);
      if (rightSrc && rightDst) renderMarkdownTo(rightDst, rightSrc.value, true);
    }
    // 初期描画
    renderCommitsSection();
    // htmx による置換後に再描画
    document.body.addEventListener('htmx:afterSwap', function (evt) {
      if (evt.target && evt.target.id === 'commits-section') {
        renderCommitsSection();
        document.getElementById('commits-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });

    // ========= テンプレートから呼び出されるグローバル関数 =========

    /**
     * 任意の textarea の内容を Markdown としてコピーする（UI上のボタンから呼ばれる想定）
     * @param {string} textareaId 対象の textarea 要素ID
     */
    window.copyMarkdown = async function copyMarkdown(textareaId) {
      const el = document.getElementById(textareaId);
      if (!el) return;
      const text = el.value ?? '';
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.top = '-1000px';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        showToast('Markdownをコピーしました');
      } catch (e) {
        console.error(e);
        showToast('コピーに失敗しました');
      }
    };

    /**
     * コード生成モーダルの実行（左/右どちらかのMarkdownを対象に送信）
     * @param {('left'|'right')} side 対象ペインの指定
     */
    window.handleCodegen = async function handleCodegen(side) {
      const taId = side === 'left' ? 'commit-left-src' : 'commit-right-src';
      const ta = document.getElementById(taId);
      if (!ta || !(ta.value || '').trim()) {
        showToast('コード生成対象のMarkdownがありません');
        return;
      }

      // モーダル初期化
      const modalEl = document.getElementById('codegenModal');
      const modal = new bootstrap.Modal(modalEl);
      const statusEl = document.getElementById('codegen-status');
      const actionsEl = document.getElementById('codegen-actions');
      const linkEl = document.getElementById('codegen-zip-link');
      const summaryEl = document.getElementById('codegen-summary');

      statusEl.textContent = '生成中…';
      actionsEl.classList.add('d-none');
      linkEl.removeAttribute('href');
      summaryEl.textContent = '';
      modal.show();

      const csrftoken = document.querySelector("input[name='csrf_token']")?.value;
      const url = root?.getAttribute('data-codegen-url');

      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(csrftoken ? { 'X-CSRFToken': csrftoken } : {})
          },
          body: JSON.stringify({
            spec_markdown: ta.value || '',
            project_name: '掲示板システム'
          })
        });

        if (!resp.ok) {
          statusEl.textContent = `エラー: ${resp.status} ${resp.statusText}`;
          return;
        }
        const data = await resp.json();
        if (!data.ok) {
          statusEl.textContent = `エラー: ${data.error || 'unknown'}`;
          return;
        }
        statusEl.textContent = '生成が完了しました。';
        summaryEl.textContent = data.summary || '';
        if (data.zip_url) {
          linkEl.href = data.zip_url;
          actionsEl.classList.remove('d-none');
        }
      } catch (e) {
        console.error(e);
        statusEl.textContent = 'エラーが発生しました。コンソールを確認してください。';
      }
    };

    /**
     * 画面右下に簡易トーストを表示（CSS .copy-toast / .copy-toast.show を想定）
     * @param {string} msg 表示メッセージ
     */
    window.showToast = function showToast(msg) {
      const t = document.createElement('div');
      t.textContent = msg;
      t.className = 'copy-toast';
      document.body.appendChild(t);
      setTimeout(() => t.classList.add('show'), 10);
      setTimeout(() => {
        t.classList.remove('show');
        setTimeout(() => document.body.removeChild(t), 200);
      }, 1500);
    };

    /**
     * 左側コミット（current_left）のメモを保存する。
     * - docs-root の data-left-doc-id / data-csrf から必要情報を取得
     */
    window.saveNote = function saveNote() {
      const noteEl = document.getElementById('note-input');
      const docId = root?.getAttribute('data-left-doc-id');
      const csrf = root?.getAttribute('data-csrf');
      if (!docId) {
        alert('左側のコミットが無いため、メモを保存できません。');
        return;
      }
      fetch(`/docs/save_note/${docId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf || ''
        },
        body: JSON.stringify({ note: noteEl ? noteEl.value : '' })
      }).then(response => {
        if (response.ok) {
          alert('メモが保存されました');
        } else {
          alert('メモの保存に失敗しました');
        }
      });
    };
  });
})();
