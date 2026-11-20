const bench = window.BENCH_DATA || { tasks: [], summary: {} };
const tasks = bench.tasks;
const taskById = new Map(tasks.map((task) => [task.id, task]));
document.querySelector('#currentRunCrumb').textContent = bench.run || 'NO RUN';

const categoryMeta = {
  'data-pipeline': { label: 'DATA', color: 'data', tone: 'red', title: '数据管道', copy: 'SCD、时点汇率、精确聚合与大文件' },
  debug: { label: 'DEBUG', color: 'debug', tone: 'orange', title: '排障能力', copy: '静默错误、生命周期与并发状态机' },
  'fix-and-test': { label: 'FIX', color: 'fix', tone: 'green', title: '修复能力', copy: '跨模块契约、缓存选择与淘汰策略' },
  locate: { label: 'LOCATE', color: 'locate', tone: 'cyan', title: '结构化根因定位', copy: '跨层调用链、干扰项与根因三元组' },
  'memory-loop': { label: 'MEMORY', color: 'memory', tone: 'red', title: '记忆闭环', copy: '主动检索、沉淀、调用与错误修正' },
  'script-dev': { label: 'SCRIPT', color: 'script', tone: 'violet', title: '脚本工程', copy: 'Unicode、时区、CLI 与隐藏泛化输入' },
};

const filters = { search: '', status: 'all', category: 'all' };
let activeTaskId = null;
let activeDetailTab = 'overview';
let viewBeforeDrawer = 'runs';
let toastTimer = null;
let runConfig = null;
let runPollTimer = null;
let runLogSequence = 0;
let pendingRunScope = null;
let historyData = null;
let historyLoading = false;
// null = 用户还没选：先看当前题面那层，空了才退回历史快照。
let rankingTier = null;
let activeModelName = null;
// 看板"跑这 N 题"带进来的排队清单；scope=board 时用它当运行范围。
let boardLaunchTasks = [];

// ── 总览附加模块：趋势对比、运行信封、真实信号面板 ──────────────────────
// 「大文件探查」「运行设置」两页删掉后，它们的真实信息改由这里供给：
// run 元信息与通过率差来自 data.js + /api/history，运行参数来自 Runner API。
const RUN_CHIP_LABELS = {
  idle: ['IDLE', ''], offline: ['OFFLINE', 'danger-chip'], starting: ['STARTING', 'running-chip'],
  running: ['RUNNING', 'running-chip'], pausing: ['PAUSING', 'running-chip'], paused: ['PAUSED', 'running-chip'],
  cancelling: ['STOPPING', 'danger-chip'], finalizing: ['REPORTING', 'running-chip'],
  completed: ['COMPLETE', ''], cancelled: ['STOPPED', 'danger-chip'], failed: ['FAILED', 'danger-chip'],
};

let envelopeState = null;
let envelopeDefaults = null;

function formatRunStamp(stamp) {
  const match = String(stamp || '').match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  if (!match) return '';
  const [, year, month, day, hour, minute] = match;
  return `${year}-${month}-${day} ${hour}:${minute}`;
}

function setRunStatusChip(status) {
  const chip = document.querySelector('#runStatusChip');
  if (!chip) return;
  const [label, extra] = RUN_CHIP_LABELS[status] || [String(status || 'UNKNOWN').toUpperCase(), ''];
  chip.className = `status-chip${extra ? ` ${extra}` : ''}`;
  chip.innerHTML = `<i></i> ${esc(label)}`;
}

// 顶栏芯片与卡片脚注共用同一份并发值，避免"4 concurrent workers"这种写死文案。
function setConcurrencyNote(jobs) {
  const foot = document.querySelector('#overviewElapsedFoot');
  if (foot) foot.textContent = jobs ? `任务墙钟均值 · ${jobs} 并发` : '任务墙钟均值';
}

function currentRunModel() {
  const models = new Set(tasks.map((task) => task.result?.model).filter(Boolean));
  return models.size === 1 ? [...models][0] : null;
}

// 趋势只跟"同一模型对同一套题的上一轮完整 run"比：跨模型比出来的涨跌是假信号。
function applyRateTrend() {
  const slot = document.querySelector('#overviewRateTrend');
  if (!slot) return;
  const comparable = tasks.filter(isComparable);
  const passed = comparable.filter((task) => taskStatus(task) === 'PASS').length;
  const rate = comparable.length ? (passed / comparable.length) * 100 : null;
  const model = currentRunModel();
  const runs = (historyData?.runs || []).filter((run) => (
    run.model === model && run.runId !== bench.run && Number(run.comparableTotal) > 0
  ));
  const previous = runs[0];   // /api/history 按时间倒序返回，第一条就是上一轮
  if (!previous || rate == null) {
    slot.hidden = true;
    slot.textContent = '';
    return;
  }
  const delta = rate - (Number(previous.comparableRate) || 0) * 100;
  slot.className = `trend ${delta >= 0 ? 'up' : 'down'}`;
  slot.hidden = Math.abs(delta) < 0.5;
  slot.textContent = `${delta >= 0 ? '↑' : '↓'} ${Math.abs(delta).toFixed(1)}%`;
  slot.title = `与同模型上一轮完整 run ${previous.runId}（${percent(previous.comparableRate)}）比较`;
}

function envRow(label, value, tone) {
  return `<div><dt>${esc(label)}</dt><dd${tone ? ` class="${tone}"` : ''}>${value}</dd></div>`;
}

// 「运行设置」页并入总览后，这里只读展示当前生效参数；要改参数仍然走发起评测弹窗。
function renderEnvelope(state) {
  envelopeState = state || envelopeState;
  const list = document.querySelector('#envList');
  if (!list) return;
  const current = envelopeState;
  const hasRun = Boolean(current?.id) && current?.status !== 'idle';
  const source = hasRun ? current : (envelopeDefaults || {});
  const jobs = current?.concurrency?.jobs ?? current?.jobs ?? source.maxJobs;
  setConcurrencyNote(hasRun ? jobs : null);
  const rows = [
    envRow('状态', esc(hasRun ? (RUN_CHIP_LABELS[current.status]?.[0] || String(current.status).toUpperCase()) : '未发起 run'), hasRun ? undefined : 'muted'),
    envRow('连接配置', `${esc(source.profile || source.name || '—')}<small>${esc(source.endpoint || '—')}</small>`, 'highlight'),
    envRow('API MODEL', esc(source.apiModel || '—')),
    envRow('并发 / SUITE', `${hasRun ? Number(jobs ?? '—') : '—'} · ${tasks.length} 题<span>${esc((current?.scope || 'full').toUpperCase())}</span>`),
    envRow('MAX OUTPUT TOKENS', `${Number(source.maxOutputTokens || 0).toLocaleString()}<span>超时 ${source.requestTimeoutSeconds ?? '—'}s · judge ${(current?.judge ?? envelopeDefaults?.judge) ? 'ON' : 'OFF'}</span>`),
    envRow('输出目录', esc(source.outputDir || (hasRun ? '—' : '等第一次 run 结束后写入'))),
  ];
  list.innerHTML = rows.join('');
  if (!hasRun && !envelopeDefaults) loadEnvelopeDefaults();
}

// 没有 run 的时候退回 models.yaml 里的默认连接配置，卡片不留空。
async function loadEnvelopeDefaults() {
  try {
    const config = await apiRequest('/api/config');
    const remembered = localStorage.getItem('tracebench-model-profile') || '';
    const profile = config.models.find((model) => model.name === remembered) || config.models[0];
    if (!profile) return;
    envelopeDefaults = { ...profile, name: profile.name };
    renderEnvelope(envelopeState);
  } catch (_) {
    const list = document.querySelector('#envList');
    if (list && !envelopeState) list.innerHTML = envRow('状态', '<span class="muted">本地 Runner 未连接，参数读不到</span>');
  }
}

function focusRunsFilter(status, category) {
  showView('runs');
  if (category) applyCategoryFilter(category);
  if (status) {
    filters.status = status;
    document.querySelectorAll('#statusFilters button').forEach((button) => button.classList.toggle('selected', button.dataset.status === status));
    renderRuns();
  }
  toggleNoticePanel(false);
}

// 信号面板只统计当前 suite 里真实存在的问题；通知徽标数 == 列表条数。
function buildSignals() {
  const items = [];
  const offline = document.querySelector('#runnerService')?.classList.contains('offline');
  if (offline) items.push({ text: '本地 Runner 未连接', note: '评测与实时参数都读不到', href: 'console.html' });
  tasks.filter((task) => taskStatus(task) === 'FAIL').slice(0, 6).forEach((task) => {
    items.push({ open: task.id, text: `失败 · ${task.title}`, note: (task.result?.failure || '无失败证据').slice(0, 90), tone: 'fail' });
  });
  const stale = tasks.filter((task) => taskStatus(task) === 'STALE');
  if (stale.length) items.push({ status: 'STALE', text: `${stale.length} 道定义已更新`, note: '旧结果不计入通过率，点进来重跑', tone: 'warn' });
  const ungraded = tasks.filter((task) => taskStatus(task) === 'UNGRADED');
  if (ungraded.length) items.push({ status: 'UNGRADED', text: `${ungraded.length} 道未评`, note: '上游故障或裁判没评成，不进分母', tone: 'warn' });
  const memoryFail = tasks.filter((task) => task.category === 'memory-loop' && taskStatus(task) === 'FAIL').length;
  if (memoryFail) items.push({ category: 'memory-loop', text: `记忆闭环 ${tasks.filter((t) => t.category === 'memory-loop').length - memoryFail}/${tasks.filter((t) => t.category === 'memory-loop').length}`, note: '检索、核验与沉淀没走通', tone: 'warn' });
  return items;
}

function renderNotices() {
  const badge = document.querySelector('#noticeCount');
  const body = document.querySelector('#noticeBody');
  if (!badge || !body) return;
  const items = buildSignals();
  badge.hidden = items.length === 0;
  badge.textContent = items.length;
  if (!items.length) {
    body.innerHTML = '<p class="notice-empty">本轮没有待处理信号：全部可比较题有结论，Runner 在线。</p>';
    return;
  }
  body.innerHTML = items.map((item) => {
    const attrs = item.open ? `data-notice-open="${esc(item.open)}"`
      : item.status ? `data-notice-status="${esc(item.status)}"`
        : item.category ? `data-notice-category="${esc(item.category)}"` : '';
    const tag = item.href ? 'a' : 'button';
    const href = item.href ? `href="${esc(item.href)}" target="_blank" rel="noopener"` : 'type="button"';
    return `<${tag} class="notice-item${item.tone ? ` ${item.tone}` : ''}" ${attrs} ${href}><b>${esc(item.text)}</b><small>${esc(item.note)}</small></${tag}>`;
  }).join('');
}

function toggleNoticePanel(force) {
  const panel = document.querySelector('#noticePanel');
  const button = document.querySelector('#notificationButton');
  const open = force === undefined ? panel.hidden : force;
  if (open) renderNotices();
  panel.hidden = !open;
  button.setAttribute('aria-expanded', String(open));
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function compactNumber(value) {
  if (value == null) return '—';
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

function categoryFor(task) {
  if (task.id === 'liberty-extract-001') return { label: 'LIBERTY', color: 'liberty' };
  return categoryMeta[task.category] || { label: task.category.toUpperCase(), color: '' };
}

function taskStatus(task) {
  if (!task.result || task.result.stale) return 'STALE';
  return task.result.status || 'STALE';
}

// STALE（题目定义已变）与 UNGRADED（上游故障/裁判没评成）都不是能力判定，
// 二者一起留在通过率分母之外；把它们画成 FAIL 会把非模型的原因算到模型头上。
const NON_COMPARABLE = new Set(['STALE', 'UNGRADED']);
function isComparable(task) {
  return !NON_COMPARABLE.has(taskStatus(task));
}

function apiTaskStatus(task) {
  if (task.stale) return 'STALE';
  if (task.graded === false) return 'UNGRADED';
  return task.passed ? 'PASS' : 'FAIL';
}

function taskRow(task, full = false) {
  const result = task.result || {};
  const status = taskStatus(task);
  const type = categoryFor(task);
  const failure = status === 'PASS' ? '—'
    : status === 'STALE' ? '题目定义已更新，需要重新评测'
    : status === 'UNGRADED' ? (result.failure || '未评：上游故障或 rubric 未评成，不计入通过率')
    : (result.failure || '未记录失败证据');
  const dot = status === 'PASS' ? 'pass' : status === 'FAIL' ? 'fail' : 'stale';
  return `<tr class="task-row" data-task="${esc(task.id)}" tabindex="0" aria-label="查看 ${esc(task.title)} 详情">
    <td><span class="result-dot ${dot}"></span><span class="task-title">${esc(task.title)}</span><span class="task-sub">${esc(task.id)}</span></td>
    <td><span class="type-tag ${type.color}">${type.label}</span></td>
    <td><span class="status-text ${status.toLowerCase()}">${status}</span></td>
    <td class="mono">${result.steps ?? '—'}</td>
    <td class="mono">${compactNumber(result.tokens)}</td>
    <td class="mono">${result.elapsed != null ? `${result.elapsed.toFixed(1)}s` : '—'}</td>
    ${full ? `<td class="failure-text" title="${esc(failure)}">${esc(failure)}</td><td><button class="row-action" data-open-task="${esc(task.id)}" aria-label="查看题目详情">查看 →</button></td>` : ''}
  </tr>`;
}

function bindTaskRows(scope = document) {
  scope.querySelectorAll('.task-row').forEach((row) => {
    row.addEventListener('click', (event) => {
      if (!event.target.closest('button')) openTask(row.dataset.task);
    });
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openTask(row.dataset.task);
      }
    });
  });
  scope.querySelectorAll('[data-open-task]').forEach((button) => {
    button.addEventListener('click', () => openTask(button.dataset.openTask));
  });
}

function renderRecent() {
  const table = document.querySelector('#recentTable');
  table.innerHTML = tasks.slice(-7).reverse().map((task) => taskRow(task)).join('');
  bindTaskRows(table);
}

function filteredTasks() {
  const query = filters.search.trim().toLowerCase();
  return tasks.filter((task) => {
    const type = categoryFor(task);
    const haystack = `${task.id} ${task.title} ${task.category} ${type.label} ${task.tags.join(' ')}`.toLowerCase();
    const searchMatch = !query || haystack.includes(query);
    const statusMatch = filters.status === 'all' || taskStatus(task) === filters.status;
    const categoryMatch = filters.category === 'all' || task.category === filters.category;
    return searchMatch && statusMatch && categoryMatch;
  });
}

function renderRuns() {
  const matches = filteredTasks();
  const table = document.querySelector('#runsTable');
  table.innerHTML = matches.length
    ? matches.map((task) => taskRow(task, true)).join('')
    : `<tr class="empty-row"><td colspan="8"><strong>没有匹配任务</strong><span>换个关键词或清空筛选条件。</span><button id="emptyClear">清空筛选</button></td></tr>`;
  document.querySelector('#resultCount').textContent = `显示 ${matches.length} / ${tasks.length} 个任务`;
  bindTaskRows(table);
  document.querySelector('#emptyClear')?.addEventListener('click', clearFilters);
}

function renderCategoryFilters() {
  const counts = tasks.reduce((acc, task) => {
    acc[task.category] = (acc[task.category] || 0) + 1;
    return acc;
  }, {});
  document.querySelector('#categoryFilters').innerHTML = [
    `<button class="selected" data-category="all">全部 <b>${tasks.length}</b></button>`,
    ...Object.entries(categoryMeta).map(([key, meta]) => `<button data-category="${key}">${meta.label} <b>${counts[key] || 0}</b></button>`),
  ].join('');
  document.querySelectorAll('[data-category]').forEach((button) => {
    button.addEventListener('click', () => {
      filters.category = button.dataset.category;
      document.querySelectorAll('[data-category]').forEach((item) => item.classList.toggle('selected', item === button));
      renderRuns();
    });
  });
}

function updateSuiteMeta() {
  const counts = tasks.reduce((acc, task) => {
    const status = taskStatus(task);
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  document.querySelector('#suiteTaskCount').textContent = tasks.length;
  document.querySelector('#runRecordKicker').textContent = `CURRENT SUITE / ${tasks.length} TASKS`;
  document.querySelector('#allCount').textContent = tasks.length;
  document.querySelector('#passCount').textContent = counts.PASS || 0;
  document.querySelector('#failCount').textContent = counts.FAIL || 0;
  document.querySelector('#staleCount').textContent = counts.STALE || 0;
  const comparable = tasks.filter(isComparable);
  const passed = comparable.filter((task) => taskStatus(task) === 'PASS').length;
  const rate = comparable.length ? Math.round((passed / comparable.length) * 100) : 0;
  const avgTokens = comparable.length ? comparable.reduce((sum, task) => sum + Number(task.result?.tokens || 0), 0) / comparable.length : 0;
  const avgElapsed = comparable.length ? comparable.reduce((sum, task) => sum + Number(task.result?.elapsed || 0), 0) / comparable.length : 0;
  const memory = tasks.filter((task) => task.category === 'memory-loop');
  const memoryPassed = memory.filter((task) => taskStatus(task) === 'PASS').length;
  // run 与时间只从 data.js 来，页面上不再写死日期。
  const startedLabel = formatRunStamp(bench.startedAt);
  document.querySelector('#overviewKicker').textContent = `SUITE ${bench.run || 'NO RUN'}${startedLabel ? ` / ${startedLabel}` : ''}`;
  document.querySelector('#overviewRate').innerHTML = `${rate}<span>%</span>`;
  document.querySelector('#overviewNavBadge').textContent = comparable.length ? `${rate}%` : '—';
  document.querySelector('#overviewRateFoot').innerHTML = `<b>${passed}</b> / ${comparable.length} comparable`
    + (counts.STALE ? ` · ${counts.STALE} 待重测` : '')
    + (counts.UNGRADED ? ` · ${counts.UNGRADED} 未评` : '');
  document.querySelector('#overviewTokens').innerHTML = `${(avgTokens / 1000).toFixed(1)}<span>k</span>`;
  document.querySelector('#overviewElapsed').innerHTML = `${Math.round(avgElapsed)}<span>s</span>`;
  document.querySelector('#overviewMemory').innerHTML = `${memoryPassed}<span>/${memory.length}</span>`;
  // ATTENTION 只在真有未闭环时挂着；全过还亮红就是噪音。
  const memoryFlag = document.querySelector('#memoryFlag');
  const memoryClean = memory.length > 0 && memoryPassed === memory.length;
  memoryFlag.hidden = memoryClean;
  document.querySelector('#overviewMemoryFoot').textContent = !memory.length
    ? '当前 suite 没有记忆闭环题'
    : (memoryClean ? `${memory.length} 道全部完成检索与核验` : `${memory.length - memoryPassed} 道未闭环，点 MEMORY 行下钻`);
  document.querySelector('#overviewRingPass').textContent = passed;
  document.querySelector('#overviewRingCaption').innerHTML = `current suite<br /><b>${tasks.length} tasks · ${counts.STALE || 0} stale${counts.UNGRADED ? ` · ${counts.UNGRADED} 未评` : ''}</b>`;
  document.querySelector('#launchSummary').textContent = `${tasks.length} tasks × 1 model`;
  document.querySelector('#monitorCompleted').textContent = `0 / ${tasks.length}`;
  document.querySelector('#insightSource').textContent = bench.generatedFrom || 'report.md';
  document.querySelector('#insightRun').textContent = bench.run || '—';
  document.querySelector('#workspaceMeta').textContent = `${tasks.length} 题 · run ${bench.run || '—'}`;
  document.querySelector('#buildMeta').textContent = `TRACEBENCH · SUITE ${tasks.length}${startedLabel ? ` · DATA ${startedLabel}` : ''}`;
  // 维度信号是下钻入口：矩阵页删掉后，同一组通过率只在这一处出现。
  document.querySelector('#overviewSignalList').innerHTML = Object.entries(categoryMeta).map(([category, meta]) => {
    const group = tasks.filter((task) => task.category === category);
    const ready = group.filter(isComparable);
    const ok = ready.filter((task) => taskStatus(task) === 'PASS').length;
    const label = ready.length ? `${Math.round((ok / ready.length) * 100)}%` : '待重测';
    return `<button class="signal-row" type="button" data-signal-category="${category}" aria-label="筛出 ${meta.title} 题目"><span><i class="signal-mark ${meta.tone}"></i>${meta.title} / ${meta.label}</span><b>${ok} / ${ready.length}</b><em>${label}</em></button>`;
  }).join('');
  document.querySelector('#overviewInsightQuote').innerHTML = counts.STALE
    ? `“有 <strong>${counts.STALE}</strong> 道定义已更新，<br /><strong>旧结果不进入比较。</strong>”`
    : `“当前完整 suite，<br /><strong>${passed} / ${comparable.length} 通过。</strong>”`;
  document.querySelector('#overviewInsightCopy').textContent = `当前共 ${tasks.length} 道任务，${comparable.length} 道有可比较结果；新增隐藏泛化测试和真实并发状态机题会自动使旧 run 标记为待重测。`;
  applyRateTrend();
  renderNotices();
}


function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function historyStatusLabel(status) {
  return {
    completed: '完成', interrupted: '中断', running: '运行中', starting: '启动中',
    finalizing: '生成报告', cancelling: '停止中', failed: '失败', cancelled: '已停止',
  }[status] || status;
}

async function loadHistory(force = false) {
  if (historyLoading || (historyData && !force)) return;
  historyLoading = true;
  const refresh = document.querySelector('#refreshHistory');
  refresh.disabled = true;
  refresh.textContent = '↻ 读取中…';
  try {
    historyData = await apiRequest('/api/history');
    renderHistory(historyData);
    renderLeaderboard(historyData.leaderboard || []);
    applyRateTrend();
    renderNotices();
  } catch (error) {
    document.querySelector('#historyTable').innerHTML = `<tr class="empty-row"><td colspan="8"><strong>历史记录读取失败</strong><span>${esc(error.message)}</span></td></tr>`;
    document.querySelector('#podiumGrid').innerHTML = `<article class="podium-loading error">${esc(error.message)}</article>`;
  } finally {
    historyLoading = false;
    refresh.disabled = false;
    refresh.textContent = '↻ 刷新记录';
  }
}

function renderHistory(data) {
  const summary = data.summary || {};
  const metricValues = [summary.runs || 0, summary.completed || 0, summary.interrupted || 0, summary.executions || 0];
  document.querySelectorAll('#historyMetrics article').forEach((card, index) => { card.querySelector('strong').textContent = metricValues[index]; });
  document.querySelector('#historyRunCount').textContent = summary.runs || 0;
  document.querySelector('#historyUpdated').textContent = `${data.runs.length} 条模型执行 · 更新于 ${new Date(data.generatedAt * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`;
  const table = document.querySelector('#historyTable');
  if (!data.runs.length) {
    table.innerHTML = '<tr class="empty-row"><td colspan="8"><strong>暂无运行记录</strong><span>从 Web 发起一次评测后会自动出现在这里。</span></td></tr>';
    return;
  }
  table.innerHTML = data.runs.map((run) => {
    const expected = run.expected || run.total;
    const report = run.reportReady ? `<a class="report-link" href="/api/runs/${encodeURIComponent(run.runId)}/report" target="_blank" rel="noopener">打开 ↗</a>` : '<span class="no-report">—</span>';
    const resume = run.resumable ? `<button class="history-run-button resume" data-resume-run="${esc(run.runId)}" type="button">▶ 继续 ${run.missingTasks.length} 题</button>` : '';
    const rejudgeIds = run.rejudgeable || [];
    const rejudge = rejudgeIds.length ? `<button class="history-run-button rejudge" data-rejudge-run="${esc(run.runId)}" title="${esc(rejudgeIds.join(', '))}" type="button">⚖ 重判 ${rejudgeIds.length} 题</button>` : '';
    const consoleLink = `<a class="history-run-button" href="console.html?run=${encodeURIComponent(run.runId)}">控制台 ↗</a>`;
    return `<tr><td><span class="history-time">${esc(run.timestamp || '未知时间')}</span><code>${esc(run.runId)}</code></td><td><strong class="history-model">${esc(run.model)}</strong></td><td><span class="run-status ${esc(run.status)}"><i></i>${esc(historyStatusLabel(run.status))}</span></td><td><span class="history-progress">${run.total} / ${expected}</span></td><td><div class="rate-cell"><strong>${percent(run.rate)}</strong><span><i style="width:${Math.round((run.rate || 0) * 100)}%"></i></span><small>${run.passed} passed</small></div></td><td class="mono">${Number(run.avgTokens || 0).toLocaleString()}</td><td class="mono">${run.avgElapsed || 0}s</td><td><div class="history-run-actions">${resume}${rejudge}${consoleLink}${report}</div></td></tr>`;
  }).join('');
  document.querySelectorAll('[data-resume-run]').forEach((button) => button.addEventListener('click', () => resumeHistoryRun(button)));
  document.querySelectorAll('[data-rejudge-run]').forEach((button) => button.addEventListener('click', () => rejudgeHistoryRun(button)));
}

async function rejudgeHistoryRun(button) {
  const runId = button.dataset.rejudgeRun;
  const run = historyData?.runs?.find((item) => item.runId === runId);
  const ids = run?.rejudgeable || [];
  if (!ids.length) return;
  if (!window.confirm(`重判 ${runId}？\n\n只补裁判那一半：用已归档的轨迹重跑判定，不重新采样、不再花一次 token。\n${ids.length} 道题：${ids.join('、')}`)) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '重判中…';
  try {
    const state = await apiRequest(`/api/runs/${encodeURIComponent(runId)}/rejudge`, { method: 'POST', body: '{}' });
    runLogSequence = 0;
    showRunMonitor(state, true);
    toast(`${runId} 已起重判 · ${ids.length} tasks`);
  } catch (error) {
    button.disabled = false;
    button.textContent = label;
    toast(`重判失败：${error.message}`);
  }
}

async function resumeHistoryRun(button) {
  const runId = button.dataset.resumeRun;
  const run = historyData?.runs?.find((item) => item.runId === runId);
  if (!run || !window.confirm(`继续 ${runId}？\n\n只会执行缺失的 ${run.missingTasks.length} 道题，已有结果不会重跑。`)) return;
  button.disabled = true;
  button.textContent = '启动中…';
  try {
    const state = await apiRequest(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: '{}' });
    runLogSequence = 0;
    showRunMonitor(state, true);
    toast(`${runId} 已续跑 · ${run.missingTasks.length} tasks`);
  } catch (error) {
    button.disabled = false;
    button.textContent = `▶ 继续 ${run.missingTasks.length} 题`;
    toast(`续跑失败：${error.message}`);
  }
}

function chooseRankingTier(tier) {
  rankingTier = tier;
  renderLeaderboard(historyData?.leaderboard || []);
}

function renderLeaderboard(models) {
  const podium = document.querySelector('#podiumGrid');
  const ranking = document.querySelector('#rankingTable');
  const tiers = {
    current: models.filter((model) => !model.historical),
    historical: models.filter((model) => model.historical),
  };
  const tier = rankingTier || (tiers.current.length ? 'current' : 'historical');
  const rows = tiers[tier];
  document.querySelector('#rankingModelCount').textContent = rows.length;
  document.querySelectorAll('#rankingTierSwitch button').forEach((button) => {
    const selected = button.dataset.tier === tier;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  document.querySelector('#tierCountCurrent').textContent = tiers.current.length;
  document.querySelector('#tierCountHistorical').textContent = tiers.historical.length;
  const archived = tier === 'historical';
  document.querySelector('#view-leaderboard .lede').textContent = archived
    ? '这些 run 的题面或判定代码版本与当前不一致，通过率不与「当前题面」那一层可比，只作留档。'
    : '每个模型只取最近那次对当前题面完整可比的 run，不跨 run 合并；更早的完整 run 留在评测历史里。';
  document.querySelector('#view-leaderboard .ranking-rule').textContent = archived ? 'ARCHIVED · NOT COMPARABLE' : 'RATE ↓ · COVERAGE ↓ · TOKENS ↑';
  document.querySelector('#view-leaderboard .ranking-panel .tiny-note').textContent = archived ? 'LAST COMPLETE SNAPSHOT PER MODEL' : 'CURRENT TASK SET ONLY';
  const successSteps = (model) => model.avgSuccessSteps == null ? '—' : `${Number(model.avgSuccessSteps).toFixed(1)} 步`;
  const suiteNote = (model) => `${model.total} 题`;
  if (!rows.length) {
    const hint = tier === 'current'
      ? '<button class="tier-empty-switch" data-tier="historical" type="button">看历史快照 →</button>'
      : '';
    podium.innerHTML = `<article class="podium-loading empty-tier"><strong>${tier === 'current' ? '还没有对当前题面完整可比的 run' : '没有需要留档的旧配置'}</strong><span>${tier === 'current' ? '整套题跑完、并且题面指纹全部对得上的模型才会进这一层。' : '所有已入库的完整 run 都还跟得上当前题面。'}</span>${hint}</article>`;
    ranking.innerHTML = `<tr class="empty-row"><td colspan="10"><strong>本层暂无排名</strong><span>${tier === 'current' ? '切到「历史快照」看旧题集上的成绩。' : '当前没有标记为历史配置的完整 run。'}</span></td></tr>`;
    document.querySelectorAll('.tier-empty-switch').forEach((button) => button.addEventListener('click', () => chooseRankingTier(button.dataset.tier)));
    return;
  }
  podium.innerHTML = rows.slice(0, 3).map((model, index) => `<article class="podium-card rank-${index + 1}${model.historical ? ' historical' : ''}" data-model-detail="${esc(model.model)}" tabindex="0" role="button" aria-label="查看 ${esc(model.model)} 详情"><span class="podium-rank">${String(index + 1).padStart(2, '0')}</span><button class="podium-delete" data-delete-model="${esc(model.model)}" type="button" aria-label="删除 ${esc(model.model)} 的评测数据">删除</button><div class="podium-medal">${index === 0 ? '♜' : index === 1 ? '◆' : '◇'}</div><p>${model.historical ? `历史配置快照 · ${suiteNote(model)}` : `当前配置 · ${suiteNote(model)}`} / ${model.runs} RUN</p><h2>${esc(model.model)}</h2><div class="podium-rate">${percent(model.rate)}</div><div class="podium-meta"><span><b>${model.passed}</b> / ${model.total} tasks</span><span><b>${Number(model.avgTokens).toLocaleString()}</b> avg tok</span><span><b>${successSteps(model)}</b> 成功 avg steps</span><span><b>${model.avgElapsed}s</b> avg</span>${model.historical ? `<span><b>${model.staleTasks}</b> stale · ${esc(model.sourceRunId || '')}</span>` : ''}</div><span class="podium-open">查看详情 →</span></article>`).join('');
  ranking.innerHTML = rows.map((model, index) => `<tr class="model-rank-row" data-model-detail="${esc(model.model)}" tabindex="0" aria-label="查看 ${esc(model.model)} 详情"><td><span class="rank-number">${String(index + 1).padStart(2, '0')}</span></td><td><strong class="history-model">${esc(model.model)}</strong><small class="model-sub">${model.historical ? `上一个完整配置 · ${suiteNote(model)} · ${esc(model.sourceRunId || '')}` : `最近一次完整 run ${esc(model.sourceRunId || '')} · ${percent(model.bestRate)}`} · 点击查看详情</small></td><td><div class="rank-rate"><strong>${percent(model.rate)}</strong><span><i style="width:${Math.round(model.rate * 100)}%"></i></span></div></td><td class="mono">${model.passed} / ${model.total}</td><td class="mono">${model.runs}</td><td class="mono">${Number(model.avgTokens).toLocaleString()}</td><td class="mono">${successSteps(model)}</td><td class="mono">${model.avgElapsed}s</td><td class="mono">${esc(model.latest)} <span class="rank-open">→</span></td><td><button class="ranking-delete" data-delete-model="${esc(model.model)}" type="button">删除</button></td></tr>`).join('');
  bindModelDetailLinks();
}

function bindModelDetailLinks() {
  document.querySelectorAll('[data-delete-model]').forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    archiveModel(button.dataset.deleteModel);
  }));
  document.querySelectorAll('[data-model-detail]').forEach((element) => {
    element.addEventListener('click', (event) => {
      if (!event.target.closest('[data-delete-model]')) openModelDetail(element.dataset.modelDetail);
    });
    element.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openModelDetail(element.dataset.modelDetail);
      }
    });
  });
}

async function archiveModel(modelName) {
  const model = historyData?.leaderboard?.find((item) => item.model === modelName);
  if (!model) return;
  const confirmed = window.confirm(`确定删除 ${modelName} 的评测数据吗？\n\n将从排名和运行记录移除 ${model.runs} 个完整 RUN、${model.total} 个任务样本。原始文件会移动到 runs/.trash，可手工恢复。`);
  if (!confirmed) return;
  const buttons = [...document.querySelectorAll('[data-delete-model]')].filter((button) => button.dataset.deleteModel === modelName);
  buttons.forEach((button) => { button.disabled = true; button.textContent = '归档中…'; });
  try {
    const result = await apiRequest('/api/models/archive', { method: 'POST', body: JSON.stringify({ modelName }) });
    closeModelDetail();
    historyData = null;
    await loadHistory(true);
    toast(`已删除 ${modelName}，归档 ${result.archivedRuns.length} 个运行目录`);
  } catch (error) {
    buttons.forEach((button) => { button.disabled = false; button.textContent = '删除'; });
    toast(`删除失败：${error.message}`);
  }
}

function modelCategoryStats(attempts) {
  const totals = new Map();
  attempts.forEach((attempt) => {
    if (attempt.stale) return;
    const item = totals.get(attempt.category) || { passed: 0, total: 0 };
    item.total += 1;
    item.passed += attempt.passed ? 1 : 0;
    totals.set(attempt.category, item);
  });
  return [...totals.entries()].sort(([left], [right]) => left.localeCompare(right));
}

function renderModelDetail(modelName) {
  const model = historyData?.leaderboard?.find((item) => item.model === modelName);
  if (!model) return;
  const runs = (historyData.runs || []).filter((run) => run.model === modelName);
  const rankedRuns = runs.filter((run) => run.indexed && run.status === 'completed');
  const attempts = rankedRuns.flatMap((run) => (run.taskResults || []).map((task) => ({ ...task, runId: run.runId, timestamp: run.timestamp })));
  const validAttempts = attempts.filter((task) => !task.stale && task.graded !== false);
  const staleAttempts = attempts.length - validAttempts.length;
  const categories = modelCategoryStats(attempts);
  document.querySelector('#modelDrawerTitle').textContent = modelName;
  document.querySelector('#modelDrawerRank').textContent = `RANK ${String(model.rank).padStart(2, '0')} · ${model.runs} COMPLETE RUN${model.runs === 1 ? '' : 'S'}${model.historical ? ' · PREVIOUS CONFIG' : ''}`;
  const categoryHtml = categories.length ? categories.map(([category, score]) => {
    const rate = score.total ? score.passed / score.total : 0;
    return `<div class="model-category-row"><span>${esc(category)}</span><div><i style="width:${Math.round(rate * 100)}%"></i></div><b>${score.passed}/${score.total}</b><em>${percent(rate)}</em></div>`;
  }).join('') : '<p class="model-empty">旧运行没有逐题类别数据。</p>';
  const runHtml = runs.length ? runs.map((run) => {
    const expected = run.expected || run.total;
    const report = run.reportReady ? `<a href="/api/runs/${encodeURIComponent(run.runId)}/report" target="_blank" rel="noopener">报告 ↗</a>` : '<span>—</span>';
    return `<tr><td><code>${esc(run.runId)}</code><small>${esc(run.timestamp || '')}</small></td><td><span class="run-status ${esc(run.status)}"><i></i>${esc(historyStatusLabel(run.status))}</span></td><td class="mono">${run.passed}/${run.total || expected}</td><td class="mono">${percent(run.rate)}</td><td class="mono">${Number(run.avgTokens || 0).toLocaleString()}</td><td class="mono">${run.avgElapsed || 0}s</td><td>${report}</td></tr>`;
  }).join('') : '<tr><td colspan="7" class="model-empty">没有运行记录</td></tr>';
  const attemptHtml = attempts.length ? attempts.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)) || a.taskId.localeCompare(b.taskId)).map((task, index) => {
    const judge = task.judgeScore == null ? '—' : `${Math.round(Number(task.judgeScore) * 100)} · ${esc(task.judgeVerdict || '')}`;
    const detailId = `model-attempt-${index}`;
    const failedChecks = (task.failedChecks || []).map((check) => `<article><span>${esc(check.type)}</span><code>${esc(check.detail)}</code><p>${esc(check.evidence || '无额外证据')}</p></article>`).join('');
    const judgeReasons = (task.judgeReasons || []).map((reason) => `<li>${esc(reason)}</li>`).join('');
    const judgeDimensions = Object.entries(task.judgeDimensions || {}).map(([name, score]) => `<span>${esc(name)} <b>${Math.round(Number(score) * 100)}</b></span>`).join('');
    const evidence = task.failure || failedChecks || judgeReasons || task.stale
      ? `${task.stale ? '<p class="attempt-stale-note">题目定义已变化：该结果仅代表当时版本，不计入上方“当前有效分类表现”。</p>' : ''}<div class="attempt-evidence-grid"><section><h4>失败摘要</h4><p>${esc(task.failure || '确定性检查没有记录失败摘要')}</p></section><section><h4>AI 裁判理由</h4>${judgeReasons ? `<ul>${judgeReasons}</ul>` : '<p>本次未启用 AI 裁判，或该题没有 rubric。</p>'}${judgeDimensions ? `<div class="judge-dimensions">${judgeDimensions}</div>` : ''}</section></div>${failedChecks ? `<section class="failed-check-list"><h4>未通过的确定性条件</h4>${failedChecks}</section>` : ''}`
      : '<p class="attempt-pass-note">该题通过，且没有额外失败证据。</p>';
    const resultClass = apiTaskStatus(task);
    const resultLabel = resultClass;
    return `<tr class="model-attempt-summary"><td><strong>${esc(task.title || task.taskId)}</strong><code>${esc(task.taskId)}</code><small>${esc(task.runId)}</small></td><td class="mono">${esc(task.category)}</td><td><span class="queue-state ${resultClass}"><i></i>${resultLabel}</span></td><td class="mono">${task.steps}</td><td class="mono">${Number(task.tokens || 0).toLocaleString()}</td><td class="mono">${task.elapsed || 0}s</td><td class="mono">${judge}</td><td><button class="attempt-toggle" data-attempt-detail="${detailId}" aria-expanded="false">证据 ↓</button></td></tr><tr class="attempt-evidence-row" id="${detailId}" hidden><td colspan="8"><div class="attempt-evidence">${evidence}<button class="attempt-task-link" data-open-model-task="${esc(task.taskId)}">查看当前题目定义 →</button></div></td></tr>`;
  }).join('') : '<tr><td colspan="8" class="model-empty">这些旧运行没有可下钻的逐题结果。</td></tr>';
  document.querySelector('#modelDrawerBody').innerHTML = `
    <section class="model-hero-stats"><article><span>HISTORICAL RATE</span><strong>${percent(model.rate)}</strong><small>best ${percent(model.bestRate)}</small></article><article><span>SAMPLE COVERAGE</span><strong>${model.total}</strong><small>${validAttempts.length} current · ${staleAttempts} stale</small></article><article><span>AVG. TOKENS</span><strong>${Number(model.avgTokens).toLocaleString()}</strong><small>per task</small></article><article><span>SUCCESS AVG. STEPS</span><strong>${model.avgSuccessSteps == null ? '—' : Number(model.avgSuccessSteps).toFixed(1)}</strong><small>successful tasks only</small></article><article><span>AVG. TIME</span><strong>${model.avgElapsed}s</strong><small>per task</small></article></section>
    <section class="model-detail-section"><header><div><span>CURRENT VALID CUT</span><h3>分类表现</h3></div><small>已排除 ${staleAttempts} 个定义变更结果</small></header><div class="model-category-list">${categoryHtml}</div></section>
    <section class="model-detail-section"><header><div><span>RUN TIMELINE</span><h3>运行记录</h3></div><small>${runs.length} EXECUTIONS</small></header><div class="model-table-wrap"><table class="model-runs-table"><thead><tr><th>RUN / 时间</th><th>状态</th><th>通过</th><th>通过率</th><th>平均 TOKENS</th><th>平均耗时</th><th>报告</th></tr></thead><tbody>${runHtml}</tbody></table></div></section>
    <section class="model-detail-section"><header><div><span>TASK ATTEMPTS</span><h3>逐题表现</h3></div><small>${attempts.length} SAMPLES</small></header><div class="model-table-wrap task-attempt-wrap"><table class="model-attempt-table"><thead><tr><th>任务 / RUN</th><th>类别</th><th>结果</th><th>步骤</th><th>TOKENS</th><th>耗时</th><th>AI 裁判</th><th>失败证据</th></tr></thead><tbody>${attemptHtml}</tbody></table></div></section>`;
  bindModelAttemptDetails();
}

function bindModelAttemptDetails() {
  document.querySelectorAll('[data-attempt-detail]').forEach((button) => button.addEventListener('click', () => {
    const row = document.getElementById(button.dataset.attemptDetail);
    const opening = row.hidden;
    row.hidden = !opening;
    button.setAttribute('aria-expanded', String(opening));
    button.textContent = opening ? '收起 ↑' : '证据 ↓';
  }));
  document.querySelectorAll('[data-open-model-task]').forEach((button) => button.addEventListener('click', () => {
    const taskId = button.dataset.openModelTask;
    closeModelDetail();
    openTask(taskId);
  }));
}

function openModelDetail(modelName) {
  if (!historyData?.leaderboard?.some((item) => item.model === modelName)) return;
  activeModelName = modelName;
  renderModelDetail(modelName);
  const layer = document.querySelector('#modelDrawer');
  layer.classList.add('open');
  layer.setAttribute('aria-hidden', 'false');
  document.body.classList.add('drawer-open');
  requestAnimationFrame(() => layer.querySelector('.drawer-close').focus());
}

function closeModelDetail() {
  const layer = document.querySelector('#modelDrawer');
  if (!layer.classList.contains('open')) return;
  layer.classList.remove('open');
  layer.setAttribute('aria-hidden', 'true');
  activeModelName = null;
  if (!document.querySelector('#taskDrawer').classList.contains('open') && !document.querySelector('#runDialog').classList.contains('open')) document.body.classList.remove('drawer-open');
}

function clearFilters() {
  filters.search = '';
  filters.status = 'all';
  filters.category = 'all';
  document.querySelector('#runSearch').value = '';
  document.querySelectorAll('#statusFilters button').forEach((button) => button.classList.toggle('selected', button.dataset.status === 'all'));
  document.querySelectorAll('[data-category]').forEach((button) => button.classList.toggle('selected', button.dataset.category === 'all'));
  renderRuns();
}

function criterionMarkup(rule, index) {
  let label = rule.type || 'rule';
  let body = '';
  if (rule.type === 'state_assert') {
    label = '状态断言';
    body = rule.expr;
  } else if (rule.type === 'tool_used') {
    label = '工具调用';
    body = `${rule.name}${rule.min_calls ? ` 至少调用 ${rule.min_calls} 次` : ' 必须调用'}`;
  } else if (rule.type === 'order_of') {
    label = '调用顺序';
    body = (rule.tools || []).join(' → ');
  } else {
    body = JSON.stringify(rule, null, 2);
  }
  return `<article class="criterion"><span class="criterion-index">${String(index + 1).padStart(2, '0')}</span><div><span class="criterion-type">${esc(label)}</span><code>${esc(body)}</code></div></article>`;
}

function codeBlock(title, value, copyKind) {
  return `<section class="detail-section code-section"><div class="detail-section-head"><h3>${title}</h3><button class="copy-button" data-copy-kind="${copyKind}">复制</button></div><pre>${esc(value || '—')}</pre></section>`;
}

function drawerOverview(task) {
  const result = task.result || {};
  const env = task.environment || {};
  return `<div class="detail-stack">
    <section class="detail-brief"><span>任务目标</span><p>${esc(task.prompt.split('\n').filter(Boolean)[0] || task.title)}</p></section>
    <div class="detail-metrics">
      <div><span>最大步骤</span><strong>${task.budget.max_steps ?? '—'}</strong></div>
      <div><span>Token 预算</span><strong>${compactNumber(task.budget.max_tokens)}</strong></div>
      <div><span>超时</span><strong>${task.budget.timeout_s ? `${task.budget.timeout_s}s` : '—'}</strong></div>
      <div><span>本轮速度</span><strong>${result.speed ? `${result.speed.toFixed(1)} t/s` : '—'}</strong></div>
    </div>
    <section class="detail-section"><h3>环境与来源</h3><dl class="detail-list"><div><dt>环境</dt><dd>${esc(env.name || '—')}</dd></div><div><dt>快照</dt><dd>${esc(env.snapshot || '—')}</dd></div><div><dt>定义文件</dt><dd><code>${esc(task.source)}</code></dd></div><div><dt>难度</dt><dd>${esc(task.difficulty)}</dd></div></dl></section>
    <section class="detail-section"><h3>能力标签</h3><div class="tag-list">${task.tags.map((tag) => `<span>${esc(tag)}</span>`).join('')}</div></section>
    <section class="detail-section"><h3>可用工具 <small>${task.tools.length}</small></h3><div class="tool-list">${task.tools.map((tool, index) => `<span><b>${String(index + 1).padStart(2, '0')}</b>${esc(tool)}</span>`).join('')}</div></section>
  </div>`;
}

function drawerPrompt(task) {
  return `<div class="detail-stack">${codeBlock('系统提示词', task.systemPrompt, 'system')}${codeBlock('任务提示词', task.prompt, 'prompt')}</div>`;
}

function drawerVerifier(task) {
  const judge = task.rubric
    ? `<section class="judge-rubric"><div><span>AI JUDGE</span><strong>语义评分细则</strong></div><p>确定性条件通过后，AI 裁判还会根据以下 rubric 审核无法可靠程序化的证据质量；只有 verdict=pass 才通过。</p><pre>${esc(task.rubric)}</pre></section>`
    : '<section class="judge-rubric is-disabled"><div><span>DETERMINISTIC ONLY</span><strong>本题不使用 AI 裁判</strong></div><p>所有通过条件均由下列确定性 verifier 判定。</p></section>';
  return `<div class="detail-stack"><section class="verifier-note"><strong>严格判定</strong><p>以下 ${task.success.length} 项全部满足才进入 AI rubric（若有）。这里展示任务 YAML 中的真实判定条件。</p></section><div class="criteria-list">${task.success.map(criterionMarkup).join('')}</div>${judge}</div>`;
}

function drawerResult(task) {
  const result = task.result || {};
  const stale = Boolean(result.stale || !task.result);
  const passed = !stale && result.status === 'PASS';
  const displayStatus = stale ? 'STALE' : (result.status || 'NOT RUN');
  return `<div class="detail-stack">
    <section class="result-hero ${stale ? 'is-stale' : passed ? 'is-pass' : 'is-fail'}"><span>${stale ? '↻' : passed ? '✓' : '×'}</span><div><small>FINAL RESULT</small><strong>${displayStatus}</strong><p>${stale ? '题目定义已变化，旧 run 结果不能代表当前版本，需要重新评测。' : passed ? '所有严格判定条件均已满足。' : '任务已结束，但至少一个严格判定条件未满足。'}</p></div></section>
    <div class="detail-metrics result-metrics"><div><span>步骤</span><strong>${result.steps ?? '—'}</strong></div><div><span>累计 Tokens</span><strong>${result.tokens?.toLocaleString() || '—'}</strong></div><div><span>耗时</span><strong>${result.elapsed != null ? `${result.elapsed.toFixed(2)}s` : '—'}</strong></div><div><span>Accounted</span><strong>${result.speed != null ? `${result.speed.toFixed(2)} t/s` : '—'}</strong></div></div>
    <section class="detail-section failure-section"><h3>${stale ? '旧版本证据' : passed ? '判定证据' : '失败证据'}</h3>${passed ? '<p class="pass-evidence">报告未记录失败项。</p>' : `<pre>${esc(result.failure || (stale ? '尚未运行当前题目版本' : '报告中没有可用证据'))}</pre>`}</section>
    <section class="detail-section"><h3>运行上下文</h3><dl class="detail-list"><div><dt>模型</dt><dd>${esc(result.model || '—')}</dd></div><div><dt>Run</dt><dd>${esc(bench.run || '—')}</dd></div><div><dt>并发</dt><dd>${bench.jobs ?? '—'} workers</dd></div><div><dt>数据来源</dt><dd><code>${esc(bench.generatedFrom || '—')}</code></dd></div></dl></section>
  </div>`;
}

function renderDrawer() {
  const task = taskById.get(activeTaskId);
  if (!task) return;
  const type = categoryFor(task);
  const result = task.result || {};
  document.querySelector('#drawerEyebrow').textContent = `${type.label} / ${task.difficulty.toUpperCase()}`;
  document.querySelector('#drawerTitle').textContent = task.title;
  document.querySelector('#drawerTaskId').textContent = task.id;
  document.querySelector('#criteriaCount').textContent = task.rubric ? `${task.success.length}+AI` : task.success.length;
  const status = taskStatus(task);
  document.querySelector('#drawerStatusline').innerHTML = `<span class="type-tag ${type.color}">${type.label}</span><span class="status-text ${status.toLowerCase()}">${status}</span><span>${result.steps ?? '—'} steps</span><span>${compactNumber(result.tokens)} tokens</span><span>${result.elapsed != null ? `${result.elapsed.toFixed(1)}s` : '—'}</span>`;
  document.querySelectorAll('[data-detail-tab]').forEach((button) => button.classList.toggle('active', button.dataset.detailTab === activeDetailTab));
  const renderer = { overview: drawerOverview, prompt: drawerPrompt, verifier: drawerVerifier, result: drawerResult }[activeDetailTab];
  document.querySelector('#drawerBody').innerHTML = renderer(task);
  document.querySelectorAll('[data-copy-kind]').forEach((button) => button.addEventListener('click', () => {
    const value = button.dataset.copyKind === 'system' ? task.systemPrompt : task.prompt;
    copyText(value, button);
  }));
  const index = tasks.findIndex((item) => item.id === activeTaskId);
  document.querySelector('#previousTask').disabled = index <= 0;
  document.querySelector('#nextTask').disabled = index >= tasks.length - 1;
}

function openTask(taskId, updateHash = true) {
  if (!taskById.has(taskId)) return;
  const activeView = document.querySelector('.view.active');
  viewBeforeDrawer = activeView?.id.replace('view-', '') || viewBeforeDrawer;
  activeTaskId = taskId;
  activeDetailTab = 'overview';
  renderDrawer();
  const layer = document.querySelector('#taskDrawer');
  layer.classList.add('open');
  layer.setAttribute('aria-hidden', 'false');
  document.body.classList.add('drawer-open');
  if (updateHash) history.pushState({ taskId }, '', `#task=${encodeURIComponent(taskId)}`);
  requestAnimationFrame(() => document.querySelector('.drawer-close').focus());
}

function closeDrawer(updateHash = true) {
  const layer = document.querySelector('#taskDrawer');
  if (!layer.classList.contains('open')) return;
  layer.classList.remove('open');
  layer.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('drawer-open');
  activeTaskId = null;
  if (updateHash) history.pushState({ view: viewBeforeDrawer }, '', `#${viewBeforeDrawer}`);
}

function moveTask(offset) {
  const index = tasks.findIndex((task) => task.id === activeTaskId);
  const next = tasks[index + offset];
  if (!next) return;
  activeTaskId = next.id;
  renderDrawer();
  history.replaceState({ taskId: next.id }, '', `#task=${encodeURIComponent(next.id)}`);
  document.querySelector('#drawerBody').scrollTop = 0;
}

async function copyText(value, button) {
  try {
    await navigator.clipboard.writeText(value);
  } catch (_) {
    const area = document.createElement('textarea');
    area.value = value;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  button.textContent = '已复制';
  setTimeout(() => { button.textContent = '复制'; }, 1300);
}

function toast(message) {
  const element = document.querySelector('#toast');
  clearTimeout(toastTimer);
  element.textContent = message;
  element.classList.add('show');
  toastTimer = setTimeout(() => element.classList.remove('show'), 3200);
}

function apiRequest(path, options = {}) {
  return fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  }).then(async (response) => {
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.error || `Runner API 返回 ${response.status}`);
    return payload;
  });
}

function setRunnerService(online, label = '') {
  const service = document.querySelector('#runnerService');
  service.classList.toggle('offline', !online);
  service.querySelector('b').textContent = online ? (label || 'READY') : 'OFFLINE';
  if (!online) setRunStatusChip('offline');
  renderNotices();
}

function openRunDialog(scope = null, boardTasks = null) {
  pendingRunScope = typeof scope === 'string' ? scope : null;
  if (Array.isArray(boardTasks)) boardLaunchTasks = boardTasks.slice();
  const layer = document.querySelector('#runDialog');
  layer.classList.add('open');
  layer.setAttribute('aria-hidden', 'false');
  document.body.classList.add('drawer-open');
  loadRunner();
}

function closeRunDialog() {
  const layer = document.querySelector('#runDialog');
  layer.classList.remove('open');
  layer.setAttribute('aria-hidden', 'true');
  if (!document.querySelector('#taskDrawer').classList.contains('open')) document.body.classList.remove('drawer-open');
}

async function loadRunner() {
  const health = document.querySelector('#runnerHealth');
  health.className = 'runner-health';
  health.innerHTML = '<span class="spinner"></span><div><b>正在连接本地 Runner</b><small>检查 API 和任务目录…</small></div>';
  document.querySelector('#runForm').hidden = true;
  try {
    const [config, current] = await Promise.all([apiRequest('/api/config'), apiRequest('/api/runs/current?since=0')]);
    runConfig = config;
    setRunnerService(true, current.status && current.status !== 'idle' ? current.status.toUpperCase() : 'READY');
    populateRunForm(config);
    health.hidden = true;
    if (current.id && current.status !== 'idle') showRunMonitor(current, true);
    else {
      if (pendingRunScope && document.querySelector(`#runScope option[value="${CSS.escape(pendingRunScope)}"]`)) {
        document.querySelector('#runScope').value = pendingRunScope;
        updateLaunchSummary();
      }
      showRunSetup();
    }
  } catch (error) {
    setRunnerService(false);
    document.querySelector('#runSetup').hidden = false;
    document.querySelector('#runMonitor').hidden = true;
    health.hidden = false;
    health.className = 'runner-health error';
    health.innerHTML = `<div><b>本地 Runner 未连接</b><small>${esc(error.message)}。请使用 python3 web_server.py 启动本页面。</small></div>`;
  }
}

function populateRunForm(config) {
  const modelName = document.querySelector('#runModelName');
  if (!modelName.value) {
    const remembered = localStorage.getItem('tracebench-model-name');
    modelName.value = remembered || config.models[0]?.name || '';
    modelName.dataset.automatic = remembered ? 'false' : 'true';
    // 一并记住这个名字当时配的是哪个连接配置：只记名字的话，下次换配置（比如端点点到
    // 小米）名字还留着上一个本地模型，结果就挂到别人名下了——2026-09-24 真踩过。
    const rememberedProfile = localStorage.getItem('tracebench-model-profile') || '';
    if (remembered) modelName.dataset.rememberedProfile = rememberedProfile;
  }
  const scopeSelect = document.querySelector('#runScope');
  // 看板排队作为第一个范围：从看板上按"跑这 N 题"进来时只有它，直接按发起按钮进来时
  // 队列为空就不显示这一项，免得选到一个 0 题的范围。
  const boardOption = boardLaunchTasks.length
    ? `<option value="board">看板排队 · ${boardLaunchTasks.length} 道</option>` : '';
  const categories = Object.entries(config.categories).map(([category, count]) => `<option value="${esc(category)}">${esc(category)} · ${count} 道</option>`).join('');
  const taskOptions = config.tasks.map((task) => `<option value="${esc(task.id)}">${esc(task.id)} · ${esc(task.title)}</option>`).join('');
  const bankOptions = (config.banks || []).map((bank) => `<option value="bank:${esc(bank.name)}">题库 ${esc(bank.name)} · ${bank.count} 道</option>`).join('');
  scopeSelect.innerHTML = `${boardOption}<option value="all">全部任务 · ${config.tasks.length} 道</option>${bankOptions ? `<optgroup label="题库">${bankOptions}</optgroup>` : ''}<optgroup label="按类别">${categories}</optgroup><optgroup label="单道任务">${taskOptions}</optgroup>`;
  fillProfileSelect();
  document.querySelector('.cloud-field').hidden = runLocation() === 'local';
  fillJudgeSelect(config);
  document.querySelector('#runForm').hidden = false;
  applyProfileDefaults();
  updateLaunchSummary();
}

// ---------- 裁判档案（可选的尺子）----------
// 裁判从一个勾选框变成"多把尺 + 不启用"：默认选中表里 judge.default 那一档
// （现在是 mimo-flash），缺密钥的档位直接 disable，一个都不可用时自动落到「不启用」。
// 选中的名字直接发给服务端："哪一把尺"必须是显式事实，不能靠上一轮的默认值。
function fillJudgeSelect(config) {
  const select = document.querySelector('#runJudge');
  const hint = document.querySelector('#judgeHint');
  const note = document.querySelector('#judgeNote');
  const judges = config.judges || [];
  const remembered = localStorage.getItem('tracebench-judge') || '';
  const usable = (name) => {
    const item = judges.find((judges_item) => judges_item.name === name);
    return Boolean(item && item.keyReady);
  };
  // 默认项优先级：上次用过且还能用 → 表里的 default → 第一个密钥就绪的 → 不启用。
  let selected = judges.some((item) => item.name === remembered && item.keyReady) ? remembered : '';
  if (!selected && usable(config.judgeDefault)) selected = config.judgeDefault;
  if (!selected) selected = (judges.find((item) => item.keyReady) || {}).name || 'off';
  select.innerHTML = [
    ...judges.map((item) => `<option value="${esc(item.name)}"${item.keyReady ? '' : ' disabled'}>${esc(item.name)} \u00b7 ${esc(item.apiModel)}${item.isDefault ? ' \u00b7 \u9ed8\u8ba4' : ''}${item.keyReady ? '' : ' \u00b7 \u7f3a\u5bc6\u94a5'}</option>`),
    '<option value="off">\u4e0d\u542f\u7528\uff08\u5e26 rubric \u7684\u9898\u8bb0\u672a\u8bc4\uff09</option>',
  ].join('');
  select.value = selected;
  updateJudgeHint();
}

function judgeSelection() {
  const value = document.querySelector('#runJudge').value;
  return value === 'off' ? null : value;
}

function updateJudgeHint() {
  const select = document.querySelector('#runJudge');
  if (!select || !runConfig) return;
  const hint = document.querySelector('#judgeHint');
  const note = document.querySelector('#judgeNote');
  // 旧的服务端进程不认识裁判档位（config 里没有 judges）：那时下拉框里必然只剩「不启用」。
  // 与其让它看起来像"裁判功能坏了"，直接把原因写在这行字上——重启 web_server 就恢复。
  if (!Array.isArray(runConfig.judges)) {
    select.disabled = true;
    hint.textContent = '服务端未更新';
    note.textContent = '当前运行的 web_server 还是旧版进程，拿不到裁判档位清单，所以只能选「不启用」。'
      + '重启 web_server（python3 web_server.py --port 4173）后刷新本页即可挑尺子；'
      + '急着判分也可以先走命令行：python3 cli.py run … --judge mimo-flash。';
    return;
  }
  select.disabled = false;
  const chosen = judgeSelection();
  const item = (runConfig.judges || []).find((row) => row.name === chosen);
  hint.textContent = item ? `${item.endpoint}${item.isDefault ? ' \u00b7 \u9ed8\u8ba4\u6863' : ''}` : '\u672c\u8f6e\u4e0d\u8dd1\u88c1\u5224';
  note.textContent = item
    ? `\u53ea\u5bf9\u5e26 rubric \u7684\u9898\u751f\u6548\uff1b${item.keySource}${item.keyReady ? ' \u5df2\u5c31\u7eea' : ' \u7f3a\u5bc6\u94a5'}\u3002\u9898\u9762\u9489\u4e86\u5176\u4ed6\u5c3a\u5b50\u7684\u9898\u4f1a\u8bb0\u672a\u8bc4\u3002`
    : '\u5173\u95ed\u88c1\u5224\u540e\uff0c\u5e26 rubric \u7684\u9898\u53ea\u8dd1\u786e\u5b9a\u6027\u5224\u636e\uff0c\u5224\u5b9a\u8bb0\u300c\u672a\u8bc4\u300d\u3001\u4e0d\u8fdb\u901a\u8fc7\u7387\u5206\u6bcd\u3002';
}

// ---------- 外部端点（发车面板三件套：地址 / 密钥 / 协议）----------
// 端点性质决定并发天花板与服务端校验，所以这三个函数是 renderJobOptions 与
// updateLaunchSummary 共用的唯一真相——面板里看到啥，就是发出去啥。
// ---------- 运行位置：本地 / 云端 ----------
// 本地与云端唯一的差别就是"用哪套 url + key"，所以这里做成一个硬开关：
// 选本地时不显示、也绝不发送云端那三个字段；选云端时只列外部连接配置。
// 2026-09-24 那次整轮 50 个 FAIL 就是因为端点写成了小米、model id 还留着本地占位。
function runLocation() {
  return document.querySelector('input[name="location"]:checked')?.value || 'local';
}

function profilesForLocation(location = runLocation()) {
  const all = runConfig?.models || [];
  return all.filter((model) => (model.kind === 'external') === (location === 'cloud'));
}

// 连接配置下拉只列当前这一侧的配置，切换位置时整套换掉——同一个下拉里混放本地与云端，
// 才是"混了"的根源。
function fillProfileSelect() {
  const location = runLocation();
  const select = document.querySelector('#runModel');
  const list = profilesForLocation(location);
  const previous = select.value;
  select.innerHTML = list.map((model) => `<option value="${esc(model.name)}">${esc(model.name)} · ${esc(model.endpoint)}</option>`).join('');
  const remembered = localStorage.getItem(`tracebench-profile-${location}`) || '';
  const wanted = list.some((model) => model.name === previous) ? previous
    : (list.some((model) => model.name === remembered) ? remembered : list[0]?.name || '');
  select.value = wanted;
  if (location === 'local') {
    // 本地这轮从这里就断干净：地址、密钥、协议一律清空，后面也没机会被带进 payload。
    document.querySelector('#runBaseUrl').value = '';
    document.querySelector('#runApiKey').value = '';
    document.querySelector('#runWireApi').value = '';
  }
  document.querySelector('.cloud-field').hidden = location === 'local';
  document.querySelector('#locationHint').textContent = location === 'local'
    ? '本地推理端点：并发上限 4，连的是回环地址。要跑云端 API 就切到右边。'
    : '云端 API：并发上限 16，地址与密钥都取自云端连接配置（密钥指向 .env 里的变量）。';
  return list;
}

function setRunLocation() {
  const list = fillProfileSelect();
  if (!list.length) {
    // 这一侧一个配置都没有：别静默用对侧的配置，直接说清楚去哪儿加。
    document.querySelector('#runError').textContent = runLocation() === 'cloud'
      ? 'models.yaml 里没有外部（云端）连接配置；先在 models.yaml 加一个云端配置，或在下面手填端点地址与密钥。'
      : 'models.yaml 里没有本地连接配置。';
  } else {
    document.querySelector('#runError').textContent = '';
    document.querySelector('#runApiModel').value = list[0].apiModel || 'local';
  }
  applyProfileDefaults();
  renderJobOptions(effectiveCeiling());
  updateLaunchSummary();
}

function endpointHost(value) {
  try { return new URL(value).hostname.toLowerCase(); } catch { return ''; }
}

function endpointIsExternal(value) {
  const host = endpointHost(value);
  if (!host) return false;
  return !(host === 'localhost' || host === '::1' || host === '0.0.0.0' || host.startsWith('127.'));
}

function selectedProfile() {
  const profile = document.querySelector('#runModel').value;
  return (runConfig?.models || []).find((item) => item.name === profile) || null;
}

function cloudOverrideUrl() {
  // 本地模式下这两个框虽然清空了，还是把"位置"也当成一道前提写进来：只要不是云端，
  // 手填地址一律不看。
  return runLocation() === 'cloud' ? document.querySelector('#runBaseUrl').value.trim() : '';
}

function effectiveEndpoint() {
  return cloudOverrideUrl() || selectedProfile()?.endpoint || '';
}

function effectiveCeiling() {
  const typed = cloudOverrideUrl();
  if (typed) return endpointIsExternal(typed) ? 16 : 4;
  return selectedProfile()?.maxJobs || 4;
}

// 发车闸门：面板自己先拦一次，服务端还会再拦一次——一个写错的 model id 曾让整轮
// 50 道题一起 400，还被记成 50 个 FAIL 进了榜，这种代价不值得"先跑跑看"。
let launchBlockReason = '';

function apiModelFieldState() {
  const field = document.querySelector('#runApiModel');
  const value = field.value.trim();
  const placeholder = !value || ['local', 'default'].includes(value.toLowerCase());
  return { field, value, placeholder };
}

function updateEndpointState() {
  const location = runLocation();
  const info = selectedProfile();
  const typed = cloudOverrideUrl();
  const endpoint = effectiveEndpoint();
  const external = endpointIsExternal(endpoint);
  const typedKey = location === 'cloud' ? document.querySelector('#runApiKey').value.trim() : '';
  const wire = location === 'cloud' ? document.querySelector('#runWireApi').value : '';
  const model = apiModelFieldState();
  const notes = [];

  // 端点命中已知连接配置时把 model id 一起带进来：临时指一个端点不该还要人去查模型名。
  if (typed) {
    const host = endpointHost(typed);
    const match = host && (runConfig?.models || []).find((item) => endpointHost(item.endpoint) === host);
    const inherited = model.value === (info?.apiModel || '') || model.placeholder;
    if (match && match.apiModel && inherited) {
      model.field.value = match.apiModel;
      notes.push(`端点命中连接配置「${match.name}」，model id 已填 ${match.apiModel}`);
    }
  }
  const model2 = apiModelFieldState();
  const mismatch = external && model2.placeholder;
  model2.field.closest('.run-field').classList.toggle('warn', mismatch);
  document.querySelector('#modelEndpoint').classList.toggle('warn', mismatch);

  document.querySelector('#wireApiHint').textContent = info
    ? `连接配置现在用 ${info.wireApi || 'chat'}；改了要确认对面真实现了这个口` : '';
  const parts = [`运行位置 ${location === 'cloud' ? '云端' : '本地'} · 本次端点 ${endpoint || '—'}`];
  if (!endpoint) {
    launchBlockReason = '这一侧还没有连接配置：先在 models.yaml 里补一个';
  }
  if (external) {
    // 关键前提说在发车前：外部端点没有密钥就是整轮 401，而每个 401 都会被记成一次未评。
    if (typedKey) parts.push('密钥走面板（仅本次运行）');
    else if (info?.keyReady) parts.push(`密钥从 ${info.keySource} 读`);
    else {
      parts.push('缺密钥：请填 API Key，或把密钥写进仓库根的 .env 再重启服务');
      launchBlockReason = launchBlockReason || `云端端点 ${endpointHost(endpoint)} 还没有密钥`;
    }
  } else if (location === 'cloud') {
    // 选了云端却指着回环地址：这是把两套东西混了，必须说出来。
    parts.push('这个地址是回环地址，云端那侧不该用它');
    launchBlockReason = launchBlockReason || '选了云端，但地址是本地回环地址：换成云端连接配置或外部地址';
  }
  if (mismatch) {
    parts.push(`API Model ID 还是「${model2.value || '空'}」：外部端点必须填对面真实的模型名（小米是 mimo-v2.6-pro）。`);
  }
  if (wire || typed) {
    parts.push('面板覆盖会写进这次 run 的 manifest，报告里能看到');
  }
  document.querySelector('#externalState').textContent = notes.concat(parts).join(' · ');
  if (mismatch) {
    launchBlockReason = `外部端点要用真实的 API Model ID，不能是「${model2.value || '空'}」`;
  }
  syncLaunchButton();
}

function syncLaunchButton() {
  const button = document.querySelector('#launchButton');
  if (!button || button.dataset.busy === 'true') return;
  button.disabled = Boolean(launchBlockReason);
  button.title = launchBlockReason || '';
}

function renderJobOptions(ceiling) {
  // 并发上限由端点决定（本地 4 / 外部 16），所以选项是渲染出来的，不是写死 1–4。
  // 默认值取看板滑条最后一次设定，夹到当前端点的上限内。
  const limit = Math.min(Math.max(1, Number(ceiling) || 4), 16);
  const remembered = Number(localStorage.getItem('tracebench-jobs')) || 0;
  const wanted = Math.min(remembered || Math.min(4, limit), limit);
  const box = document.querySelector('#jobOptions');
  box.innerHTML = Array.from({ length: limit }, (_, index) => index + 1)
    .map((jobs) => `<label><input type="radio" name="jobs" value="${jobs}"${jobs === wanted ? ' checked' : ''}><span>${jobs}</span></label>`)
    .join('');
  const hint = document.querySelector('#jobsHint');
  if (hint) hint.textContent = `同一时刻最多 ${limit} 道题（这个端点的上限）；跑起来之后也能在看板上热调。`;
}

function applyProfileDefaults() {
  if (!runConfig) return;
  const profile = document.querySelector('#runModel').value;
  const modelInfo = runConfig.models.find((item) => item.name === profile);
  if (!modelInfo) return;
  const modelName = document.querySelector('#runModelName');
  // 模型名称跟着连接配置走：这三个字段是一件事，只改一个会让结果标错名字。
  // 实测踩过：端点点到小米，名字还是上一个本地模型（Qwopus…gguf），于是小米那轮的
  // 结果挂在了一个本地模型名下。只有用户手写的名字才不覆盖。
  const derived = modelName.dataset.derivedFromProfile || '';
  const remembered = modelName.dataset.rememberedProfile || '';
  const followsProfile = !modelName.value || modelName.dataset.automatic === 'true'
    || Boolean(derived) || (Boolean(remembered) && remembered !== profile);
  if (followsProfile && modelName.value !== modelInfo.name) {
    modelName.value = modelInfo.name;
    modelName.dataset.automatic = 'true';
    delete modelName.dataset.rememberedProfile;
  }
  modelName.dataset.derivedFromProfile = modelInfo.name;
  const previousJobs = Number(document.querySelector('input[name="jobs"]:checked')?.value);
  renderJobOptions(effectiveCeiling());
  if (previousJobs) {
    const keep = document.querySelector(`input[name="jobs"][value="${previousJobs}"]`);
    if (keep) keep.checked = true;
  }
  document.querySelector('#runApiModel').value = modelInfo.apiModel || 'local';
  document.querySelector('#runMaxTokens').value = modelInfo.maxOutputTokens || 16384;
  document.querySelector('#runRequestTimeout').value = modelInfo.timeoutSeconds || 300;
  updateEndpointState();
}

function selectedTaskCount() {
  const scope = document.querySelector('#runScope').value;
  if (!runConfig) return 0;
  if (scope === 'board') return boardLaunchTasks.length;
  if (scope === 'all') return runConfig.tasks.length;
  if (runConfig.categories[scope]) return runConfig.categories[scope];
  return 1;
}

function updateLaunchSummary() {
  if (!runConfig) return;
  const profile = document.querySelector('#runModel').value;
  const modelName = document.querySelector('#runModelName').value.trim() || '未命名模型';
  const apiModel = document.querySelector('#runApiModel').value.trim() || 'local';
  const maxTokens = document.querySelector('#runMaxTokens').value;
  const requestTimeout = document.querySelector('#runRequestTimeout').value;
  const scope = document.querySelector('#runScope').value;
  const jobs = document.querySelector('input[name="jobs"]:checked')?.value || '4';
  const modelInfo = runConfig.models.find((item) => item.name === profile);
  const baseUrl = cloudOverrideUrl();
  const apiKey = runLocation() === 'cloud' ? document.querySelector('#runApiKey').value.trim() : '';
  const wireApi = runLocation() === 'cloud' ? document.querySelector('#runWireApi').value : '';
  const endpoint = effectiveEndpoint();
  document.querySelector('#modelEndpoint').textContent = endpoint
    ? `${endpoint}${baseUrl ? '（面板覆盖）' : ''}${endpointIsExternal(endpoint) ? ' · 外部 API' : ''}`
    : '';
  document.querySelector('#scopeHint').textContent = scope === 'all' ? '运行完整 hard suite'
    : scope === 'board' ? `看板排队清单里的 ${boardLaunchTasks.length} 道，发车即出队`
    : `${selectedTaskCount()} 道任务`;
  document.querySelector('#launchSummary').textContent = `${selectedTaskCount()} tasks × ${modelName} · ${runLocation() === 'cloud' ? '云端' : '本地'} · ${jobs} concurrent · ${Number(maxTokens || 0).toLocaleString()} max · ${requestTimeout}s timeout${baseUrl || wireApi ? ' · 面板端点覆盖' : ''}`;
  const scopeArg = scope === 'all' ? '' : scope === 'board' ? `--tasks ${boardLaunchTasks.join(',')} ` : `--tasks ${scope} `;
  // 命令预览里密钥只能出现变量名：把值打进这段文本，它就会跟着截图、聊天记录、日志一起跑出去。
  const urlArg = baseUrl ? `--base-url ${baseUrl} ` : '';
  const wireArg = wireApi ? `--wire-api ${wireApi} ` : '';
  const keyArg = apiKey ? `--api-key-env $TRACEBENCH_API_KEY ` : '';
  const judgeChoice = typeof judgeSelection === 'function' ? judgeSelection() : null;
  const judgeArg = judgeChoice ? `--judge ${judgeChoice} ` : '--no-judge ';
  document.querySelector('#launchCommand').textContent = `python3 cli.py run --models ${profile} --model-label ${modelName} --api-model ${apiModel} ${urlArg}${wireArg}${keyArg}--max-output-tokens ${maxTokens} --request-timeout-s ${requestTimeout} ${scopeArg}--jobs ${jobs} ${judgeArg}`.trim();
}

function showRunSetup() {
  clearTimeout(runPollTimer);
  document.querySelector('#launchButton').dataset.busy = 'false';
  syncLaunchButton();
  document.querySelector('#runSetup').hidden = false;
  document.querySelector('#runMonitor').hidden = true;
  document.querySelector('#runnerHealth').hidden = true;
  document.querySelector('#runForm').hidden = false;
  document.querySelector('#runError').textContent = '';
  document.querySelector('#runDialogTitle').textContent = '发起新评测';
}

async function launchRun(event) {
  event.preventDefault();
  const button = document.querySelector('#launchButton');
  const error = document.querySelector('#runError');
  const scope = document.querySelector('#runScope').value;
  const payload = {
    profile: document.querySelector('#runModel').value,
    modelName: document.querySelector('#runModelName').value.trim(),
    apiModel: document.querySelector('#runApiModel').value.trim(),
    maxOutputTokens: Number(document.querySelector('#runMaxTokens').value),
    requestTimeoutSeconds: Number(document.querySelector('#runRequestTimeout').value),
    scope,
    jobs: Number(document.querySelector('input[name="jobs"]:checked').value),
    judge: judgeSelection(),
  };
  // 看板排队走 /api/board/launch：题号由服务端从排队泳道现取，并且发车后自动出队，
  // 所以这里不把 scope 传过去（"board" 不是服务端认识的范围）。
  const fromBoard = scope === 'board';
  if (fromBoard) delete payload.scope;
  // 外部端点 + 占位 model id 在服务端会被拒；在这里先给一句能看懂的话，别让整轮白跑。
  if (launchBlockReason) {
    error.textContent = launchBlockReason;
    button.dataset.busy = 'false';
    button.disabled = false;
    button.innerHTML = '<span>▶</span> 启动评测';
    return;
  }
  // 外部端点覆盖：只在这三个字段有值时进 payload，空着就是"完全按连接配置来"。
  // 密钥只进这一次 POST 的请求体，服务端把它注入子进程环境；不落 localStorage。
  // 位置闸门：本地那轮只带连接配置，绝不把云端的地址或密钥发出去（哪怕框里还剩东西）。
  const baseUrl = cloudOverrideUrl();
  const apiKey = runLocation() === 'cloud' ? document.querySelector('#runApiKey').value.trim() : '';
  const wireApi = runLocation() === 'cloud' ? document.querySelector('#runWireApi').value : '';
  if (baseUrl) payload.baseUrl = baseUrl;
  if (apiKey) payload.apiKey = apiKey;
  if (wireApi) payload.wireApi = wireApi;
  localStorage.setItem(`tracebench-profile-${runLocation()}`, payload.profile);
  localStorage.setItem('tracebench-jobs', String(payload.jobs));
  // 记住这一轮用的尺子（含「不启用」）：连续跑好几轮时不该每次重新接一遍。
  localStorage.setItem('tracebench-judge', payload.judge || 'off');
  button.dataset.busy = 'true';
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> 正在创建评测…';
  error.textContent = '';
  try {
    const response = await apiRequest(fromBoard ? '/api/board/launch' : '/api/runs',
                                      { method: 'POST', body: JSON.stringify(payload) });
    const state = fromBoard ? response.run : response;
    localStorage.setItem('tracebench-model-name', payload.modelName);
    localStorage.setItem('tracebench-model-profile', payload.profile);
    if (fromBoard) {
      boardLaunchTasks = [];
      if (window.Board) window.Board.applyPayload(response.board);
    }
    runLogSequence = 0;
    document.querySelector('#runLog').textContent = '';
    showRunMonitor(state, true);
    toast(`${state.id} 已启动 · ${state.jobs} workers`);
  } catch (reason) {
    error.textContent = reason.message;
  } finally {
    button.dataset.busy = 'false';
    button.innerHTML = '<span>▶</span> 启动评测';
    syncLaunchButton();
  }
}

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}` : `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function showRunMonitor(state, resetLogs = false) {
  document.querySelector('#runSetup').hidden = true;
  document.querySelector('#runMonitor').hidden = false;
  document.querySelector('#runDialogTitle').textContent = '评测运行台';
  if (resetLogs) {
    runLogSequence = 0;
    document.querySelector('#runLog').textContent = '';
  }
  updateRunMonitor(state);
}

function appendRunLogs(logs = []) {
  if (!logs.length) return;
  const log = document.querySelector('#runLog');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 45;
  const lines = logs.map((item) => item.line).join('\n');
  log.textContent += `${log.textContent ? '\n' : ''}${lines}`;
  if (log.textContent.length > 450000) log.textContent = log.textContent.slice(-350000);
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function updateRunMonitor(state) {
  const status = state.status || 'idle';
  const active = ['starting', 'running', 'pausing', 'paused', 'cancelling', 'finalizing'].includes(status);
  const cancelable = ['starting', 'running', 'pausing', 'paused'].includes(status);
  const statusLabel = { starting: 'STARTING', running: 'RUNNING', pausing: 'PAUSING', paused: 'PAUSED', cancelling: 'STOPPING', finalizing: 'REPORTING', completed: 'COMPLETE', failed: 'FAILED', cancelled: 'CANCELLED' }[status] || status.toUpperCase();
  const statusElement = document.querySelector('#monitorStatus');
  statusElement.className = `monitor-status ${status}`;
  statusElement.innerHTML = `<i></i> ${statusLabel}`;
  document.querySelector('#monitorRunId').textContent = state.id || '—';
  document.querySelector('#monitorClock').textContent = formatClock(state.elapsedSeconds);
  document.querySelector('#monitorCompleted').textContent = `${state.completed || 0} / ${state.total || 0}`;
  document.querySelector('#monitorPassed').textContent = state.passed || 0;
  document.querySelector('#monitorFailed').textContent = state.failed || 0;
  // "并发"显示实盘生效值：热调之后 state.jobs 还是发车时那个数，只有闸回写的
  // concurrency.jobs 才是当前真正在跑的题数。
  document.querySelector('#monitorJobs').textContent = state.concurrency?.jobs ?? state.jobs ?? '—';
  document.querySelector('#runProgressBar').style.width = `${state.total ? Math.min(100, (state.completed / state.total) * 100) : 0}%`;
  document.querySelector('#activeTasks').textContent = state.currentTasks?.length ? state.currentTasks.join(' · ') : (active ? '等待 worker 接收任务…' : '没有运行中的任务');
  document.querySelector('#monitorOutput').textContent = state.outputDir || '';
  appendRunLogs(state.logs);
  runLogSequence = Math.max(runLogSequence, state.lastLogSequence || 0);
  document.querySelector('#cancelRun').hidden = !cancelable;
  document.querySelector('#cancelRun').disabled = status === 'cancelling';
  document.querySelector('#newRun').hidden = active;
  document.querySelector('#refreshReport').hidden = !state.reportReady;
  const runButton = document.querySelector('#rerunButton');
  runButton.innerHTML = active ? '<span class="spinner"></span> 查看评测进度' : '<span>＋</span> 发起新评测';
  setRunnerService(true, active ? statusLabel : 'READY');
  setRunStatusChip(status);
  setConcurrencyNote(state.concurrency?.jobs ?? state.jobs);
  clearTimeout(runPollTimer);
  if (active) runPollTimer = setTimeout(pollRun, 1000);
  else if (status === 'completed') loadHistory(true);
}

async function pollRun() {
  try {
    const state = await apiRequest(`/api/runs/current?since=${runLogSequence}`);
    updateRunMonitor(state);
  } catch (error) {
    clearTimeout(runPollTimer);
    setRunnerService(false);
    appendRunLogs([{ line: `[web:error] 状态连接中断：${error.message}` }]);
  }
}

async function syncRunnerState() {
  try {
    const state = await apiRequest('/api/runs/current?since=0');
    setRunnerService(true, state.status !== 'idle' ? state.status.toUpperCase() : 'READY');
    // 顶栏芯片以前写死 COMPLETE，现在跟着 Runner 真实状态走。
    setRunStatusChip(state.status || 'idle');
    renderEnvelope(state);
    if (['starting', 'running', 'pausing', 'paused', 'cancelling', 'finalizing'].includes(state.status)) {
      runLogSequence = state.lastLogSequence || 0;
      updateRunMonitor({ ...state, logs: [] });
    }
  } catch (_) {
    setRunnerService(false);
  }
}

async function cancelRun() {
  if (!window.confirm('确定停止当前评测？已完成任务的结果会保留。')) return;
  try {
    const state = await apiRequest('/api/runs/current/cancel', { method: 'POST', body: '{}' });
    updateRunMonitor(state);
  } catch (error) {
    toast(`停止失败：${error.message}`);
  }
}

function showView(view, updateHash = true) {
  const target = document.querySelector(`#view-${view}`);
  if (!target) return;
  closeDrawer(false);
  document.querySelectorAll('.view').forEach((item) => item.classList.toggle('active', item === target));
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  if (updateHash) history.pushState({ view }, '', `#${view}`);
  if (view === 'history' || view === 'leaderboard') loadHistory();
  if (view === 'board' && window.Board) window.Board.ensureLoaded();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// 合并掉的页面保留旧入口：老书签直接落到承载它的新视图，不白屏。
const LEGACY_VIEWS = { matrix: 'runs', memory: 'memory-loop', liberty: null, settings: null };

function applyCategoryFilter(category) {
  filters.category = category;
  document.querySelectorAll('[data-category]').forEach((item) => item.classList.toggle('selected', item.dataset.category === category));
  renderRuns();
}

function routeFromHash() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith('task=')) {
    openTask(hash.slice(5), false);
    return;
  }
  if (Object.prototype.hasOwnProperty.call(LEGACY_VIEWS, hash)) {
    const drill = LEGACY_VIEWS[hash];
    showView(drill ? 'runs' : 'overview', false);
    if (drill) applyCategoryFilter(drill);
    toast(`「${hash}」页已并入${drill ? '任务明细的维度筛选' : '总览'}。`);
    return;
  }
  if (hash && document.querySelector(`#view-${hash}`)) showView(hash, false);
  else if (!hash) showView('overview', false);
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
document.querySelectorAll('[data-view-link]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.viewLink)));
document.querySelector('#runSearch').addEventListener('input', (event) => { filters.search = event.target.value; renderRuns(); });
document.querySelectorAll('#statusFilters button').forEach((button) => button.addEventListener('click', () => {
  filters.status = button.dataset.status;
  document.querySelectorAll('#statusFilters button').forEach((item) => item.classList.toggle('selected', item === button));
  renderRuns();
}));
document.querySelector('#filterButton').addEventListener('click', () => {
  const panel = document.querySelector('#filterPanel');
  panel.hidden = !panel.hidden;
  document.querySelector('#filterButton').setAttribute('aria-expanded', String(!panel.hidden));
});
document.querySelector('#clearFilters').addEventListener('click', clearFilters);
document.querySelector('#refreshHistory').addEventListener('click', () => loadHistory(true));
document.querySelectorAll('#rankingTierSwitch [data-tier]').forEach((button) => button.addEventListener('click', () => chooseRankingTier(button.dataset.tier)));
document.querySelectorAll('[data-close-drawer]').forEach((button) => button.addEventListener('click', () => closeDrawer()));
document.querySelectorAll('[data-detail-tab]').forEach((button) => button.addEventListener('click', () => { activeDetailTab = button.dataset.detailTab; renderDrawer(); }));
document.querySelector('#previousTask').addEventListener('click', () => moveTask(-1));
document.querySelector('#nextTask').addEventListener('click', () => moveTask(1));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && document.querySelector('#boardDrawer').classList.contains('open')) {
    if (window.Board) window.Board.closeDrawer();
    return;
  }
  if (event.key === 'Escape' && document.querySelector('#modelDrawer').classList.contains('open')) {
    closeModelDetail();
    return;
  }
  if (event.key === 'Escape' && document.querySelector('#runDialog').classList.contains('open')) {
    closeRunDialog();
    return;
  }
  if (!activeTaskId) return;
  if (event.key === 'Escape') closeDrawer();
  if (event.key === 'ArrowLeft' && !event.target.closest('pre, input, textarea')) moveTask(-1);
  if (event.key === 'ArrowRight' && !event.target.closest('pre, input, textarea')) moveTask(1);
});
window.addEventListener('popstate', routeFromHash);

document.querySelector('#themeButton').addEventListener('click', () => {
  document.body.classList.toggle('light');
  localStorage.setItem('tracebench-theme', document.body.classList.contains('light') ? 'light' : 'dark');
  toast(document.body.classList.contains('light') ? '已切换亮色对照主题' : '已切换信号台暗色主题');
});
document.querySelector('#rerunButton').addEventListener('click', openRunDialog);
// 通知按钮以前弹的是写死文案，现在只列当前 suite 真实存在的异常。
document.querySelector('#notificationButton').addEventListener('click', () => toggleNoticePanel());
document.querySelector('#noticeClose').addEventListener('click', () => toggleNoticePanel(false));
document.querySelector('#noticeBody').addEventListener('click', (event) => {
  const item = event.target.closest('[data-notice-open], [data-notice-status], [data-notice-category]');
  if (!item) return;
  if (item.dataset.noticeOpen) { toggleNoticePanel(false); openTask(item.dataset.noticeOpen); }
  else focusRunsFilter(item.dataset.noticeStatus, item.dataset.noticeCategory);
});
document.addEventListener('click', (event) => {
  const panel = document.querySelector('#noticePanel');
  if (!panel.hidden && !event.target.closest('#noticePanel') && !event.target.closest('#notificationButton')) toggleNoticePanel(false);
});
// 维度信号行 = 原「场景矩阵」的入口：点一下到任务明细并按该维度筛好。
document.querySelector('#overviewSignalList').addEventListener('click', (event) => {
  const row = event.target.closest('[data-signal-category]');
  if (row) focusRunsFilter(null, row.dataset.signalCategory);
});
document.querySelector('#envelopeNewRun').addEventListener('click', () => openRunDialog());
document.querySelectorAll('[data-close-run]').forEach((button) => button.addEventListener('click', closeRunDialog));
document.querySelectorAll('[data-close-model]').forEach((button) => button.addEventListener('click', closeModelDetail));
document.querySelector('#deleteActiveModel').addEventListener('click', () => archiveModel(activeModelName));
document.querySelector('#runForm').addEventListener('submit', launchRun);
document.querySelector('#runModel').addEventListener('change', () => { applyProfileDefaults(); updateLaunchSummary(); });
// 运行位置开关：换的是整套 url + key，所以下拉、并发上限、model id、摘要一起重算。
document.querySelectorAll('input[name="location"]').forEach((radio) => radio.addEventListener('change', setRunLocation));
document.querySelector('#runModelName').addEventListener('input', (event) => {
  event.target.dataset.automatic = 'false';
  delete event.target.dataset.derivedFromProfile;   // 手写了名字就断开跟随后端配置的绑定
  delete event.target.dataset.rememberedProfile;
  updateLaunchSummary();
});
document.querySelector('#runApiModel').addEventListener('input', () => { updateEndpointState(); updateLaunchSummary(); });
// 端点三件套：地址/协议会改并发天花板与校验，所以除了摘要还要重渲并发选项。
document.querySelector('#runBaseUrl').addEventListener('input', () => {
  updateEndpointState();
  const previousJobs = Number(document.querySelector('input[name="jobs"]:checked')?.value);
  renderJobOptions(effectiveCeiling());
  if (previousJobs) {
    const keep = document.querySelector(`input[name="jobs"][value="${previousJobs}"]`);
    if (keep) keep.checked = true;
  }
  updateLaunchSummary();
});
document.querySelector('#runApiKey').addEventListener('input', () => { updateEndpointState(); updateLaunchSummary(); });
document.querySelector('#runWireApi').addEventListener('change', () => { updateEndpointState(); updateLaunchSummary(); });
document.querySelector('#runMaxTokens').addEventListener('input', updateLaunchSummary);
document.querySelector('#runRequestTimeout').addEventListener('input', updateLaunchSummary);
document.querySelector('#runScope').addEventListener('change', updateLaunchSummary);
// 切裁判档位：提示语与命令预览都要跟着变，否则面板上写的和发出去的不是同一把尺。
document.querySelector('#runJudge').addEventListener('change', () => { updateJudgeHint(); updateLaunchSummary(); });
// 并发选项是按端点上限动态渲染的，所以用事件委托绑定，不能只在加载时挑一遍节点。
document.querySelector('#jobOptions').addEventListener('change', updateLaunchSummary);
document.querySelector('#cancelRun').addEventListener('click', cancelRun);
document.querySelector('#newRun').addEventListener('click', showRunSetup);
document.querySelector('#refreshReport').addEventListener('click', () => location.reload());
document.querySelector('#clearLog').addEventListener('click', () => { document.querySelector('#runLog').textContent = ''; });

if (localStorage.getItem('tracebench-theme') === 'light') document.body.classList.add('light');
renderRecent();
renderCategoryFilters();
updateSuiteMeta();
renderRuns();
routeFromHash();
renderEnvelope(null);
syncRunnerState();
loadHistory();
