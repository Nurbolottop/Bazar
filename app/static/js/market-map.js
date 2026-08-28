/* Интерактивная карта рынка (Konva.js) — редактор в стиле Figma.
 *
 * Просмотр: карта + поиск + zoom. Редактор: слева список мест (drag&drop
 * на карту), справа свойства выбранного объекта, минимальный тулбар.
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
    searchBox: byId('map-search-box'),
    zoomIn: byId('map-zoom-in'),
    zoomOut: byId('map-zoom-out'),
    zoomLevel: byId('map-zoom-level'),
    fit: byId('map-fit'),
    editToggle: byId('map-edit-toggle'),
    listLink: byId('map-list-link'),
    unplacedList: byId('map-unplaced-list'),
    unplacedCount: byId('map-unplaced-count'),
    unplacedBlock: byId('map-unplaced-block'),
    props: byId('map-props'),
    propsEmpty: byId('map-props-empty'),
    propsBody: byId('map-props-body'),
    propsSize: byId('map-props-size'),
    propsCode: byId('map-props-code'),
    propsInfo: byId('map-props-info'),
    propW: byId('map-prop-w'),
    propH: byId('map-prop-h'),
    propsOpen: byId('map-props-open'),
    propsRemove: byId('map-props-remove'),
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

  let stage, planLayer, spotsLayer, transformer;
  let plan = null;
  let editMode = false;
  let selected = null;
  let swapTarget = null;          // подсвеченная цель при перетаскивании
  let pendingSwap = null;         // {sourceGroup, targetGroup}
  let unplaced = [];
  const nodes = new Map();        // position.id -> Konva.Group

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
      draggable: true,
    });
    planLayer = new Konva.Layer({ listening: false });
    spotsLayer = new Konva.Layer();
    stage.add(planLayer);
    stage.add(spotsLayer);

    // Рабочая область: белый лист с точечной сеткой (только визуально, без snap)
    planLayer.add(new Konva.Rect({
      x: 0, y: 0, width: plan.width, height: plan.height,
      fill: '#ffffff', stroke: '#b9c6c0', strokeWidth: 2, cornerRadius: 6,
      shadowColor: 'rgba(16,32,26,.16)', shadowBlur: 18, shadowOffsetY: 4,
    }));
    const dots = new Konva.Shape({
      sceneFunc: function (ctx, shape) {
        const step = 50;
        ctx.beginPath();
        for (let x = step; x < plan.width; x += step)
          for (let y = step; y < plan.height; y += step) {
            ctx.moveTo(x, y);
            ctx.arc(x, y, 1.1, 0, Math.PI * 2);
          }
        ctx.fillStyle = '#d5ddd8';
        ctx.fill();
      },
      listening: false,
    });
    dots.cache({ x: 0, y: 0, width: plan.width, height: plan.height });
    planLayer.add(dots);

    transformer = new Konva.Transformer({
      rotateEnabled: false, flipEnabled: false, keepRatio: false,
      anchorSize: 9, anchorCornerRadius: 3,
      borderStroke: HIGHLIGHT, anchorStroke: HIGHLIGHT,
      boundBoxFunc: (oldBox, newBox) =>
        (newBox.width < 12 || newBox.height < 12) ? oldBox : newBox,
    });
    spotsLayer.add(transformer);

    stage.on('wheel', function (e) {
      e.evt.preventDefault();
      zoomAt(stage.getPointerPosition(), e.evt.deltaY > 0 ? 1 / 1.1 : 1.1);
    });
    stage.on('click tap', function (e) {
      if (e.target === stage || e.target.getLayer() === planLayer) select(null);
    });
    stage.on('dragmove', updateZoomLabel);
    window.addEventListener('resize', resizeStage);
  }

  function resizeStage() {
    if (!stage) return;
    stage.width(el.canvas.clientWidth);
    stage.height(el.canvas.clientHeight);
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
    updateZoomLabel();
  }

  function setZoom(scale, center) {
    stage.scale({ x: scale, y: scale });
    if (center) stage.position(center);
    updateZoomLabel();
  }

  function updateZoomLabel() {
    el.zoomLevel.textContent = Math.round(stage.scaleX() * 100) + '%';
  }

  function fitAll() {
    const padding = 60;
    const scale = Math.min(
      (stage.width() - padding) / plan.width,
      (stage.height() - padding) / plan.height, 2);
    setZoom(scale, {
      x: (stage.width() - plan.width * scale) / 2,
      y: (stage.height() - plan.height * scale) / 2,
    });
  }

  function centerOn(meta, minScale) {
    const scale = Math.max(stage.scaleX(), minScale || 1);
    setZoom(scale, {
      x: stage.width() / 2 - (meta.x + meta.width / 2) * scale,
      y: stage.height() / 2 - (meta.y + meta.height / 2) * scale,
    });
  }

  // ================================================================ узлы
  function makeNode(p) {
    const c = colorFor(p);
    const group = new Konva.Group({ x: p.x, y: p.y, draggable: editMode });
    const rect = new Konva.Rect({
      name: 'body', width: p.width, height: p.height,
      fill: c.fill, stroke: c.stroke, strokeWidth: 1.5, cornerRadius: 4,
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
      if (!editMode) showTooltip(p);
    });
    group.on('mousemove', moveTooltip);
    group.on('mouseleave', function () {
      stage.container().style.cursor = 'grab';
      el.tooltip.hidden = true;
    });

    group.on('click tap', function (e) {
      e.cancelBubble = true;
      // В обоих режимах клик выделяет место; переход — кнопкой «Открыть карточку»
      select(group);
      el.tooltip.hidden = true;
    });

    group.on('dragmove', function () {
      const target = findOverlap(group);
      if (target !== swapTarget) {
        clearSwapHighlight();
        swapTarget = target;
        if (target) target.findOne('.body').stroke(HIGHLIGHT).strokeWidth(3);
      }
    });
    group.on('dragend', function () { onDragEnd(group); });
    group.on('transformend', function () { onTransformEnd(group); });
    return group;
  }

  function clearSwapHighlight() {
    if (swapTarget) {
      const meta = swapTarget.getAttr('meta');
      swapTarget.findOne('.body').stroke(colorFor(meta).stroke).strokeWidth(1.5);
      swapTarget = null;
    }
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
    el.emptyText.textContent = empty
      ? (editMode ? 'Перетащите торговое место из панели слева на карту'
                  : 'На карте пока пусто — нажмите «Редактировать», чтобы расставить места')
      : '';
  }

  // ================================================================ tooltip
  function showTooltip(p) {
    let html = '<b>Место ' + (p.code || '—') + '</b>';
    if (p.building) html += '<br>Корпус: ' + p.building;
    if (p.tenant) html += '<br>Арендатор: ' + p.tenant;
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

  // ================================================================ выбор и панель свойств
  function select(group) {
    selected = group;
    transformer.nodes(group && editMode ? [group] : []);
    renderProps();
  }

  function renderProps() {
    // Панель существует только в режиме редактирования: в просмотре она
    // полностью убрана из layout (hidden -> display:none), карта шире
    el.props.hidden = !editMode;
    if (!editMode) return;
    if (!selected) {
      el.propsEmpty.hidden = false;
      el.propsBody.hidden = true;
      el.unplacedBlock.hidden = !editMode;   // размещение мест — только в редакторе
      if (editMode) renderUnplaced();
      return;
    }
    el.unplacedBlock.hidden = true;
    el.propsEmpty.hidden = true;
    el.propsBody.hidden = false;
    el.propsSize.hidden = !editMode;         // Ширина/Высота — только в редакторе
    el.propsRemove.hidden = !editMode;       // «Убрать с карты» — только в редакторе
    const meta = selected.getAttr('meta');
    el.propsCode.textContent = 'Место ' + (meta.code || '—');
    let rows = '';
    rows += propRow('Статус', statusText(meta));
    if (meta.building) rows += propRow('Корпус', meta.building);
    if (meta.tenant) rows += propRow('Арендатор', meta.tenant);
    el.propsInfo.innerHTML = rows;
    el.propW.value = Math.round(meta.width);
    el.propH.value = Math.round(meta.height);
    if (meta.tenant_id) {
      el.propsOpen.href = urlFor(cfg.urls.tenant, meta.tenant_id);
      el.propsOpen.hidden = false;
    } else if (meta.spot_id) {
      el.propsOpen.href = urlFor(cfg.urls.spotHistory, meta.spot_id);
      el.propsOpen.hidden = false;
    } else el.propsOpen.hidden = true;
  }
  const propRow = (k, v) => '<div class="map-prop-row"><span>' + k + '</span><b>' + v + '</b></div>';

  function applySizeFromProps() {
    if (!selected) return;
    const width = parseInt(el.propW.value, 10);
    const height = parseInt(el.propH.value, 10);
    if (!width || !height) return;
    const rect = selected.findOne('.body');
    const label = selected.findOne('Text');
    rect.size({ width: width, height: height });
    label.size({ width: width, height: height });
    saveGeometry(selected);
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
      renderProps();
    } catch (e) {
      if (e.status === 409) { await load(); return; }
      group.position({ x: meta.x, y: meta.y });
      const rect = group.findOne('.body');
      const label = group.findOne('Text');
      rect.size({ width: meta.width, height: meta.height });
      label.size({ width: meta.width, height: meta.height });
      renderProps();
    }
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

  // ================================================================ перемещение и swap
  function onDragEnd(group) {
    const meta = group.getAttr('meta');
    const target = swapTarget;
    clearSwapHighlight();
    if (target && meta.spot_id) {
      askSwap(group, target);
      return;
    }
    saveGeometry(group);
  }

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

  function askSwap(sourceGroup, targetGroup) {
    const source = sourceGroup.getAttr('meta');
    const target = targetGroup.getAttr('meta');
    pendingSwap = { sourceGroup: sourceGroup, targetGroup: targetGroup };
    el.confirmText.textContent = target.spot_id
      ? 'Поменять местами ' + source.code + ' и ' + target.code + '?'
      : 'Перенести ' + source.code + ' в пустую позицию?';
    el.confirmYes.textContent = target.spot_id ? 'Поменять' : 'Перенести';
    el.confirmBox.hidden = false;
    targetGroup.findOne('.body').stroke(HIGHLIGHT).strokeWidth(3);
  }

  function closeSwap(revert) {
    if (!pendingSwap) return;
    const { sourceGroup, targetGroup } = pendingSwap;
    const targetMeta = targetGroup.getAttr('meta');
    targetGroup.findOne('.body').stroke(colorFor(targetMeta).stroke).strokeWidth(1.5);
    if (revert) {
      const meta = sourceGroup.getAttr('meta');
      sourceGroup.position({ x: meta.x, y: meta.y });
    }
    el.confirmBox.hidden = true;
    pendingSwap = null;
  }

  async function confirmSwap() {
    if (!pendingSwap) return;
    const { sourceGroup, targetGroup } = pendingSwap;
    const source = sourceGroup.getAttr('meta');
    const target = targetGroup.getAttr('meta');
    closeSwap(false);
    try {
      const result = await api(cfg.urls.transfer, 'POST',
        { source_id: source.id, target_id: target.id });
      refreshNode(result.source);
      refreshNode(result.target);
      select(null);
      renderUnplaced();
    } catch (e) {
      sourceGroup.position({ x: source.x, y: source.y });
    }
  }

  // ================================================================ список неразмещённых и drag&drop добавления
  function renderUnplaced() {
    if (!cfg.canEdit) return;
    el.unplacedList.innerHTML = '';
    unplaced.forEach(function (s) {
      const item = document.createElement('div');
      item.className = 'map-item map-item--drag';
      item.draggable = true;
      item.innerHTML = '<span class="map-item-grip">⋮⋮</span><b>' + s.code +
        '</b><span class="map-item-sub">' + s.building + '</span>';
      item.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', String(s.id));
        e.dataTransfer.effectAllowed = 'copy';
        el.stageWrap.classList.add('map-drop-ready');
      });
      item.addEventListener('dragend',
        () => el.stageWrap.classList.remove('map-drop-ready'));
      el.unplacedList.appendChild(item);
    });
    if (!unplaced.length) {
      el.unplacedList.innerHTML = '<div class="map-item-none">Все места размещены</div>';
    }
    el.unplacedCount.textContent = unplaced.length;
  }


  function bindDrop() {
    el.canvas.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });
    el.canvas.addEventListener('drop', async function (e) {
      e.preventDefault();
      el.stageWrap.classList.remove('map-drop-ready');
      const spotId = parseInt(e.dataTransfer.getData('text/plain'), 10);
      if (!spotId || !editMode) return;
      stage.setPointersPositions(e);
      const pointer = stage.getPointerPosition();
      const scale = stage.scaleX();
      const x = Math.max(0, Math.round((pointer.x - stage.x()) / scale - 40));
      const y = Math.max(0, Math.round((pointer.y - stage.y()) / scale - 25));
      try {
        const p = await api(cfg.urls.create, 'POST',
          { spot_id: spotId, x: x, y: y, width: 80, height: 50 });
        const node = refreshNode(p);
        unplaced = unplaced.filter(s => s.id !== spotId);
        select(node);
        renderUnplaced();
      } catch (e2) {}
    });
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
      select(null);
      if (meta.spot_id) unplaced.push({
        id: meta.spot_id, code: meta.code, building: meta.building || '', status: meta.status,
      });
      unplaced.sort((a, b) => a.code.localeCompare(b.code, 'ru'));
      renderUnplaced();
      updateEmptyHint();
    } catch (e) {}
  }

  // ================================================================ режимы
  function setEditMode(on) {
    editMode = on;
    select(null);
    closeSwap(true);
    el.tooltip.hidden = true;
    nodes.forEach(n => n.draggable(on));
    document.getElementById('map-app').classList.toggle('is-editing', on);
    // Одна кнопка на одном месте: «Редактировать» ↔ «Сохранить».
    // Изменения сохраняются сразу через API; «Сохранить» завершает режим.
    el.editToggle.textContent = on ? 'Сохранить' : 'Редактировать';
    el.editToggle.classList.toggle('primary', on);
    updateEmptyHint();
    setTimeout(resizeStage, 30);
  }

  // ================================================================ поиск (просмотр)
  function search(query) {
    query = query.trim().toLowerCase();
    if (!query) return;
    let found = null;
    nodes.forEach(function (group) {
      const meta = group.getAttr('meta');
      if (!found && meta.code && meta.code.toLowerCase().includes(query)) found = group;
    });
    if (!found) { setStatus('Место «' + query + '» на карте не найдено', true); return; }
    const meta = found.getAttr('meta');
    centerOn(meta, 1);
    const rect = found.findOne('.body');
    const base = colorFor(meta).stroke;
    let flashes = 0;
    const timer = setInterval(function () {
      const on = flashes % 2 === 0;
      rect.stroke(on ? HIGHLIGHT : base).strokeWidth(on ? 4 : 1.5);
      if (++flashes > 7) { clearInterval(timer); rect.stroke(base).strokeWidth(1.5); }
    }, 200);
  }

  // ================================================================ загрузка
  async function load() {
    const data = await (await fetch(cfg.urls.plan)).json();
    plan = data.plan;
    unplaced = data.unplaced;
    if (!stage) { buildStage(); fitAll(); }
    renderPositions(data.positions);
    if (editMode) renderUnplaced();
  }

  // ================================================================ события
  el.zoomIn.addEventListener('click', () => zoomAt(null, 1.25));
  el.zoomOut.addEventListener('click', () => zoomAt(null, 1 / 1.25));
  el.zoomLevel.addEventListener('click', () => setZoom(1, stage.position()));
  el.fit.addEventListener('click', fitAll);
  el.search.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); search(this.value); }
  });
  el.search.addEventListener('change', function () { search(this.value); });
  if (el.editToggle) {
    el.editToggle.addEventListener('click', () => setEditMode(!editMode));
    el.propsRemove.addEventListener('click', removeSelected);
    el.propW.addEventListener('change', applySizeFromProps);
    el.propH.addEventListener('change', applySizeFromProps);
    el.confirmYes.addEventListener('click', confirmSwap);
    el.confirmNo.addEventListener('click', () => closeSwap(true));
    bindDrop();
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (!el.confirmBox.hidden) { closeSwap(true); return; }
        if (editMode) select(null);
      }
      if (editMode && (e.key === 'Delete' || e.key === 'Backspace') && selected &&
          !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
        e.preventDefault();
        removeSelected();
      }
    });
  }

  load().catch(() => setStatus('Не удалось загрузить карту', true));
})();
