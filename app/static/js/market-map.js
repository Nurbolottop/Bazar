/* Интерактивная карта рынка (Konva.js).
 *
 * Версия 1: просмотр (zoom/pan/hover/click/поиск) и редактор
 * (разместить место, перетащить, изменить размер, swap двух мест, убрать с карты).
 * Все изменения сохраняются на backend; состояние в браузере не хранится.
 */
(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const cfg = JSON.parse(byId('map-config').textContent);
  const container = byId('map-canvas');
  const tooltip = byId('map-tooltip');
  const statusEl = byId('map-save-status');
  const unplacedSelect = byId('map-unplaced');
  const editToggle = byId('map-edit-toggle');
  const placeBtn = byId('map-place-btn');
  const deleteBtn = byId('map-delete-btn');

  const COLORS = {
    free:     { fill: '#ffffff', stroke: '#5ea98c', text: '#0b6e4f' },
    occupied: { fill: '#0b6e4f', stroke: '#095c42', text: '#ffffff' },
    debt:     { fill: '#b42318', stroke: '#8e1b12', text: '#ffffff' },
    repair:   { fill: '#cfd6d2', stroke: '#aab4ae', text: '#5c6b64' },
    empty:    { fill: '#f4f6f5', stroke: '#c3ccc7', text: '#8a958f' },
  };

  let stage, planLayer, spotsLayer, transformer;
  let plan = null;
  let editMode = false;
  let placing = false;
  let selected = null;
  const nodes = new Map();   // position.id -> Konva.Group

  // ---------------------------------------------------------------- сеть
  function setStatus(text, isError) {
    statusEl.textContent = text || '';
    statusEl.style.color = isError ? 'var(--danger)' : 'var(--muted)';
    if (text && !isError) setTimeout(() => { if (statusEl.textContent === text) statusEl.textContent = ''; }, 2500);
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

  // ---------------------------------------------------------------- сцена
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
      width: container.clientWidth,
      height: container.clientHeight,
      draggable: true,
    });
    planLayer = new Konva.Layer({ listening: false });
    spotsLayer = new Konva.Layer();
    stage.add(planLayer);
    stage.add(spotsLayer);

    planLayer.add(new Konva.Rect({
      x: 0, y: 0, width: plan.width, height: plan.height,
      fill: '#fbfdfc', stroke: '#c9d4ce', strokeWidth: 2, cornerRadius: 8,
    }));

    transformer = new Konva.Transformer({
      rotateEnabled: false,
      flipEnabled: false,
      keepRatio: false,
      anchorSize: 9,
      anchorCornerRadius: 3,
      borderStroke: '#1d4fa1',
      anchorStroke: '#1d4fa1',
      boundBoxFunc: function (oldBox, newBox) {
        if (newBox.width < 12 || newBox.height < 12) return oldBox;
        return newBox;
      },
    });
    spotsLayer.add(transformer);

    // Zoom к курсору
    stage.on('wheel', function (e) {
      e.evt.preventDefault();
      const direction = e.evt.deltaY > 0 ? -1 : 1;
      zoomAt(stage.getPointerPosition(), direction > 0 ? 1.1 : 1 / 1.1);
    });

    stage.on('click tap', function (e) {
      if (placing && e.target.getParent() !== transformer) { placeAt(); return; }
      if (e.target === stage || e.target.getLayer() === planLayer) select(null);
    });

    window.addEventListener('resize', function () {
      stage.width(container.clientWidth);
      stage.height(container.clientHeight);
    });
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
  }

  function fitAll() {
    const padding = 40;
    const scale = Math.min(
      (stage.width() - padding) / plan.width,
      (stage.height() - padding) / plan.height, 2);
    stage.scale({ x: scale, y: scale });
    stage.position({
      x: (stage.width() - plan.width * scale) / 2,
      y: (stage.height() - plan.height * scale) / 2,
    });
  }

  // ---------------------------------------------------------------- узлы
  function makeNode(p) {
    const c = colorFor(p);
    const group = new Konva.Group({ x: p.x, y: p.y, draggable: editMode });
    const rect = new Konva.Rect({
      width: p.width, height: p.height, fill: c.fill, stroke: c.stroke,
      strokeWidth: 1.5, cornerRadius: 4,
      shadowColor: 'rgba(16,32,26,.35)', shadowBlur: 0, shadowOpacity: 0,
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
      showTooltip(p);
    });
    group.on('mousemove', moveTooltip);
    group.on('mouseleave', function () {
      stage.container().style.cursor = placing ? 'crosshair' : 'grab';
      tooltip.hidden = true;
    });

    group.on('click tap', function (e) {
      e.cancelBubble = true;
      const meta = group.getAttr('meta');
      if (editMode) { select(group); return; }
      if (meta.tenant_id) window.location = cfg.urls.tenant.replace('/0/', '/' + meta.tenant_id + '/');
      else if (meta.spot_id) window.location = cfg.urls.spotHistory.replace('/0/', '/' + meta.spot_id + '/');
    });

    group.on('dragend', function () { onDragEnd(group); });
    group.on('transformend', function () { onTransformEnd(group); });
    return group;
  }

  function renderPositions(positions) {
    nodes.forEach(n => n.destroy());
    nodes.clear();
    positions.forEach(function (p) {
      const node = makeNode(p);
      nodes.set(p.id, node);
      spotsLayer.add(node);
    });
    transformer.moveToTop();
  }

  function refreshNode(p) {
    const old = nodes.get(p.id);
    if (old) old.destroy();
    const node = makeNode(p);
    nodes.set(p.id, node);
    spotsLayer.add(node);
    transformer.moveToTop();
  }

  // ---------------------------------------------------------------- tooltip
  function showTooltip(p) {
    if (editMode) return;
    let html = '<b>Место ' + (p.code || '—') + '</b>';
    if (p.building) html += '<br>Корпус: ' + p.building;
    if (p.tenant) html += '<br>Арендатор: ' + p.tenant;
    html += '<br>Статус: ' + statusText(p);
    tooltip.innerHTML = html;
    tooltip.hidden = false;
  }
  function statusText(p) {
    if (!p.spot_id) return 'пустая позиция';
    if (p.status === 'repair') return 'на ремонте';
    if (p.status === 'occupied') return p.has_debt ? 'занято · есть долг' : 'занято';
    return 'свободно';
  }
  function moveTooltip(e) {
    tooltip.style.left = (e.evt.offsetX + 14) + 'px';
    tooltip.style.top = (e.evt.offsetY + 14) + 'px';
  }

  // ---------------------------------------------------------------- редактор
  function select(group) {
    selected = group;
    transformer.nodes(group ? [group] : []);
    if (deleteBtn) deleteBtn.hidden = !group;
  }

  function setEditMode(on) {
    editMode = on;
    select(null);
    tooltip.hidden = true;
    nodes.forEach(n => n.draggable(on));
    document.getElementById('map-edit-tools').hidden = !on;
    editToggle.textContent = on ? 'Готово' : 'Редактировать';
    editToggle.classList.toggle('primary', on);
    if (!on) stopPlacing();
  }

  function geometryOf(group) {
    const rect = group.findOne('Rect');
    return {
      x: Math.round(group.x()), y: Math.round(group.y()),
      width: Math.round(rect.width() * group.scaleX()),
      height: Math.round(rect.height() * group.scaleY()),
    };
  }

  async function onDragEnd(group) {
    const meta = group.getAttr('meta');
    const target = findOverlap(group);
    if (target && meta.spot_id) {
      const targetMeta = target.getAttr('meta');
      const question = targetMeta.spot_id
        ? 'Поменять местами ' + meta.code + ' и ' + targetMeta.code + '?'
        : 'Перенести ' + meta.code + ' в пустую позицию?';
      if (confirm(question)) {
        try {
          const result = await api(cfg.urls.transfer, 'POST',
            { source_id: meta.id, target_id: targetMeta.id });
          refreshNode(result.source);
          refreshNode(result.target);
          select(null);
          return;
        } catch (e) { /* откат ниже */ }
      }
      group.position({ x: meta.x, y: meta.y });
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

  async function onTransformEnd(group) {
    const rect = group.findOne('Rect');
    const label = group.findOne('Text');
    const width = rect.width() * group.scaleX();
    const height = rect.height() * group.scaleY();
    group.scale({ x: 1, y: 1 });
    rect.size({ width: width, height: height });
    label.size({ width: width, height: height });
    saveGeometry(group);
  }

  async function saveGeometry(group) {
    const meta = group.getAttr('meta');
    const geometry = geometryOf(group);
    try {
      const updated = await api(
        cfg.urls.update.replace('/0/', '/' + meta.id + '/'), 'PATCH',
        Object.assign({ updated_at: meta.updated_at }, geometry));
      group.setAttr('meta', updated);
      group.position({ x: updated.x, y: updated.y });
    } catch (e) {
      if (e.status === 409) { await load(); return; }
      group.position({ x: meta.x, y: meta.y });   // откат
      const rect = group.findOne('Rect');
      const label = group.findOne('Text');
      rect.size({ width: meta.width, height: meta.height });
      label.size({ width: meta.width, height: meta.height });
    }
  }

  // Размещение нового места: выбрал в списке → клик по холсту
  function startPlacing() {
    if (!unplacedSelect.value) { alert('Выберите место из списка.'); return; }
    placing = true;
    stage.container().style.cursor = 'crosshair';
    placeBtn.textContent = 'Кликните по карте…';
  }
  function stopPlacing() {
    placing = false;
    stage.container().style.cursor = 'grab';
    if (placeBtn) placeBtn.textContent = 'Разместить на карте';
  }
  async function placeAt() {
    const pointer = stage.getPointerPosition();
    const scale = stage.scaleX();
    const x = Math.max(0, Math.round((pointer.x - stage.x()) / scale - 40));
    const y = Math.max(0, Math.round((pointer.y - stage.y()) / scale - 25));
    const spotId = parseInt(unplacedSelect.value, 10);
    stopPlacing();
    try {
      const p = await api(cfg.urls.create, 'POST',
        { spot_id: spotId, x: x, y: y, width: 80, height: 50 });
      refreshNode(p);
      unplacedSelect.querySelector('option[value="' + spotId + '"]').remove();
      const group = nodes.get(p.id);
      select(group);
    } catch (e) {}
  }

  async function deleteSelected() {
    if (!selected) return;
    const meta = selected.getAttr('meta');
    if (!confirm('Убрать ' + (meta.code || 'позицию') + ' с карты? Само место и его история останутся в системе.')) return;
    try {
      await api(cfg.urls.delete.replace('/0/', '/' + meta.id + '/'), 'DELETE');
      selected.destroy();
      nodes.delete(meta.id);
      select(null);
      if (meta.spot_id) {
        const option = document.createElement('option');
        option.value = meta.spot_id;
        option.textContent = meta.code + ' (' + (meta.building || '') + ')';
        unplacedSelect.appendChild(option);
      }
    } catch (e) {}
  }

  // ---------------------------------------------------------------- поиск
  function search(query) {
    query = query.trim().toLowerCase();
    if (!query) return;
    let found = null;
    nodes.forEach(function (group) {
      const meta = group.getAttr('meta');
      if (!found && meta.code && meta.code.toLowerCase().indexOf(query) !== -1) found = group;
    });
    if (!found) { setStatus('Место «' + query + '» на карте не найдено', true); return; }
    const meta = found.getAttr('meta');
    const scale = Math.max(stage.scaleX(), 1);
    stage.scale({ x: scale, y: scale });
    stage.position({
      x: stage.width() / 2 - (meta.x + meta.width / 2) * scale,
      y: stage.height() / 2 - (meta.y + meta.height / 2) * scale,
    });
    const rect = found.findOne('Rect');
    const original = rect.strokeWidth();
    let flashes = 0;
    const timer = setInterval(function () {
      rect.strokeWidth(rect.strokeWidth() === original ? 5 : original);
      rect.stroke(rect.strokeWidth() === original ? colorFor(meta).stroke : '#1d4fa1');
      if (++flashes > 7) { clearInterval(timer); rect.strokeWidth(original); rect.stroke(colorFor(meta).stroke); }
    }, 220);
  }

  // ---------------------------------------------------------------- загрузка
  async function load() {
    const data = await (await fetch(cfg.urls.plan)).json();
    plan = data.plan;
    if (!stage) { buildStage(); fitAll(); }
    renderPositions(data.positions);
    unplacedSelect.innerHTML = '<option value="">— выберите место —</option>' +
      data.unplaced.map(s => '<option value="' + s.id + '">' + s.code + ' (' + s.building + ')</option>').join('');
    document.getElementById('map-counts').textContent =
      'на карте: ' + data.positions.length + ' · не размещено: ' + data.unplaced.length;
  }

  // ---------------------------------------------------------------- события
  document.getElementById('map-zoom-in').addEventListener('click', () => zoomAt(null, 1.25));
  document.getElementById('map-zoom-out').addEventListener('click', () => zoomAt(null, 1 / 1.25));
  document.getElementById('map-fit').addEventListener('click', fitAll);
  document.getElementById('map-search-btn').addEventListener('click',
    () => search(document.getElementById('map-search').value));
  document.getElementById('map-search').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); search(this.value); }
  });
  if (editToggle) editToggle.addEventListener('click', () => setEditMode(!editMode));
  if (placeBtn) placeBtn.addEventListener('click', startPlacing);
  if (deleteBtn) deleteBtn.addEventListener('click', deleteSelected);
  document.addEventListener('keydown', function (e) {
    if (editMode && (e.key === 'Delete' || e.key === 'Backspace') &&
        selected && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      deleteSelected();
    }
    if (e.key === 'Escape' && placing) stopPlacing();
  });

  load().catch(() => setStatus('Не удалось загрузить карту', true));
})();
