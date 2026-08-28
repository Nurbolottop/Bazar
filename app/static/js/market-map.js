/* Карта рынка (Konva.js) — простая карта с редким редактированием.
 *
 * Тачпад: скролл двумя пальцами = перемещение, pinch/Ctrl+прокрутка = масштаб.
 * Просмотр: клик по месту — floating-панель с информацией.
 * Редактор: перетаскивание, swap по клику («Поменять местами» → клик по второму),
 * добавление через модалку в центр экрана, resize только через «••• → Изменить размер».
 * Backend, API и модель позиций не меняются.
 */
(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const cfg = JSON.parse(byId('map-config').textContent);

  const el = {
    stageWrap: byId('map-stage-wrap'),
    canvas: byId('map-canvas'),
    tooltip: byId('map-tooltip'),
    status: byId('map-save-status'),
    search: byId('map-search'),
    zoomIn: byId('map-zoom-in'),
    zoomOut: byId('map-zoom-out'),
    zoomLevel: byId('map-zoom-level'),
    fit: byId('map-fit'),
    help: byId('map-help'),
    legendPop: byId('map-legend-pop'),
    modeBtn: byId('map-mode-btn'),
    addBtn: byId('map-add-btn'),
    addModal: byId('map-add-modal'),
    addClose: byId('map-add-close'),
    addSearch: byId('map-add-search'),
    addList: byId('map-add-list'),
    float: byId('map-float'),
    floatTitle: byId('map-float-title'),
    floatInfo: byId('map-float-info'),
    floatActions: byId('map-float-actions'),
    floatMenu: byId('map-float-menu'),
    swapHint: byId('map-swap-hint'),
    emptyHint: byId('map-empty-hint'),
    emptyText: byId('map-empty-text'),
    confirmBox: byId('map-confirm'),
    confirmText: byId('map-confirm-text'),
    confirmYes: byId('map-confirm-yes'),
    confirmNo: byId('map-confirm-no'),
  };

  const COLORS = {
    free:     { fill: '#ffffff', stroke: '#5ea98c', text: '#0b6e4f' },
    occupied: { fill: '#0b6e4f', stroke: '#095c42', text: '#ffffff' },
    debt:     { fill: '#b42318', stroke: '#8e1b12', text: '#ffffff' },
    repair:   { fill: '#cfd6d2', stroke: '#aab4ae', text: '#5c6b64' },
    empty:    { fill: '#f4f6f5', stroke: '#c3ccc7', text: '#8a958f' },
  };
  const HIGHLIGHT = '#1d4fa1';
  const HIT_PAD = 12;             // расширенная зона нажатия вокруг места

  let stage, planLayer, spotsLayer, transformer;
  let plan = null;
  let editMode = false;
  let selected = null;            // выбранная группа
  let resizing = false;           // ручки resize включены для выбранного
  let swapSource = null;          // место, ожидающее второй клик для обмена
  let dragSwapTarget = null;      // подсвеченная цель при перетаскивании
  let pendingSwap = null;         // {sourceGroup, targetGroup, viaDrag}
  let unplaced = [];
  const nodes = new Map();

  // ================================================================ сеть
  function setStatus(text, isError) {
    el.status.textContent = text || '';
    el.status.style.color = isError ? 'var(--danger)' : 'var(--muted)';
    if (text && !isError) setTimeout(() => {
      if (el.status.textContent === text) el.status.textContent = '';
    }, 2200);
  }

  async function api(url, method, body) {
    setStatus('Сохранение…');
    const response = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await response.json(); } catch (e) {}
    if (!response.ok) {
      setStatus(data.error || 'Ошибка сохранения', true);
      const error = new Error(data.error || response.status);
      error.status = response.status;
      throw error;
    }
    setStatus('Сохранено');
    return data;
  }

  const urlFor = (template, id) => template.replace('/0/', '/' + id + '/');

  // ================================================================ сцена
  function colorFor(p) {
    if (!p.spot_id) return COLORS.empty;
    if (p.status === 'repair') return COLORS.repair;
    if (p.has_debt) return COLORS.debt;
    if (p.status === 'occupied') return COLORS.occupied;
    return COLORS.free;
  }

  function buildStage() {
    stage = new Konva.Stage({
      container: 'map-canvas',
      width: el.canvas.clientWidth,
      height: el.canvas.clientHeight,
      draggable: true,             // drag фона мышью/пальцем = pan
    });
    planLayer = new Konva.Layer({ listening: false });
    spotsLayer = new Konva.Layer();
    stage.add(planLayer);
    stage.add(spotsLayer);

    planLayer.add(new Konva.Rect({
      x: 0, y: 0, width: plan.width, height: plan.height,
      fill: '#ffffff', stroke: '#b9c6c0', strokeWidth: 2, cornerRadius: 6,
      shadowColor: 'rgba(16,32,26,.16)', shadowBlur: 18, shadowOffsetY: 4,
    }));
    const dots = new Konva.Shape({
      sceneFunc: function (ctx) {
        const step = 50;
        ctx.beginPath();
        for (let x = step; x < plan.width; x += step)
          for (let y = step; y < plan.height; y += step) {
            ctx.moveTo(x, y);
            ctx.arc(x, y, 1.1, 0, Math.PI * 2);
          }
        ctx.fillStyle = '#d9e0dc';
        ctx.fill();
      },
      listening: false,
    });
    dots.cache({ x: 0, y: 0, width: plan.width, height: plan.height });
    planLayer.add(dots);

    transformer = new Konva.Transformer({
      rotateEnabled: false, flipEnabled: false, keepRatio: false,
      anchorSize: 10, anchorCornerRadius: 3,
      borderStroke: HIGHLIGHT, anchorStroke: HIGHLIGHT,
      boundBoxFunc: (oldBox, newBox) =>
        (newBox.width < 12 || newBox.height < 12) ? oldBox : newBox,
    });
    spotsLayer.add(transformer);

    // Тачпад: двумя пальцами — pan; pinch (wheel+ctrlKey) и Ctrl+колесо — zoom.
    // Обычный скролл НИКОГДА не масштабирует.
    stage.on('wheel', function (e) {
      e.evt.preventDefault();
      if (e.evt.ctrlKey || e.evt.metaKey) {
        const factor = Math.exp(-e.evt.deltaY * 0.012);
        zoomAt(stage.getPointerPosition(), factor);
      } else {
        stage.position({
          x: stage.x() - e.evt.deltaX,
          y: stage.y() - e.evt.deltaY,
        });
        onViewportChanged();
      }
    });

    stage.on('click tap', function (e) {
      if (e.target === stage || e.target.getLayer() === planLayer) {
        if (swapSource) return;    // в режиме обмена клик мимо не сбрасывает выбор цели
        select(null);
        closeMenus();
      }
    });
    stage.on('dragmove', onViewportChanged);   // pan перетаскиванием фона

    window.addEventListener('resize', function () {
      stage.width(el.canvas.clientWidth);
      stage.height(el.canvas.clientHeight);
      placeFloat();
    });
  }

  function onViewportChanged() {
    updateZoomLabel();
    placeFloat();
  }

  function zoomAt(point, factor) {
    const oldScale = stage.scaleX();
    const newScale = Math.min(4, Math.max(0.1, oldScale * factor));
    if (!point) point = { x: stage.width() / 2, y: stage.height() / 2 };
    const mapPoint = {
      x: (point.x - stage.x()) / oldScale,
      y: (point.y - stage.y()) / oldScale,
    };
    stage.scale({ x: newScale, y: newScale });
    stage.position({
      x: point.x - mapPoint.x * newScale,
      y: point.y - mapPoint.y * newScale,
    });
    onViewportChanged();
  }

  function updateZoomLabel() {
    el.zoomLevel.textContent = Math.round(stage.scaleX() * 100) + '%';
  }

  function fitAll() {
    const padding = 60;
    const scale = Math.min(
      (stage.width() - padding) / plan.width,
      (stage.height() - padding) / plan.height, 2);
    stage.scale({ x: scale, y: scale });
    stage.position({
      x: (stage.width() - plan.width * scale) / 2,
      y: (stage.height() - plan.height * scale) / 2,
    });
    onViewportChanged();
  }

  function centerOn(meta, minScale) {
    const scale = Math.max(stage.scaleX(), minScale || 1);
    stage.scale({ x: scale, y: scale });
    stage.position({
      x: stage.width() / 2 - (meta.x + meta.width / 2) * scale,
      y: stage.height() / 2 - (meta.y + meta.height / 2) * scale,
    });
    onViewportChanged();
  }

  // ================================================================ узлы
  function makeNode(p) {
    const c = colorFor(p);
    const group = new Konva.Group({ x: p.x, y: p.y, draggable: editMode });
    const rect = new Konva.Rect({
      name: 'body', width: p.width, height: p.height,
      fill: c.fill, stroke: c.stroke, strokeWidth: 1.5, cornerRadius: 4,
    });
    // Расширенная hit-area: попадать в маленькое место тачпадом проще
    rect.hitFunc(function (ctx, shape) {
      ctx.beginPath();
      ctx.rect(-HIT_PAD, -HIT_PAD,
        shape.width() + HIT_PAD * 2, shape.height() + HIT_PAD * 2);
      ctx.closePath();
      ctx.fillStrokeShape(shape);
    });
    const label = new Konva.Text({
      width: p.width, height: p.height, text: p.code || '·',
      align: 'center', verticalAlign: 'middle',
      fontSize: 13, fontStyle: 'bold', fill: c.text,
      fontFamily: '-apple-system, Segoe UI, Roboto, Arial, sans-serif',
      listening: false,
    });
    group.add(rect); group.add(label);
    group.setAttr('meta', p);

    group.on('mouseenter', function () {
      stage.container().style.cursor = editMode ? 'move' : 'pointer';
      if (!editMode && !selected) showTooltip(p);
    });
    group.on('mousemove', moveTooltip);
    group.on('mouseleave', function () {
      stage.container().style.cursor = 'grab';
      el.tooltip.hidden = true;
    });

    group.on('click tap', function (e) {
      e.cancelBubble = true;
      if (swapSource && group !== swapSource) { askSwap(swapSource, group, false); return; }
      select(group);
    });

    group.on('dragstart', function () { el.float.hidden = true; el.tooltip.hidden = true; });
    group.on('dragmove', function () {
      const target = findOverlap(group);
      if (target !== dragSwapTarget) {
        clearDragHighlight();
        dragSwapTarget = target;
        if (target) setStroke(target, HIGHLIGHT, 3);
      }
    });
    group.on('dragend', function () {
      const target = dragSwapTarget;
      clearDragHighlight();
      const meta = group.getAttr('meta');
      if (target && meta.spot_id) { askSwap(group, target, true); return; }
      saveGeometry(group);
    });
    group.on('transformend', function () { onTransformEnd(group); });
    return group;
  }

  function setStroke(group, color, width) {
    group.findOne('.body').stroke(color).strokeWidth(width);
  }
  function resetStroke(group) {
    const meta = group.getAttr('meta');
    setStroke(group, colorFor(meta).stroke, 1.5);
  }
  function clearDragHighlight() {
    if (dragSwapTarget) { resetStroke(dragSwapTarget); dragSwapTarget = null; }
  }

  function refreshNode(p) {
    const old = nodes.get(p.id);
    if (old) old.destroy();
    const node = makeNode(p);
    nodes.set(p.id, node);
    spotsLayer.add(node);
    transformer.moveToTop();
    updateEmptyHint();
    return node;
  }

  function renderPositions(positions) {
    nodes.forEach(n => n.destroy());
    nodes.clear();
    positions.forEach(p => {
      const node = makeNode(p);
      nodes.set(p.id, node);
      spotsLayer.add(node);
    });
    transformer.moveToTop();
    updateEmptyHint();
  }

  function updateEmptyHint() {
    const empty = nodes.size === 0;
    el.emptyHint.hidden = !empty;
    if (empty) {
      el.emptyText.textContent = editMode
        ? 'Нажмите «+ Добавить», выберите место — оно появится в центре карты'
        : 'На карте пока пусто — нажмите «Редактировать», чтобы расставить места';
    }
  }

  // ================================================================ tooltip
  function showTooltip(p) {
    let html = '<b>Место ' + (p.code || '—') + '</b>';
    if (p.tenant) html += '<br>' + p.tenant;
    html += '<br>' + statusText(p);
    el.tooltip.innerHTML = html;
    el.tooltip.hidden = false;
  }
  function statusText(p) {
    if (!p.spot_id) return 'пустая позиция';
    if (p.status === 'repair') return 'на ремонте';
    if (p.status === 'occupied') return p.has_debt ? 'занято · есть долг' : 'занято';
    return 'свободно';
  }
  function moveTooltip(e) {
    el.tooltip.style.left = (e.evt.offsetX + 14) + 'px';
    el.tooltip.style.top = (e.evt.offsetY + 14) + 'px';
  }

  // ================================================================ выбор и floating-панель
  function select(group) {
    if (selected && selected !== group) resetStroke(selected);
    selected = group;
    stopResize();
    el.tooltip.hidden = true;
    if (group) setStroke(group, HIGHLIGHT, 2.5);
    renderFloat();
  }

  function renderFloat() {
    closeMenus();
    if (!selected) { el.float.hidden = true; return; }
    const meta = selected.getAttr('meta');
    el.floatTitle.textContent = 'Место ' + (meta.code || '—');

    let info = '';
    if (!editMode) {
      if (meta.tenant) info += row('Арендатор', meta.tenant);
      if (meta.building) info += row('Корпус', meta.building);
      info += row('Статус', statusText(meta));
    } else if (meta.tenant) {
      info = '<div class="map-float-sub">' + meta.tenant + '</div>';
    }
    el.floatInfo.innerHTML = info;

    el.floatActions.innerHTML = '';
    if (!editMode) {
      if (meta.tenant_id || meta.spot_id) {
        addAction('Открыть карточку', 'primary', function () {
          window.location = meta.tenant_id
            ? urlFor(cfg.urls.tenant, meta.tenant_id)
            : urlFor(cfg.urls.spotHistory, meta.spot_id);
        });
      }
    } else {
      if (meta.spot_id) addAction('⇄ Поменять местами', '', startSwap);
      addAction('•••', 'map-more', toggleMenu);
      buildMenu(meta);
    }
    el.float.hidden = false;
    placeFloat();
  }
  const row = (k, v) =>
    '<div class="map-float-row"><span>' + k + '</span><b>' + v + '</b></div>';

  function addAction(text, cls, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn small ' + (cls || '');
    button.textContent = text;
    button.addEventListener('click', function (e) { e.stopPropagation(); handler(); });
    el.floatActions.appendChild(button);
  }

  function buildMenu(meta) {
    el.floatMenu.innerHTML = '';
    const items = [];
    if (meta.tenant_id || meta.spot_id) {
      items.push(['Открыть карточку', function () {
        window.location = meta.tenant_id
          ? urlFor(cfg.urls.tenant, meta.tenant_id)
          : urlFor(cfg.urls.spotHistory, meta.spot_id);
      }]);
    }
    items.push(['Изменить размер', startResize]);
    items.push(['Убрать с карты', removeSelected, 'danger']);
    items.forEach(function ([text, handler, cls]) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'map-menu-item' + (cls ? ' ' + cls : '');
      item.textContent = text;
      item.addEventListener('click', function (e) {
        e.stopPropagation();
        el.floatMenu.hidden = true;
        handler();
      });
      el.floatMenu.appendChild(item);
    });
  }

  function toggleMenu() { el.floatMenu.hidden = !el.floatMenu.hidden; }
  function closeMenus() { el.floatMenu.hidden = true; el.legendPop.hidden = true; }

  function placeFloat() {
    if (!selected || el.float.hidden) return;
    const box = selected.getClientRect();
    const wrap = el.stageWrap.getBoundingClientRect();
    const width = el.float.offsetWidth || 200;
    let left = box.x + box.width / 2 - width / 2;
    let top = box.y - el.float.offsetHeight - 10;
    left = Math.max(8, Math.min(left, wrap.width - width - 8));
    if (top < 8) top = box.y + box.height + 10;
    el.float.style.left = left + 'px';
    el.float.style.top = top + 'px';
  }

  // ================================================================ resize по запросу
  function startResize() {
    if (!selected) return;
    resizing = true;
    transformer.nodes([selected]);
  }
  function stopResize() {
    if (!resizing) return;
    resizing = false;
    transformer.nodes([]);
  }

  async function onTransformEnd(group) {
    const rect = group.findOne('.body');
    const label = group.findOne('Text');
    const width = rect.width() * group.scaleX();
    const height = rect.height() * group.scaleY();
    group.scale({ x: 1, y: 1 });
    rect.size({ width: width, height: height });
    label.size({ width: width, height: height });
    saveGeometry(group);
  }

  // ================================================================ сохранение геометрии
  function geometryOf(group) {
    const rect = group.findOne('.body');
    return {
      x: Math.round(group.x()), y: Math.round(group.y()),
      width: Math.round(rect.width() * group.scaleX()),
      height: Math.round(rect.height() * group.scaleY()),
    };
  }

  async function saveGeometry(group) {
    const meta = group.getAttr('meta');
    const geometry = geometryOf(group);
    try {
      const updated = await api(urlFor(cfg.urls.update, meta.id), 'PATCH',
        Object.assign({ updated_at: meta.updated_at }, geometry));
      group.setAttr('meta', updated);
      group.position({ x: updated.x, y: updated.y });
      if (selected === group) { el.float.hidden = false; placeFloat(); }
    } catch (e) {
      if (e.status === 409) { await load(); return; }
      group.position({ x: meta.x, y: meta.y });
      const rect = group.findOne('.body');
      const label = group.findOne('Text');
      rect.size({ width: meta.width, height: meta.height });
      label.size({ width: meta.width, height: meta.height });
      if (selected === group) { el.float.hidden = false; placeFloat(); }
    }
  }

  // ================================================================ swap: click → click (основной) и drag (дополнительный)
  function startSwap() {
    if (!selected) return;
    swapSource = selected;
    el.float.hidden = true;
    el.swapHint.hidden = false;
    setStroke(swapSource, HIGHLIGHT, 3);
  }

  function cancelSwapSelect() {
    if (!swapSource) return;
    resetStroke(swapSource);
    if (selected === swapSource) setStroke(swapSource, HIGHLIGHT, 2.5);
    swapSource = null;
    el.swapHint.hidden = true;
  }

  function askSwap(sourceGroup, targetGroup, viaDrag) {
    const source = sourceGroup.getAttr('meta');
    const target = targetGroup.getAttr('meta');
    pendingSwap = { sourceGroup, targetGroup, viaDrag };
    el.confirmText.innerHTML = target.spot_id
      ? '<b>' + source.code + '</b> ↔ <b>' + target.code + '</b><br>Поменять местами?'
      : 'Перенести <b>' + source.code + '</b> в пустую позицию?';
    el.confirmYes.textContent = target.spot_id ? 'Поменять' : 'Перенести';
    el.confirmBox.hidden = false;
    el.swapHint.hidden = true;
    setStroke(targetGroup, HIGHLIGHT, 3);
  }

  function closeSwap(revert) {
    if (!pendingSwap) return;
    const { sourceGroup, targetGroup, viaDrag } = pendingSwap;
    resetStroke(targetGroup);
    resetStroke(sourceGroup);
    if (revert && viaDrag) {
      const meta = sourceGroup.getAttr('meta');
      sourceGroup.position({ x: meta.x, y: meta.y });
    }
    el.confirmBox.hidden = true;
    pendingSwap = null;
    swapSource = null;
    el.swapHint.hidden = true;
    if (selected) setStroke(selected, HIGHLIGHT, 2.5);
  }

  async function confirmSwap() {
    if (!pendingSwap) return;
    const { sourceGroup, targetGroup, viaDrag } = pendingSwap;
    const source = sourceGroup.getAttr('meta');
    const target = targetGroup.getAttr('meta');
    el.confirmBox.hidden = true;
    pendingSwap = null;
    swapSource = null;
    try {
      const result = await api(cfg.urls.transfer, 'POST',
        { source_id: source.id, target_id: target.id });
      select(null);
      refreshNode(result.source);
      refreshNode(result.target);
    } catch (e) {
      if (viaDrag) sourceGroup.position({ x: source.x, y: source.y });
      resetStroke(sourceGroup);
      resetStroke(targetGroup);
    }
  }

  // ================================================================ добавление через модалку
  function openAddModal() {
    renderAddList('');
    el.addSearch.value = '';
    el.addModal.hidden = false;
    setTimeout(() => el.addSearch.focus(), 40);
  }
  function closeAddModal() { el.addModal.hidden = true; }

  function renderAddList(query) {
    query = query.trim().toLowerCase();
    el.addList.innerHTML = '';
    const visible = unplaced.filter(
      s => !query || s.code.toLowerCase().includes(query));
    if (!visible.length) {
      el.addList.innerHTML = '<div class="map-item-none">' +
        (unplaced.length ? 'Ничего не найдено'
          : 'Все места уже на карте. Новые создаются на странице «Создать места».') +
        '</div>';
      return;
    }
    visible.forEach(function (s) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'map-add-item';
      item.innerHTML = '<b>' + s.code + '</b><span>' + s.building + '</span>';
      item.addEventListener('click', function () { addSpot(s); });
      el.addList.appendChild(item);
    });
  }

  async function addSpot(s) {
    closeAddModal();
    // Место появляется в центре текущей видимой области
    const scale = stage.scaleX();
    const width = 80, height = 50;
    const x = Math.max(0, Math.round(
      (stage.width() / 2 - stage.x()) / scale - width / 2));
    const y = Math.max(0, Math.round(
      (stage.height() / 2 - stage.y()) / scale - height / 2));
    try {
      const p = await api(cfg.urls.create, 'POST',
        { spot_id: s.id, x: x, y: y, width: width, height: height });
      unplaced = unplaced.filter(u => u.id !== s.id);
      const node = refreshNode(p);
      select(node);
    } catch (e) {}
  }

  async function removeSelected() {
    if (!selected) return;
    const meta = selected.getAttr('meta');
    if (!confirm('Убрать ' + (meta.code || 'позицию') +
        ' с карты? Само место и его история останутся в системе.')) return;
    try {
      await api(urlFor(cfg.urls.delete, meta.id), 'DELETE');
      selected.destroy();
      nodes.delete(meta.id);
      selected = null;
      el.float.hidden = true;
      if (meta.spot_id) {
        unplaced.push({ id: meta.spot_id, code: meta.code,
          building: meta.building || '', status: meta.status });
        unplaced.sort((a, b) => a.code.localeCompare(b.code, 'ru'));
      }
      updateEmptyHint();
    } catch (e) {}
  }

  // ================================================================ прочее
  function findOverlap(group) {
    const box = group.getClientRect();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    let found = null;
    nodes.forEach(function (other) {
      if (other === group || found) return;
      const b = other.getClientRect();
      if (cx >= b.x && cx <= b.x + b.width && cy >= b.y && cy <= b.y + b.height) found = other;
    });
    return found;
  }

  function setEditMode(on) {
    editMode = on;
    select(null);
    cancelSwapSelect();
    closeSwap(true);
    closeMenus();
    el.tooltip.hidden = true;
    nodes.forEach(n => n.draggable(on));
    el.modeBtn.textContent = on ? '✓ Готово' : 'Редактировать';
    el.modeBtn.classList.toggle('primary', on);
    el.addBtn.hidden = !on;
    updateEmptyHint();
  }

  function search(query) {
    query = query.trim().toLowerCase();
    if (!query) return;
    let found = null;
    nodes.forEach(function (group) {
      const meta = group.getAttr('meta');
      if (!found && meta.code && meta.code.toLowerCase().includes(query)) found = group;
    });
    if (!found) { setStatus('Место «' + query + '» на карте не найдено', true); return; }
    centerOn(found.getAttr('meta'), 1);
    select(found);
  }

  async function load() {
    const data = await (await fetch(cfg.urls.plan)).json();
    plan = data.plan;
    unplaced = data.unplaced;
    if (!stage) { buildStage(); fitAll(); }
    select(null);
    renderPositions(data.positions);
  }

  // ================================================================ события
  el.zoomIn.addEventListener('click', () => zoomAt(null, 1.25));
  el.zoomOut.addEventListener('click', () => zoomAt(null, 1 / 1.25));
  el.zoomLevel.addEventListener('click', function () {
    zoomAt(null, 1 / stage.scaleX());   // вернуть масштаб 100%
  });
  el.fit.addEventListener('click', function () { el.legendPop.hidden = true; fitAll(); });
  el.help.addEventListener('click', function (e) {
    e.stopPropagation();
    el.legendPop.hidden = !el.legendPop.hidden;
  });
  el.search.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); search(this.value); }
  });

  if (el.modeBtn) {
    el.modeBtn.addEventListener('click', () => setEditMode(!editMode));
    el.addBtn.addEventListener('click', openAddModal);
    el.addClose.addEventListener('click', closeAddModal);
    el.addModal.addEventListener('click', function (e) {
      if (e.target === el.addModal) closeAddModal();
    });
    el.addSearch.addEventListener('input', function () { renderAddList(this.value); });
    el.confirmYes.addEventListener('click', confirmSwap);
    el.confirmNo.addEventListener('click', () => closeSwap(true));
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (!el.addModal.hidden) { closeAddModal(); return; }
      if (!el.confirmBox.hidden) { closeSwap(true); return; }
      if (swapSource) { cancelSwapSelect(); return; }
      if (resizing) { stopResize(); return; }
      if (selected) { select(null); return; }
      if (!el.legendPop.hidden) el.legendPop.hidden = true;
    }
    if (editMode && (e.key === 'Delete' || e.key === 'Backspace') && selected &&
        !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      e.preventDefault();
      removeSelected();
    }
  });
  document.addEventListener('click', function (e) {
    if (!el.legendPop.hidden && !el.legendPop.contains(e.target) && e.target !== el.help)
      el.legendPop.hidden = true;
  });

  load().catch(() => setStatus('Не удалось загрузить карту', true));
})();
