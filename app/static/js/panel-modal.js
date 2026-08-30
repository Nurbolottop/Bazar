/* Единые модальные окна панели вместо системных confirm()/prompt().
 *
 * На формах:
 *   data-confirm="Текст вопроса"     — окно подтверждения
 *   data-confirm-ok="Подтвердить"    — подпись кнопки (необязательно)
 *   data-reason="Заголовок"          — окно с обязательной причиной;
 *                                      значение попадает в input[name=reason].
 *   форма._presets = ['...']         — готовые варианты причины (кнопки-подсказки)
 *
 * Из скриптов: window.panelConfirm(text, opts) -> Promise<bool>
 *              window.panelPrompt(text, opts)  -> Promise<string|null>
 */
(function () {
  'use strict';

  const DANGER_RE = /отклон|отмен|удал|сторно|освобод|архив|заблокир|расформ|убрать/i;

  let overlay, card, textEl, inputWrap, textarea, presetsEl, errorEl, okBtn, cancelBtn;
  let resolver = null;
  let inputMode = false;
  let lastFocused = null;

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'pmodal';
    overlay.innerHTML =
      '<div class="pmodal-card" role="dialog" aria-modal="true">' +
      '  <div class="pmodal-text"></div>' +
      '  <div class="pmodal-input" hidden>' +
      '    <div class="pmodal-presets"></div>' +
      '    <textarea rows="2" placeholder="Причина…"></textarea>' +
      '    <div class="pmodal-error" hidden>Укажите причину</div>' +
      '  </div>' +
      '  <div class="pmodal-actions">' +
      '    <button type="button" class="btn pmodal-cancel">Отмена</button>' +
      '    <button type="button" class="btn primary pmodal-ok">Подтвердить</button>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(overlay);
    card = overlay.firstElementChild;
    textEl = card.querySelector('.pmodal-text');
    inputWrap = card.querySelector('.pmodal-input');
    textarea = card.querySelector('textarea');
    presetsEl = card.querySelector('.pmodal-presets');
    errorEl = card.querySelector('.pmodal-error');
    okBtn = card.querySelector('.pmodal-ok');
    cancelBtn = card.querySelector('.pmodal-cancel');

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) settle(inputMode ? null : false);
    });
    cancelBtn.addEventListener('click', () => settle(inputMode ? null : false));
    okBtn.addEventListener('click', accept);
    textarea.addEventListener('input', () => { errorEl.hidden = true; });
    textarea.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); accept(); }
    });
    document.addEventListener('keydown', function (e) {
      if (!resolver) return;
      if (e.key === 'Escape') { e.stopPropagation(); settle(inputMode ? null : false); }
    }, true);
  }

  function accept() {
    if (inputMode) {
      const value = textarea.value.trim();
      if (!value) { errorEl.hidden = false; textarea.focus(); return; }
      settle(value);
    } else settle(true);
  }

  function settle(result) {
    if (!resolver) return;
    const resolve = resolver;
    resolver = null;
    overlay.classList.remove('open');
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    resolve(result);
  }

  function open(text, opts) {
    opts = opts || {};
    if (!overlay) build();
    if (resolver) settle(inputMode ? null : false);   // предыдущее окно закрывается
    lastFocused = document.activeElement;
    inputMode = Boolean(opts.input);

    textEl.textContent = text || 'Вы уверены?';
    inputWrap.hidden = !inputMode;
    errorEl.hidden = true;
    textarea.value = '';
    if (opts.placeholder) textarea.placeholder = opts.placeholder;

    presetsEl.innerHTML = '';
    (opts.presets || []).forEach(function (preset) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'pmodal-preset';
      chip.textContent = preset;
      chip.addEventListener('click', function () {
        textarea.value = preset;
        errorEl.hidden = true;
        textarea.focus();
      });
      presetsEl.appendChild(chip);
    });

    const danger = opts.danger !== undefined ? opts.danger : DANGER_RE.test(text || '');
    okBtn.textContent = opts.ok || 'Подтвердить';
    okBtn.classList.toggle('danger-solid', danger);

    overlay.classList.add('open');
    setTimeout(() => (inputMode ? textarea : okBtn).focus(), 30);
    return new Promise(resolve => { resolver = resolve; });
  }

  window.panelConfirm = (text, opts) => open(text, opts || {});
  window.panelPrompt = (text, opts) => open(text, Object.assign({ input: true }, opts || {}));

  // Автопривязка к формам
  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
    const submitter = e.submitter;

    if (form.dataset.reason !== undefined) {
      e.preventDefault();
      window.panelPrompt(form.dataset.reason || 'Укажите причину', {
        ok: form.dataset.confirmOk || 'Подтвердить',
        presets: form._presets || [],
      }).then(function (value) {
        if (value === null) return;
        const input = form.querySelector('[data-reason-target]') ||
          form.querySelector('[name=reason]');
        if (input) input.value = value;
        form.dataset.confirmed = '1';
        submitter ? form.requestSubmit(submitter) : form.requestSubmit();
      });
      return;
    }
    if (submitter && submitter.dataset.confirm) {
      e.preventDefault();
      window.panelConfirm(submitter.dataset.confirm, { ok: submitter.dataset.confirmOk })
        .then(function (ok) {
          if (!ok) return;
          form.dataset.confirmed = '1';
          form.requestSubmit(submitter);
        });
      return;
    }
    if (form.dataset.confirm) {
      e.preventDefault();
      window.panelConfirm(form.dataset.confirm, { ok: form.dataset.confirmOk })
        .then(function (ok) {
          if (!ok) return;
          form.dataset.confirmed = '1';
          submitter ? form.requestSubmit(submitter) : form.requestSubmit();
        });
    }
  }, true);
})();
