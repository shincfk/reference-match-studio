const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const controlIds = ['mix', 'shadows', 'midtones', 'highlights', 'hue-rotate', 'chroma', 'highlight-protect', 'warm-protect'];
const MIN_VIEWER_ZOOM = .25;
const MAX_VIEWER_ZOOM = 8;
const defaults = { mix: .75, shadows: 1, midtones: 1, highlights: 1, 'hue-rotate': 8, chroma: .85, 'highlight-protect': .5, 'warm-protect': .25 };
const profileControlMap = {
  mix: 'mix', shadows: 'shadows', midtones: 'midtones', highlights: 'highlights',
  'hue-rotate': 'hueRotateDegrees', chroma: 'chromaScale',
  'highlight-protect': 'highlightProtect', 'warm-protect': 'warmToneProtect',
};

const state = {
  source: null,
  reference: null,
  analyses: { source: null, reference: null, result: null },
  preview: null,
  profileId: null,
  profile: null,
  dirty: true,
  view: 'result',
  scope: 'waveform',
  scopeTarget: 'result',
  splitPosition: 50,
  splitOrientation: 'vertical',
  splitSwapped: false,
  splitDragging: false,
  viewerZoom: 1,
  viewerPanX: 0,
  viewerPanY: 0,
  viewerPanning: false,
  viewerPanPointer: null,
  spacePan: false,
  bypassing: false,
  previewTimer: null,
  previewSequence: 0,
  toastTimer: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '本地处理失败。');
  return result;
}

function showToast(message, error = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 6500 : 3600);
}

function setStatus(message) {
  $('#status').textContent = message;
}

function profileName() {
  return state.profile?.profile?.name || $('#profile-name').value.trim() || 'Untitled Match';
}

function setDirty(dirty) {
  state.dirty = dirty;
  const title = $('#profile-title');
  title.classList.toggle('saved', !dirty);
  title.querySelector('b').textContent = profileName();
  title.querySelector('span').textContent = dirty ? '• 未保存' : '• 已保存';
  updateActions();
}

function canPreview() {
  return Boolean(state.source && (state.reference || state.profileId));
}

function canSave() {
  return Boolean(state.profileId || (state.source && state.reference));
}

function updateActions() {
  $('#preview').disabled = !canPreview();
  $('#save-profile').disabled = !canSave();
  $('#install-profile').disabled = !(state.profileId && !state.dirty);
  $('#install-profile').title = state.profileId && !state.dirty ? '激活当前配置' : '请先保存配置';
  updateViewButtons();
}

function payload(extra = {}) {
  const values = Object.fromEntries(controlIds.map(id => [id.replaceAll('-', '_'), Number($(`#${id}`).value)]));
  return {
    ...values,
    inputEncoding: $('#input-encoding').value,
    sourceId: state.source?.id,
    referenceId: state.reference?.id,
    profileId: state.profileId,
    profileName: profileName(),
    tags: state.profile?.profile?.tags || [],
    ...extra,
  };
}

function formatControl(id, value) {
  if (id === 'hue-rotate') return Number(value).toFixed(1);
  return Number(value).toFixed(2);
}

function syncControl(id, value, markDirty = true) {
  const range = $(`#${id}`);
  const number = $(`#${id}-value`);
  const minimum = Number(range.min);
  const maximum = Number(range.max);
  const normalized = Math.max(minimum, Math.min(maximum, Number(value)));
  range.value = String(normalized);
  number.value = formatControl(id, normalized);
  if (markDirty) {
    setDirty(true);
    schedulePreview();
  }
}

function applyProfile(profile) {
  state.profile = profile;
  $('#profile-name').value = profile.profile.name;
  $('#profile-tags').value = (profile.profile.tags || []).join(', ');
  Object.entries(profileControlMap).forEach(([id, key]) => syncControl(id, profile.controls[key], false));
  $('#input-encoding').value = profile.colorPipeline.inputEncoding === 'linear-rec709' ? 'linear' : 'srgb';
  updateContract();
  setDirty(false);
}

function updateContract() {
  const linear = $('#input-encoding').value === 'linear';
  $('#contract-summary').textContent = linear ? 'Linear Rec.709 → OKLab' : 'Display sRGB → OKLab';
}

function resetPreview() {
  state.preview = null;
  state.analyses.result = null;
  $('#viewer-result').removeAttribute('src');
  $('#viewer-difference').removeAttribute('src');
  $('#split-result').removeAttribute('src');
  resetViewerZoom();
  renderViewer();
  drawScopes();
}

async function upload(kind, file) {
  if (!file) return;
  setStatus(`正在导入${kind === 'reference' ? '参考图' : '源静帧'}…`);
  const form = new FormData();
  form.append('file', file, file.name);
  try {
    const data = await api(`/api/upload/${kind}`, { method: 'POST', body: form });
    state[kind] = data;
    if (kind === 'reference') {
      state.profileId = null;
      state.profile = null;
      $('#profile-name').value = 'Untitled Match';
      $('#profile-tags').value = '';
    }
    const card = $(`#${kind}-drop`);
    card.classList.add('ready');
    const image = $(`#${kind}-image`);
    image.src = data.url;
    image.hidden = false;
    $(`#${kind}-empty`).hidden = true;
    $(`#${kind}-name`).textContent = data.name;
    $(`#${kind}-meta`).textContent = `${data.width} × ${data.height} · ${$('#input-encoding').value === 'linear' ? 'Linear Rec.709' : 'sRGB Display'}`;
    $(`#${kind}-state`).textContent = '已导入';
    $(`#${kind}-analysis-state`).textContent = '正在分析';
    const report = await api('/api/analyse', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ id: data.id }),
    });
    state.analyses[kind] = report;
    $(`#${kind}-analysis-state`).textContent = '分析就绪';
    if (kind === 'source') $('#viewer-source').src = data.url;
    if (kind === 'reference') $('#viewer-reference').src = data.url;
    resetPreview();
    setDirty(kind === 'source' && state.profileId ? state.dirty : true);
    updateActions();
    renderViewer();
    drawScopes();
    if (canPreview()) {
      setStatus('素材已就绪，正在生成同链路预览。');
      await makePreview();
    } else {
      setStatus(state.profileId ? '源静帧已就绪，可生成 Profile 预览。' : '继续导入另一张图片。');
    }
  } catch (error) {
    setStatus(error.message);
    showToast(error.message, true);
  }
}

function schedulePreview() {
  if (!state.preview || !canPreview()) return;
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(makePreview, 240);
}

async function makePreview() {
  if (!canPreview()) return;
  const sequence = ++state.previewSequence;
  $('#viewer-loading').hidden = false;
  $('#preview').disabled = true;
  $('#analysis-state').textContent = '正在分析';
  setStatus('正在以 ReferenceMatch.dctl 同链路生成预览…');
  try {
    const result = await api('/api/preview', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload()),
    });
    if (sequence !== state.previewSequence) return;
    state.preview = result;
    state.analyses.result = result.analysis;
    $('#viewer-result').src = result.url;
    $('#viewer-difference').src = result.differenceUrl;
    $('#split-result').src = result.url;
    $('#analysis-state').textContent = '预览已更新';
    $('#viewer-status').textContent = 'Result · 同链路预览';
    setStatus('预览已更新；可使用 Split、Difference 与 Scopes 复核。');
    renderViewer();
    drawScopes();
  } catch (error) {
    if (sequence === state.previewSequence) {
      $('#analysis-state').textContent = '预览失败';
      setStatus(error.message);
      showToast(error.message, true);
    }
  } finally {
    if (sequence === state.previewSequence) {
      $('#viewer-loading').hidden = true;
      updateActions();
    }
  }
}

function updateViewButtons() {
  const availability = {
    source: Boolean(state.source),
    reference: Boolean(state.reference),
    result: Boolean(state.preview),
    split: Boolean(state.preview && state.source),
    difference: Boolean(state.preview),
  };
  $$('[data-view]').forEach(button => {
    button.disabled = !availability[button.dataset.view];
    button.classList.toggle('active', button.dataset.view === state.view);
  });
}

function chooseFallbackView() {
  if (state.preview) return state.view;
  if (state.source) return 'source';
  if (state.reference) return 'reference';
  return state.view;
}

function splitSources() {
  const source = state.source?.url || '';
  const result = state.preview?.url || '';
  if (state.splitSwapped) return { base: result, result: source, left: 'RESULT', right: 'SOURCE' };
  return { base: source, result, left: 'SOURCE', right: 'RESULT' };
}

function updateSplitClip() {
  const wrapper = $('#split-result-wrap');
  const divider = wrapper.querySelector('i');
  const position = state.splitPosition;
  if (state.splitOrientation === 'horizontal') {
    wrapper.style.clipPath = `inset(${position}% 0 0 0)`;
    divider.style.top = `${position}%`;
    divider.style.left = '0';
  } else {
    wrapper.style.clipPath = `inset(0 0 0 ${position}%)`;
    divider.style.left = `${position}%`;
    divider.style.top = '0';
  }
}

function activeViewerImage() {
  if (state.bypassing && state.source) return $('#viewer-source');
  const view = chooseFallbackView();
  if (view === 'source') return $('#viewer-source');
  if (view === 'reference') return $('#viewer-reference');
  if (view === 'result') return $('#viewer-result');
  if (view === 'difference') return $('#viewer-difference');
  if (view === 'split') return $('#split-base');
  return null;
}

function viewerHasMedia() {
  const image = activeViewerImage();
  return Boolean(image && image.getAttribute('src'));
}

function actualSizeZoom() {
  const image = activeViewerImage();
  const stage = $('#viewer-stage');
  if (!image?.naturalWidth || !image?.naturalHeight) return 1;
  const bounds = stage.getBoundingClientRect();
  const fitScale = Math.min(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
  return Number.isFinite(fitScale) && fitScale > 0 ? 1 / fitScale : 1;
}

function viewerMediaBox() {
  const image = activeViewerImage();
  const bounds = $('#viewer-stage').getBoundingClientRect();
  if (!image?.naturalWidth || !image?.naturalHeight || !bounds.width || !bounds.height) {
    return { width: bounds.width, height: bounds.height };
  }
  const fitScale = Math.min(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
  return { width: image.naturalWidth * fitScale, height: image.naturalHeight * fitScale };
}

function clampViewerPan() {
  const stage = $('#viewer-stage').getBoundingClientRect();
  const media = viewerMediaBox();
  const maxX = Math.max(0, (media.width * state.viewerZoom - stage.width) / 2);
  const maxY = Math.max(0, (media.height * state.viewerZoom - stage.height) / 2);
  state.viewerPanX = Math.max(-maxX, Math.min(maxX, state.viewerPanX));
  state.viewerPanY = Math.max(-maxY, Math.min(maxY, state.viewerPanY));
}

function updateViewerZoomUI() {
  const stage = $('#viewer-stage');
  const hasMedia = viewerHasMedia();
  const isFit = Math.abs(state.viewerZoom - 1) < .001 && Math.abs(state.viewerPanX) < .5 && Math.abs(state.viewerPanY) < .5;
  const actualZoom = actualSizeZoom();
  const media = viewerMediaBox();
  stage.style.setProperty('--viewer-zoom', String(state.viewerZoom));
  stage.style.setProperty('--viewer-pan-x', `${state.viewerPanX}px`);
  stage.style.setProperty('--viewer-pan-y', `${state.viewerPanY}px`);
  stage.style.setProperty('--viewer-media-width', `${media.width}px`);
  stage.style.setProperty('--viewer-media-height', `${media.height}px`);
  stage.classList.toggle('has-media', hasMedia);
  stage.classList.toggle('is-zoomed', hasMedia && !isFit);
  stage.classList.toggle('is-panning', state.viewerPanning);
  $('#fit-view').classList.toggle('active', isFit);
  $('#actual-view').classList.toggle('active', hasMedia && Math.abs(state.viewerZoom - actualZoom) < .01);
  $('#fit-view').disabled = !hasMedia;
  $('#actual-view').disabled = !hasMedia;
  $('#zoom-out').disabled = !hasMedia || state.viewerZoom <= MIN_VIEWER_ZOOM + .001;
  $('#zoom-in').disabled = !hasMedia || state.viewerZoom >= MAX_VIEWER_ZOOM - .001;
  $('#zoom-readout').textContent = isFit ? 'Fit' : `${Math.round(state.viewerZoom * 100)}%`;
}

function setViewerZoom(nextZoom, anchor = null) {
  if (!viewerHasMedia()) return;
  const previousZoom = state.viewerZoom;
  const zoom = Math.max(MIN_VIEWER_ZOOM, Math.min(MAX_VIEWER_ZOOM, nextZoom));
  const bounds = $('#viewer-stage').getBoundingClientRect();
  if (anchor && bounds.width && bounds.height && zoom !== previousZoom) {
    const factor = zoom / previousZoom;
    const offsetX = anchor.x - bounds.left - bounds.width / 2;
    const offsetY = anchor.y - bounds.top - bounds.height / 2;
    state.viewerPanX = offsetX - factor * (offsetX - state.viewerPanX);
    state.viewerPanY = offsetY - factor * (offsetY - state.viewerPanY);
  }
  state.viewerZoom = zoom;
  clampViewerPan();
  updateViewerZoomUI();
}

function resetViewerZoom() {
  state.viewerZoom = 1;
  state.viewerPanX = 0;
  state.viewerPanY = 0;
  state.viewerPanning = false;
  state.viewerPanPointer = null;
  updateViewerZoomUI();
}

function zoomViewerBy(factor) {
  const bounds = $('#viewer-stage').getBoundingClientRect();
  setViewerZoom(state.viewerZoom * factor, { x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 });
}

function handleViewerWheel(event) {
  if (!viewerHasMedia()) return;
  event.preventDefault();
  const factor = Math.exp(-event.deltaY * .0015);
  setViewerZoom(state.viewerZoom * factor, { x: event.clientX, y: event.clientY });
}

function splitDividerNearPointer(event) {
  if (state.view !== 'split') return false;
  const bounds = $('#viewer-stage').getBoundingClientRect();
  const position = state.splitPosition / 100;
  const scaled = state.splitOrientation === 'horizontal'
    ? bounds.top + bounds.height / 2 + ((position - .5) * bounds.height * state.viewerZoom) + state.viewerPanY
    : bounds.left + bounds.width / 2 + ((position - .5) * bounds.width * state.viewerZoom) + state.viewerPanX;
  const pointer = state.splitOrientation === 'horizontal' ? event.clientY : event.clientX;
  return Math.abs(pointer - scaled) < 14;
}

function beginViewerPan(event) {
  const canPan = state.viewerZoom > 1.001;
  const pointerPan = event.pointerType === 'touch' || event.button === 1 || state.spacePan || (canPan && state.view !== 'split');
  if (!canPan || !pointerPan || (state.view === 'split' && splitDividerNearPointer(event))) return false;
  state.viewerPanning = true;
  state.viewerPanPointer = { x: event.clientX, y: event.clientY };
  $('#viewer-stage').setPointerCapture(event.pointerId);
  updateViewerZoomUI();
  return true;
}

function moveViewerPan(event) {
  if (!state.viewerPanning || !state.viewerPanPointer) return;
  state.viewerPanX += event.clientX - state.viewerPanPointer.x;
  state.viewerPanY += event.clientY - state.viewerPanPointer.y;
  state.viewerPanPointer = { x: event.clientX, y: event.clientY };
  clampViewerPan();
  updateViewerZoomUI();
}

function endViewerPointer() {
  state.viewerPanning = false;
  state.viewerPanPointer = null;
  state.splitDragging = false;
  updateViewerZoomUI();
}

function renderViewer() {
  const empty = $('#viewer-empty');
  const viewerElements = ['viewer-source', 'viewer-reference', 'viewer-result', 'viewer-difference', 'viewer-split'];
  viewerElements.forEach(id => { $(`#${id}`).hidden = true; });
  $('#viewer-label').hidden = true;

  if (state.bypassing && state.source) {
    empty.hidden = true;
    $('#viewer-source').hidden = false;
    $('#viewer-label').textContent = 'BYPASS · SOURCE';
    $('#viewer-label').hidden = false;
    updateViewerZoomUI();
    return;
  }

  const hasAny = state.source || state.reference || state.profileId;
  if (!hasAny) {
    empty.hidden = false;
    empty.querySelector('h1').textContent = '从源静帧和参考图开始';
    updateViewerZoomUI();
    return;
  }

  const view = chooseFallbackView();
  empty.hidden = true;
  if (view === 'source' && state.source) {
    $('#viewer-source').hidden = false;
    $('#viewer-label').textContent = 'SOURCE';
    $('#viewer-label').hidden = false;
  } else if (view === 'reference' && state.reference) {
    $('#viewer-reference').hidden = false;
    $('#viewer-label').textContent = 'REFERENCE';
    $('#viewer-label').hidden = false;
  } else if (view === 'result' && state.preview) {
    $('#viewer-result').hidden = false;
    $('#viewer-label').textContent = 'RESULT';
    $('#viewer-label').hidden = false;
  } else if (view === 'difference' && state.preview) {
    $('#viewer-difference').hidden = false;
    $('#viewer-label').textContent = 'DIFFERENCE ×4';
    $('#viewer-label').hidden = false;
  } else if (view === 'split' && state.preview) {
    const sources = splitSources();
    $('#split-base').src = sources.base;
    $('#split-result').src = sources.result;
    $('#split-left-label').textContent = sources.left;
    $('#viewer-split span:last-child').textContent = sources.right;
    $('#viewer-split').classList.toggle('horizontal', state.splitOrientation === 'horizontal');
    $('#viewer-split').hidden = false;
    updateSplitClip();
  } else if (state.profileId && !state.source) {
    empty.hidden = false;
    empty.querySelector('h1').textContent = '配置已加载，等待源静帧';
  }
  updateViewButtons();
  updateViewerZoomUI();
}

function canvasContext(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  return { context, width: rect.width, height: rect.height };
}

function drawGrid(context, width, height, vertical = 6, horizontal = 4) {
  context.strokeStyle = '#282c31';
  context.lineWidth = 1;
  for (let index = 1; index < vertical; index++) {
    context.beginPath(); context.moveTo(width*index/vertical, 0); context.lineTo(width*index/vertical, height); context.stroke();
  }
  for (let index = 1; index < horizontal; index++) {
    context.beginPath(); context.moveTo(0, height*index/horizontal); context.lineTo(width, height*index/horizontal); context.stroke();
  }
}

function drawEmptyScope(canvas, label) {
  const { context, width, height } = canvasContext(canvas);
  drawGrid(context, width, height);
  context.fillStyle = '#626971';
  context.font = '11px -apple-system, sans-serif';
  context.textAlign = 'center';
  context.fillText(label, width/2, height/2);
}

function drawWaveform(canvas, data) {
  const { context, width, height } = canvasContext(canvas);
  drawGrid(context, width, height, 8, 4);
  const sourceWidth = data.waveformWidth;
  const sourceHeight = data.waveformHeight;
  const values = data.waveform;
  const maximum = Math.max(...values, 1);
  const cellWidth = width/sourceWidth;
  const cellHeight = height/sourceHeight;
  for (let x = 0; x < sourceWidth; x++) {
    for (let y = 0; y < sourceHeight; y++) {
      const value = values[x*sourceHeight+y];
      if (!value) continue;
      const alpha = .05 + .78*Math.sqrt(value/maximum);
      context.fillStyle = `rgba(112, 210, 161, ${alpha})`;
      context.fillRect(x*cellWidth, height-(y+1)*cellHeight, Math.max(1, cellWidth), Math.max(1, cellHeight));
    }
  }
}

function drawParade(canvas, data) {
  const { context, width, height } = canvasContext(canvas);
  drawGrid(context, width, height, 6, 4);
  const colors = ['#d76f67', '#72c58a', '#6f9eda'];
  data.histogram.forEach((values, channel) => {
    const sectionWidth = width/3;
    const start = channel*sectionWidth;
    const maximum = Math.max(...values, 1);
    context.beginPath();
    values.forEach((value, index) => {
      const x = start + index/(values.length-1)*(sectionWidth-10) + 5;
      const y = height - Math.log1p(value)/Math.log1p(maximum)*(height-10);
      index ? context.lineTo(x, y) : context.moveTo(x, y);
    });
    context.strokeStyle = colors[channel];
    context.lineWidth = 1.5;
    context.stroke();
  });
}

function drawVector(canvas, data) {
  const { context, width, height } = canvasContext(canvas);
  const radius = Math.min(width, height)*.39;
  const centerX = width/2;
  const centerY = height/2;
  context.strokeStyle = '#343a3f';
  context.lineWidth = 1;
  [1/3, 2/3, 1].forEach(scale => {
    context.beginPath(); context.arc(centerX, centerY, radius*scale, 0, Math.PI*2); context.stroke();
  });
  context.beginPath(); context.moveTo(centerX-radius, centerY); context.lineTo(centerX+radius, centerY); context.moveTo(centerX, centerY-radius); context.lineTo(centerX, centerY+radius); context.stroke();
  const maximum = Math.max(...data.vectorscope, 1);
  const size = data.scopeSize;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const value = data.vectorscope[y*size+x];
      if (!value) continue;
      const alpha = .06 + .78*Math.sqrt(value/maximum);
      context.fillStyle = `rgba(213, 169, 78, ${alpha})`;
      context.fillRect(centerX+(x/(size-1)-.5)*radius*2, centerY+(y/(size-1)-.5)*radius*2, Math.max(1, radius*2/size), Math.max(1, radius*2/size));
    }
  }
  context.fillStyle = '#717880';
  context.font = '10px -apple-system, sans-serif';
  context.fillText('R', centerX-3, centerY-radius-7);
  context.fillText('M', centerX+radius+7, centerY+3);
  context.fillText('C', centerX-3, centerY+radius+14);
  context.fillText('G', centerX-radius-14, centerY+3);
}

function analysisForTarget() {
  return state.analyses[state.scopeTarget];
}

function drawDelta() {
  const delta = state.preview?.delta;
  const signal = $('#signal-check');
  signal.className = '';
  if (!delta) {
    $('#delta-luma').textContent = '—';
    $('#delta-oklab').textContent = '—';
    $('#delta-chroma').textContent = '—';
    $('#delta-clipping').textContent = '—';
    signal.querySelector('span').textContent = '等待结果分析';
    return;
  }
  const signed = value => `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}`;
  $('#delta-luma').textContent = `${signed(delta.lumaP50Ire)} IRE`;
  $('#delta-oklab').textContent = Number(delta.oklabMeanDelta).toFixed(4);
  $('#delta-chroma').textContent = signed(delta.chromaDelta);
  const risk = delta.clippingPercent < .5 ? 'LOW' : delta.clippingPercent < 2 ? 'MED' : 'HIGH';
  $('#delta-clipping').textContent = `${Number(delta.clippingPercent).toFixed(2)}% · ${risk}`;
  signal.classList.add(risk === 'HIGH' ? 'warn' : 'ok');
  signal.querySelector('span').textContent = risk === 'HIGH' ? '检测到明显信号越界风险' : '当前配置通过基础信号检查';
}

function drawScopes() {
  const data = analysisForTarget();
  const targetLabel = state.scopeTarget[0].toUpperCase() + state.scopeTarget.slice(1);
  $('#vector-scope-card header span').textContent = targetLabel;
  $('#scope-grid').classList.toggle('vector-focus', state.scope === 'vectorscope');
  if (!data) {
    drawEmptyScope($('#primary-scope'), '导入或生成画面后显示示波器');
    drawEmptyScope($('#vectorscope'), '等待分析数据');
    $('#luma').textContent = 'P10 / P50 / P90：—';
  } else {
    $('#luma').textContent = `P10 / P50 / P90：${data.luma.map(value => Number(value).toFixed(3)).join(' / ')}`;
    if (state.scope === 'parade') {
      $('#primary-scope-title').textContent = 'RGB PARADE';
      drawParade($('#primary-scope'), data);
    } else {
      $('#primary-scope-title').textContent = 'WAVEFORM · LUMA';
      drawWaveform($('#primary-scope'), data);
    }
    drawVector($('#vectorscope'), data);
  }
  drawDelta();
}

async function openProfile(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file, file.name);
  setStatus('正在校验 Profile schema 与引擎兼容性…');
  try {
    const result = await api('/api/profile/import', { method: 'POST', body: form });
    state.profileId = result.profileId;
    applyProfile(result.profile);
    const calibration = result.profile.calibration;
    if (!state.source) {
      $('#source-name').textContent = calibration.source.label;
      $('#source-meta').textContent = 'Profile 已包含源统计；导入画面后可预览';
      $('#source-state').textContent = '配置数据';
    }
    if (!state.reference) {
      $('#reference-name').textContent = calibration.reference.label;
      $('#reference-meta').textContent = 'Profile 已包含参考统计；原图未内嵌';
      $('#reference-state').textContent = '配置数据';
    }
    resetPreview();
    updateActions();
    renderViewer();
    setStatus(`已打开 ${result.filename}；配置兼容 ReferenceMatch ${result.profile.engine.minVersion}。`);
    showToast('Profile 校验通过。导入源静帧即可预览，或直接安装到 Resolve。');
    if (state.source) await makePreview();
  } catch (error) {
    setStatus(error.message);
    showToast(error.message, true);
  } finally {
    $('#profile-input').value = '';
  }
}

function openSaveDialog() {
  if (!canSave()) return;
  $('#profile-name').value = profileName();
  $('#profile-tags').value = (state.profile?.profile?.tags || []).join(', ');
  $('#save-dialog').showModal();
  requestAnimationFrame(() => $('#profile-name').focus());
}

async function saveProfile() {
  const name = $('#profile-name').value.trim();
  if (!name) {
    showToast('请输入 Profile 名称。', true);
    return;
  }
  const tags = $('#profile-tags').value.split(',').map(value => value.trim()).filter(Boolean);
  setStatus('正在校验并保存 .rmatch.json…');
  try {
    const result = await api('/api/profile/save', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload({ profileName: name, tags })),
    });
    state.profileId = result.profileId;
    state.profile = result.profile;
    applyProfile(result.profile);
    const anchor = document.createElement('a');
    anchor.href = result.downloadUrl;
    anchor.download = result.filename;
    anchor.click();
    $('#save-dialog').close();
    setStatus(`已保存 ${result.filename}。JSON 是唯一可编辑配置源。`);
    showToast(`已保存并下载 ${result.filename}`);
  } catch (error) {
    setStatus(error.message);
    showToast(error.message, true);
  }
}

async function installProfile() {
  if (!state.profileId || state.dirty) {
    showToast('请先保存当前 Profile，再安装到 Resolve。', true);
    return;
  }
  setStatus('Reference Match Bridge 正在校验并激活配置…');
  $('#install-profile').disabled = true;
  try {
    const result = await api('/api/profile/activate', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload()),
    });
    $('#install-title').textContent = result.resolveInstalled ? '已安装到 Resolve LUT 目录' : '工作区配置已激活';
    $('#install-message').textContent = result.message;
    $('#install-name').textContent = result.profileName;
    $('#install-engine').textContent = `ReferenceMatch.dctl · ${result.engineVersion}`;
    $('#install-hash').textContent = result.profileHash;
    $('#install-dialog').showModal();
    setStatus(result.message);
  } catch (error) {
    setStatus(error.message);
    showToast(error.message, true);
  } finally {
    updateActions();
  }
}

function handleSplitPointer(event) {
  if (!state.splitDragging || state.view !== 'split') return;
  const bounds = $('#viewer-stage').getBoundingClientRect();
  state.splitPosition = state.splitOrientation === 'horizontal'
    ? Math.max(0, Math.min(100, (event.clientY-bounds.top)/bounds.height*100))
    : Math.max(0, Math.min(100, (event.clientX-bounds.left)/bounds.width*100));
  updateSplitClip();
}

function bindEvents() {
  $('#choose-source').addEventListener('click', () => $('#source-input').click());
  $('#choose-reference').addEventListener('click', () => $('#reference-input').click());
  $('#empty-source').addEventListener('click', () => $('#source-input').click());
  $('#empty-profile').addEventListener('click', () => $('#profile-input').click());
  $('#open-profile').addEventListener('click', () => $('#profile-input').click());
  $('#source-input').addEventListener('change', event => upload('source', event.target.files[0]));
  $('#reference-input').addEventListener('change', event => upload('reference', event.target.files[0]));
  $('#profile-input').addEventListener('change', event => openProfile(event.target.files[0]));

  ['source', 'reference'].forEach(kind => {
    const drop = $(`#${kind}-drop`);
    drop.addEventListener('dragenter', event => { event.preventDefault(); drop.classList.add('dragging'); });
    drop.addEventListener('dragover', event => event.preventDefault());
    drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
    drop.addEventListener('drop', event => {
      event.preventDefault();
      drop.classList.remove('dragging');
      upload(kind, event.dataTransfer.files[0]);
    });
  });

  controlIds.forEach(id => {
    $(`#${id}`).addEventListener('input', event => syncControl(id, event.target.value));
    $(`#${id}-value`).addEventListener('input', event => syncControl(id, event.target.value));
    $(`#${id}-value`).addEventListener('change', event => syncControl(id, event.target.value));
    [$(`#${id}`), $(`#${id}-value`)].forEach(element => element.addEventListener('dblclick', () => syncControl(id, defaults[id])));
  });

  $('#input-encoding').addEventListener('change', () => {
    updateContract();
    setDirty(true);
    resetPreview();
    if (canPreview()) makePreview();
  });
  $('#reset-controls').addEventListener('click', () => {
    Object.entries(defaults).forEach(([id, value]) => syncControl(id, value, false));
    setDirty(true);
    schedulePreview();
  });
  $('#preview').addEventListener('click', makePreview);

  $$('[data-view]').forEach(button => button.addEventListener('click', () => {
    state.view = button.dataset.view;
    resetViewerZoom();
    renderViewer();
  }));
  $('#fit-view').addEventListener('click', resetViewerZoom);
  $('#actual-view').addEventListener('click', () => setViewerZoom(actualSizeZoom()));
  $('#zoom-out').addEventListener('click', () => zoomViewerBy(1 / 1.25));
  $('#zoom-in').addEventListener('click', () => zoomViewerBy(1.25));
  $('#split-orientation').addEventListener('click', event => {
    state.splitOrientation = state.splitOrientation === 'vertical' ? 'horizontal' : 'vertical';
    event.target.textContent = state.splitOrientation === 'vertical' ? '垂直擦拭' : '水平擦拭';
    renderViewer();
  });
  $('#swap-split').addEventListener('click', () => { state.splitSwapped = !state.splitSwapped; renderViewer(); });
  const viewerStage = $('#viewer-stage');
  viewerStage.addEventListener('wheel', handleViewerWheel, { passive: false });
  viewerStage.addEventListener('dblclick', resetViewerZoom);
  viewerStage.addEventListener('pointerdown', event => {
    if (beginViewerPan(event)) return;
    if (state.view === 'split') {
      state.splitDragging = true;
      viewerStage.setPointerCapture(event.pointerId);
      handleSplitPointer(event);
    }
  });
  viewerStage.addEventListener('pointermove', event => { moveViewerPan(event); handleSplitPointer(event); });
  viewerStage.addEventListener('pointerup', endViewerPointer);
  viewerStage.addEventListener('pointercancel', endViewerPointer);

  $$('[data-scope]').forEach(button => button.addEventListener('click', () => {
    state.scope = button.dataset.scope;
    $$('[data-scope]').forEach(item => item.classList.toggle('active', item === button));
    drawScopes();
  }));
  $('#scope-target').addEventListener('change', event => { state.scopeTarget = event.target.value; drawScopes(); });

  $('#save-profile').addEventListener('click', openSaveDialog);
  $('#save-dialog').addEventListener('submit', event => {
    if (event.submitter?.id === 'confirm-save') {
      event.preventDefault();
      saveProfile();
    }
  });
  $('#install-profile').addEventListener('click', installProfile);

  document.addEventListener('keydown', event => {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    if (event.code === 'Space' && viewerHasMedia()) { event.preventDefault(); state.spacePan = true; }
    if (!event.repeat && event.key.toLowerCase() === 'b' && state.source) { state.bypassing = true; renderViewer(); }
    const modes = { '1': 'source', '2': 'reference', '3': 'result', '4': 'split', '5': 'difference' };
    if (modes[event.key] && !$(`[data-view="${modes[event.key]}"]`).disabled) { state.view = modes[event.key]; renderViewer(); }
    if (event.key === '0') { event.preventDefault(); resetViewerZoom(); }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomViewerBy(1.25); }
    if (event.key === '-') { event.preventDefault(); zoomViewerBy(1 / 1.25); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); openSaveDialog(); }
    if (event.shiftKey && event.key.toLowerCase() === 'f') { event.preventDefault(); $('#viewer-stage').requestFullscreen?.(); }
  });
  document.addEventListener('keyup', event => {
    if (event.code === 'Space') state.spacePan = false;
    if (event.key.toLowerCase() === 'b') { state.bypassing = false; renderViewer(); }
  });
  window.addEventListener('blur', () => { state.spacePan = false; endViewerPointer(); });
  window.addEventListener('resize', () => requestAnimationFrame(() => { clampViewerPan(); updateViewerZoomUI(); drawScopes(); }));
}

async function init() {
  bindEvents();
  controlIds.forEach(id => syncControl(id, defaults[id], false));
  updateContract();
  setDirty(true);
  renderViewer();
  requestAnimationFrame(drawScopes);
  try {
    const health = await api('/api/health');
    $('#bridge-state').textContent = `Bridge ${health.engineVersion}${health.resolveConfigured ? ' · Resolve 已配置' : ''}`;
    if (health.activeProfile) $('#bridge-state').title = `当前激活：${health.activeProfile}`;
  } catch (error) {
    $('#service').classList.add('offline');
    $('#service').lastChild.textContent = '服务不可用';
    setStatus('本地服务不可用，请启动 server.py。');
  }
}

init();
