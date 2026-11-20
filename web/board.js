/* 任务看板：一题一张卡，拖拽换泳道、按难度/实测排序、跑动中热调并发、点开看逐次运行。
 *
 * 依赖 app.js 已经定义的全局：esc / compactNumber / categoryFor / toast / apiRequest /
 * openTask / openRunDialog。这个文件在它之后加载，只往 window.Board 上挂接口，
 * 不去改 app.js 的内部状态（除了 showView 里那一行 ensureLoaded 的回调）。
 *
 * 一条原则贯穿全文件：**前端不自己算泳道**。在跑 / 本轮待跑由服务端从 RunManager
 * 实况推导，已完成只收"对当前题面有有效结论"的，其余没完成的（从没跑过、未评、
 * 被题面变更作废、拖回去重测）都落在排队列——列里有什么，发车就原样跑什么。
 * 这里只负责把拖拽结果作为"意图"提交回去。否则刷新一次页面就会出现两份真相——
 * 那是最难查的 bug。
 */
(() => {
  const LANES = ['queue', 'running', 'waiting', 'done'];
  // 只有这两列能接拖拽：排队是意图、已完成是"从排队名单里减掉"，两者都是可写的状态；
  // 在跑 / 本轮待跑由当前 run 的清单决定，拖进去不会改变任何东西，所以直接拒绝而不是假装成功。
  const DROPPABLE = new Set(['queue', 'done']);
  let state = null;              // 最近一次 /api/board 返回
  let loading = false;
  let polling = null;
  let draggingId = null;
  let openTaskId = null;
  const detailCache = new Map(); // `${runId}::${taskId}` -> 单题运行详情
  const expanded = new Set();    // 当前展开的 attempt key

  const el = (id) => document.querySelector(`#${id}`);
  const laneBox = (lane) => document.querySelector(`.board-cards[data-drop="${lane}"]`);
  const laneIds = (lane) => (state ? state.lanes[lane] || [] : []);
  const cardOf = (id) => (state && state.cards[id]) || null;

  function fmtElapsed(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return `${m}分${r ? `${r}秒` : ''}`;
    return `${Math.floor(m / 60)}时${String(m % 60).padStart(2, '0')}分`;
  }

  function verdictOf(card) {
    if (card.lane === 'waiting') return { key: 'waiting', text: '待跑' };
    const live = card.live;
    if (live && (live.status === 'running' || live.status === 'pending')) {
      return { key: 'running', text: live.status === 'pending' ? 'WAIT' : 'RUN' };
    }
    const last = card.measure && card.measure.last;
    if (!last) return { key: 'none', text: '无数据' };
    if (last.stale) return { key: 'stale', text: 'STALE' };
    if (last.graded === false) return { key: 'ungraded', text: '未评' };
    return last.passed ? { key: 'pass', text: 'PASS' } : { key: 'fail', text: 'FAIL' };
  }

  function cardMarkup(id) {
    const card = cardOf(id);
    if (!card) return '';
    const type = categoryFor(card);
    const measure = card.measure || {};
    const verdict = verdictOf(card);
    const rate = measure.passRate == null ? null : Math.round(measure.passRate * 100);
    const live = card.live;
    const position = card.runPosition
      ? `本轮第 ${card.runPosition}/${card.runTotal ?? '?'} 题` : '';
    const liveText = card.lane === 'waiting'
      ? `<div class="board-card-live waiting">${esc(position || '已排进这一轮')} · 还没开跑</div>`
      : live && live.status === 'running'
        ? `<div class="board-card-live">正在跑 · ${esc(position || `第 ${live.index ?? '?'} 题`)}${card.liveSeconds != null ? ` · 已跑 ${fmtElapsed(card.liveSeconds)}` : ''}</div>`
        : live && live.status === 'pending'
          ? `<div class="board-card-live muted">本轮排队中 · 第 ${live.index ?? '?'} 题</div>` : '';
    const stats = [
      `<span title="实测通过率 = 通过 / 已判定（未评与题目已变更不进分母）">${rate == null ? '—' : `${rate}%`}<i>${measure.gradedAttempts || 0} 次</i></span>`,
      `<span title="平均累计 tokens">${compactNumber(measure.avgTokens)}</span>`,
      `<span title="平均步数">${measure.avgSteps ?? '—'}</span>`,
    ].join('');
    return `<article class="board-card" draggable="${DROPPABLE.has(card.lane)}" data-task="${esc(id)}" data-lane="${esc(card.lane)}" tabindex="0"
      aria-label="${esc(card.title)}，难度 ${esc(card.difficultyEffective)}">
      <div class="board-card-top">
        <span class="type-tag ${type.color}">${type.label}</span>
        <span class="difficulty-badge ${esc(card.difficultyEffective)}${card.difficultyOverridden ? ' overridden' : ''}"
          title="题面难度 ${esc(card.difficulty)}${card.difficultyOverridden ? '（已被看板改成 ' + esc(card.difficultyEffective) + '）' : ''}">${esc(card.difficultyEffective)}</span>
        <span class="board-card-verdict ${verdict.key}">${esc(verdict.text)}</span>
      </div>
      <b class="board-card-title">${esc(card.title)}</b>
      <code class="board-card-id">${esc(id)}</code>
      <div class="board-card-stats">${stats}</div>
      ${liveText}
    </article>`;
  }

  function filterValue() {
    return (el('boardFilter').value || '').trim().toLowerCase();
  }

  function matches(card, query) {
    if (!query) return true;
    return `${card.id} ${card.title} ${card.category} ${card.difficultyEffective}`.toLowerCase().includes(query);
  }

  function render() {
    if (!state) return;
    const query = filterValue();
    LANES.forEach((lane) => {
      const box = laneBox(lane);
      box.innerHTML = laneIds(lane)
        .map((id) => cardOf(id)).filter((card) => card && matches(card, query))
        .map((card) => cardMarkup(card.id)).join('')
        || `<p class="board-empty">${query ? '这一列没有匹配的卡片' : '空的'}</p>`;
      el(`lane${lane[0].toUpperCase()}${lane.slice(1)}Count`).textContent = laneIds(lane).length;
    });
    el('boardQueueCount').textContent = laneIds('queue').length;
    el('boardLaunchCount').textContent = laneIds('queue').length;
    el('boardLaunch').disabled = !laneIds('queue').length;
    document.querySelectorAll('#boardSorts button').forEach((button) => {
      button.classList.toggle('selected', button.dataset.sort === state.board.sort);
    });
    el('sortHint').textContent = state.board.sort === 'manual'
      ? '拖拽调整顺序' : '当前按' + ({
        difficulty_desc: '题面难度降序', difficulty_asc: '题面难度升序',
        measured_desc: '实测难度降序', measured_asc: '实测难度升序',
      }[state.board.sort] || state.board.sort) + '排，拖拽会切回手动';
    syncConcurrencyControls();
    const run = state.run || {};
    const chip = el('boardRunChip');
    chip.className = `status-chip${run.status && run.status !== 'idle' ? ' running-chip' : ''}`;
    chip.innerHTML = `<i></i> ${(run.status || 'idle').toUpperCase()}`;
    const started = run.id ? `${run.id} · ${run.model || run.profile || ''}` : '没有正在运行的评测';
    const progress = run.status && run.status !== 'idle'
      ? `在跑 ${laneIds('running').length} · 本轮待跑 ${laneIds('waiting').length}` : '没有 run 在跑';
    el('boardFoot').textContent = `${started} · ${progress} · 共 ${LANES.reduce((sum, lane) => sum + laneIds(lane).length, 0)} 张卡片 · `
      + `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
    if (openTaskId) renderDrawerBody();
  }

  function syncConcurrencyControls() {
    const ceiling = Math.max(1, state.ceiling || 4);
    const slider = el('boardJobs');
    slider.max = String(ceiling);
    const live = state.concurrency || {};
    const active = Boolean(live.applicable);
    const shown = active ? (live.jobs ?? state.run.startedJobs ?? state.board.jobs)
      : (state.board.jobs ?? Math.min(4, ceiling));
    slider.value = String(Math.min(ceiling, Math.max(1, shown || 1)));
    el('boardJobsValue').textContent = slider.value;
    el('boardJobsApply').textContent = active ? '热调' : '存为默认';
    el('boardJobsHint').textContent = active
      ? `当前 run 生效并发 ${live.jobs ?? '—'}（发车时 ${state.run.startedJobs ?? '—'}）· 这个端点上限 ${ceiling} · 只在题目边界生效，在跑的题不会被切断`
      : `没有 run 在跑：这个值作为下次发车的默认并发 · 这个端点上限 ${ceiling}`;
    localStorage.setItem('tracebench-jobs', String(slider.value));
  }

  async function load(options = {}) {
    if (loading) return;
    loading = true;
    try {
      const payload = await apiRequest('/api/board');
      state = payload;
      render();
      if (options.stopPolling) { clearInterval(polling); polling = null; }
    } catch (error) {
      el('boardFoot').textContent = `看板读取失败：${error.message}`;
    } finally {
      loading = false;
    }
  }

  async function patch(body, note) {
    try {
      state = await apiRequest('/api/board', { method: 'POST', body: JSON.stringify(body) });
      render();
      if (note) toast(note);
    } catch (error) {
      toast(`看板更新失败：${error.message}`);
      load();  // 失败了就别让画面停在"看起来已经改了"的状态，回到服务端真相
    }
  }

  /** 把 id 放进 list 里、贴着可见的前邻居。
   *
   * 为什么不用整列 DOM 顺序回写：卡片可以被筛选框遮住一部分，那时 DOM 里只有"看得见的
   * 那几张"。直接拿 DOM 顺序当全量提交，会把没在屏幕上的排队题悄悄挤出去——筛选状态下
   * 拖第 2 张，前一张就没了。所以只提交一次"移动"：落在哪两张之间，用它们的 id 表达。
   */
  function moveInto(list, id, beforeId, afterId) {
    const out = list.filter((item) => item !== id);
    if (beforeId && out.includes(beforeId)) out.splice(out.indexOf(beforeId), 0, id);
    else if (afterId && out.includes(afterId)) out.splice(out.indexOf(afterId) + 1, 0, id);
    else out.push(id);
    return out;
  }

  function neighboursOf(box, dragged) {
    const nodes = Array.from(box.querySelectorAll('.board-card'));
    const index = nodes.indexOf(dragged);
    return {
      before: nodes.slice(index + 1).map((node) => node.dataset.task)[0] || null,
      after: nodes.slice(0, Math.max(0, index)).map((node) => node.dataset.task).reverse()[0] || null,
    };
  }

  function commitDrag(targetLane, dragged) {
    if (!dragged || !DROPPABLE.has(targetLane)) return;
    const taskId = dragged.dataset.task;
    const card = cardOf(taskId);
    if (!card || !DROPPABLE.has(card.lane)) return;   // 在跑 / 本轮待跑两列不接受"搬动"
    const source = card.lane;
    const { before, after } = neighboursOf(laneBox(targetLane), dragged);
    const lists = {
      queue: [...laneIds('queue')], running: [...laneIds('running')],
      waiting: [...laneIds('waiting')], done: [...laneIds('done')],
    };
    lists[source] = lists[source].filter((item) => item !== taskId);
    lists[targetLane] = moveInto(lists[targetLane], taskId, before, after);
    const body = {
      queued: lists.queue,
      order: [...lists.queue, ...lists.running, ...lists.waiting, ...lists.done],
    };
    const switchToManual = state.board.sort !== 'manual';
    if (switchToManual) body.sort = 'manual';
    patch(body, switchToManual ? '已切回手动排序（拖拽只影响手动顺序）' : '');
  }

  // ---------- 拖拽 ----------
  function bindDnd() {
    const lanes = document.querySelectorAll('.board-cards');
    document.querySelector('#boardLanes').addEventListener('dragstart', (event) => {
      const card = event.target.closest('.board-card');
      if (!card) return;
      draggingId = card.dataset.task;
      card.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggingId);
    });
    document.querySelector('#boardLanes').addEventListener('dragend', () => {
      draggingId = null;
      lanes.forEach((box) => box.classList.remove('drop-target', 'drop-blocked'));
      render();  // 不管落到哪，最后都以服务端的推导结果为准
    });
    lanes.forEach((box) => {
      box.addEventListener('dragover', (event) => {
        if (!draggingId) return;
        const target = box.dataset.drop;
        if (!DROPPABLE.has(target)) {      // 在跑 / 本轮待跑由当前 run 决定，不收拖拽
          event.dataTransfer.dropEffect = 'none';
          box.classList.add('drop-blocked');
          return;
        }
        event.preventDefault();
        box.classList.add('drop-target');
        const dragged = document.querySelector('.board-card.dragging');
        if (!dragged) return;
        const after = Array.from(box.querySelectorAll('.board-card:not(.dragging)'))
          .find((node) => event.clientY < node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2);
        if (after) box.insertBefore(dragged, after);
        else box.appendChild(dragged);
      });
      box.addEventListener('dragleave', () => box.classList.remove('drop-target', 'drop-blocked'));
      box.addEventListener('drop', (event) => {
        const target = box.dataset.drop;
        box.classList.remove('drop-target', 'drop-blocked');
        if (!DROPPABLE.has(target) || !draggingId) return;
        event.preventDefault();
        try {
          commitDrag(target, document.querySelector('.board-card.dragging'));
        } catch (error) {
          // 拖拽出异常时画面已经"看起来改了"，必须回到服务端真相而不是留在那儿骗人。
          toast(`拖拽没生效：${error.message}`);
          load();
        }
      });
    });
  }

  // ---------- 卡片详情 ----------
  // 进行中的这一次还没有落盘（result.json / trajectory.json 都等这一步结束才写），
  // 但卡片点开必须看得见"现在正在发生什么"：模型、第几题、已经跑了多久。
  function liveAttemptRow(card) {
    const live = card.live;
    if (!live || !(live.status === 'running' || live.status === 'pending')) return '';
    const run = state.run || {};
    const runningNow = live.status === 'running';
    const position = card.runPosition != null ? `第 ${card.runPosition}/${card.runTotal ?? '?'} 题`
      : (live.index != null ? `第 ${live.index} 题` : '');
    return `<tr class="attempt-row latest live-row">
      <td><span class="result-dot running"></span><span class="status-text running">${runningNow ? 'RUN' : 'WAIT'}</span></td>
      <td class="attempt-model">${esc(run.model || run.profile || '—')}</td>
      <td class="mono">—</td>
      <td class="mono">—</td>
      <td class="mono">${runningNow && card.liveSeconds != null ? fmtElapsed(card.liveSeconds) : '—'}</td>
      <td class="attempt-time">${esc(position || (run.id || ''))}${position && run.id ? ` · ${esc(run.id.slice(0, 15))}` : ''}</td>
      <td><span class="muted">${runningNow ? '进行中…' : '等 worker'}</span></td>
    </tr>`;
  }

  // 排队列的卡片为什么在这：没数据 / 未评 / stale 各说各话，而不是笼统一句"排队中"。
  function queueReason(card) {
    const verdict = verdictOf(card);
    if (card.lane !== 'queue') return null;
    if (verdict.key === 'stale') return '题面已变更，旧结论作废，需要重测';
    if (verdict.key === 'ungraded') return '最近一次未评（上游故障或裁判没判成），等下一轮补跑';
    if (verdict.key === 'none') return '还没有任何落盘结果';
    // 有 PASS/FAIL 结论却还在排队 = 手动拖回来要重测的。
    return `${verdict.text} 之后被拖回排队，发车会再测一次`;
  }

  function attemptKey(card, attempt) {
    return `${attempt.runId}::${card.id}`;
  }

  function attemptMarkup(card, attempt, index) {
    const key = attemptKey(card, attempt);
    const status = attempt.stale ? 'stale' : attempt.graded === false ? 'ungraded'
      : attempt.passed ? 'pass' : 'fail';
    const label = attempt.stale ? 'STALE' : attempt.graded === false ? '未评' : attempt.passed ? 'PASS' : 'FAIL';
    return `<tr class="attempt-row ${index === 0 ? 'latest' : ''}" data-attempt="${esc(key)}" data-run="${esc(attempt.runId)}" data-task="${esc(card.id)}" tabindex="0">
      <td><span class="result-dot ${status}"></span><span class="status-text ${status}">${label}</span></td>
      <td class="attempt-model">${esc(attempt.model || '—')}</td>
      <td class="mono">${attempt.steps ?? '—'}</td>
      <td class="mono">${compactNumber(attempt.tokens)}</td>
      <td class="mono">${attempt.elapsed != null ? `${Number(attempt.elapsed).toFixed(0)}s` : '—'}</td>
      <td class="attempt-time">${esc((attempt.timestamp || '').slice(0, 16))}</td>
      <td><button class="row-action" type="button">${expanded.has(key) ? '收起' : '展开'} ↧</button></td>
    </tr>${expanded.has(key) ? `<tr class="attempt-detail"><td colspan="7">${detailMarkup(detailCache.get(key), card.id)}</td></tr>` : ''}`;
  }

  function detailMarkup(detail, taskId) {
    if (!detail) return '<span class="spinner"></span> 正在读取该次运行的判定与轨迹…';
    if (detail.error) return `<p class="detail-error">${esc(detail.error)}</p>`;
    const attempt = (detail.attempts || [])[0] || {};
    const checks = (attempt.checks || []).map((check, index) => `<li class="${check.passed ? 'pass' : 'fail'}">
      <b>${String(index + 1).padStart(2, '0')} ${esc(check.type)}</b>
      <code>${esc(check.detail)}</code>
      ${check.evidence ? `<pre>${esc(check.evidence)}</pre>` : ''}
    </li>`).join('');
    const steps = ((attempt.trajectory || {}).steps || []).map((step) => `<details class="step">
      <summary>#${step.idx} · ${esc((step.toolCalls || []).map((call) => call.name).join(' + ') || '纯文本回复')}
        <span>${step.tokens ? `${step.tokens.input ?? 0}+${step.tokens.output ?? 0} tk` : ''}${step.latencyMs ? ` · ${Math.round(step.latencyMs / 1000)}s` : ''}</span></summary>
      ${step.thought ? `<p class="step-thought">${esc(step.thought)}</p>` : ''}
      ${(step.toolCalls || []).map((call) => `<pre class="step-call">→ ${esc(call.name)} ${esc(call.args)}</pre>`).join('')}
      ${(step.observations || []).map((obs) => `<pre class="step-obs ${obs.ok ? '' : 'bad'}">← ${esc(obs.name)} ${esc(obs.result)}</pre>`).join('')}
    </details>`).join('');
    const judge = attempt.judge
      ? `<div class="detail-judge"><span>AI 裁判</span><strong>${esc(attempt.judge.score ?? '—')}</strong>
        <small>${esc(attempt.judge.verdict || '')}</small>
        ${(attempt.judge.reasons || []).map((reason) => `<p>${esc(reason)}</p>`).join('')}</div>` : '';
    return `<div class="attempt-detail-body">
      <div class="detail-metrics">
        <div><span>结论</span><strong>${attempt.passed ? 'PASS' : attempt.graded === false ? '未评' : 'FAIL'}</strong></div>
        <div><span>终止</span><strong>${esc((attempt.trajectory || {}).termination || '—')}</strong></div>
        <div><span>步骤 / tokens</span><strong>${(attempt.trajectory || {}).totalSteps ?? '—'} / ${compactNumber((attempt.trajectory || {}).totalTokens)}</strong></div>
        <div><span>模型重试</span><strong>${(attempt.trajectory || {}).retries ?? '—'}</strong></div>
      </div>
      ${attempt.failureReason ? `<p class="detail-failure"><b>失败原因</b>${esc(attempt.failureReason)}</p>` : ''}
      ${attempt.ungradedReason ? `<p class="detail-failure"><b>未评原因</b>${esc(attempt.ungradedReason)}</p>` : ''}
      ${judge}
      <h4>判定条件 <small>${(attempt.checks || []).filter((check) => !check.passed).length} 项未满足</small></h4>
      <ul class="attempt-checks">${checks || '<li>没有记录判定项</li>'}</ul>
      <h4>逐步轨迹 <small>${((attempt.trajectory || {}).steps || []).length} 步 · 观测文本已截断</small></h4>
      <div class="attempt-steps">${steps || '<p class="detail-error">这次运行没有落盘轨迹</p>'}</div>
    </div>`;
  }

  function drawerStatus(card) {
    const measure = card.measure || {};
    const budget = card.budget || {};
    const rate = measure.passRate == null ? '—' : `${Math.round(measure.passRate * 100)}%`;
    return [`<span class="type-tag ${(categoryFor(card).color || '')}">${esc(categoryFor(card).label)}</span>`,
      `<span class="difficulty-badge ${esc(card.difficultyEffective)}">${esc(card.difficultyEffective)}</span>`,
      `<span class="status-text ${(verdictOf(card).key || '').toLowerCase()}">${esc(verdictOf(card).text)}</span>`,
      `<span>实测通过率 <b>${rate}</b>（${measure.passes ?? 0}/${measure.gradedAttempts ?? 0} 已判定）</span>`,
      `<span>attempt ${measure.attempts ?? 0} · 未评 ${measure.ungradedAttempts ?? 0} · 待重测 ${measure.staleAttempts ?? 0}</span>`,
      `<span>预算 ${budget.max_steps ?? '—'} 步 / ${compactNumber(budget.max_tokens)} tk / ${budget.timeout_s ?? '—'}s</span>`]
      .filter(Boolean).join('');
  }

  function renderDrawerBody() {
    const card = cardOf(openTaskId);
    if (!card) return;
    const measure = card.measure || {};
    const attempts = measure.recent || [];
    const live = card.live;
    const passers = (measure.passers || []).map((model) => `<span>${esc(model)}</span>`).join('') || '<span class="muted">还没有模型做出来过</span>';
    el('boardDrawerBody').innerHTML = `<div class="detail-stack">
      ${live ? `<section class="board-live-banner ${esc(live.status)}"><div><span>当前 run</span><strong>${esc(live.status === 'running' ? '正在运行' : '排队等待 worker')}</strong>
        <small>${esc((state.run || {}).id || live.runId || '')} · 第 ${card.runPosition ?? live.index ?? '—'} 题${live.status === 'running' && card.liveSeconds != null ? ` · 已跑 ${fmtElapsed(card.liveSeconds)}` : ''}${live.tokens != null ? ` · ${live.tokens} tk / ${live.steps} 步` : ''}</small></div>
        <span class="board-live-note">轨迹要等这一步结束才落盘</span></section>`
        : card.lane === 'waiting' ? `<section class="board-live-banner waiting"><div><span>当前 run</span><strong>本轮待跑</strong>
        <small>${esc((state.run || {}).id || '')} · 第 ${card.runPosition ?? '—'}/${card.runTotal ?? '?'} 题，还没开题</small></div>
        <span class="board-live-note">并发热调之后它可能更早开跑</span></section>`
        : queueReason(card) ? `<section class="board-live-banner waiting"><div><span>排队中</span><strong>${esc(queueReason(card))}</strong>
        <small>${esc((state.run || {}).id && (state.run || {}).status !== 'idle' ? '当前 run 结束后可继续发车' : '点右上「跑这 N 题」即按这一列发车')}</small></div>
        <span class="board-live-note">从已完成拖回这里可以重测</span></section>` : ''}
      <section class="detail-section"><h3>实测概览</h3>
        <div class="detail-metrics">
          <div><span>已判定尝试</span><strong>${measure.gradedAttempts ?? 0}</strong></div>
          <div><span>通过</span><strong>${measure.passes ?? 0}</strong></div>
          <div><span>平均 tokens</span><strong>${compactNumber(measure.avgTokens)}</strong></div>
          <div><span>平均步数</span><strong>${measure.avgSteps ?? '—'}</strong></div>
        </div>
        <p class="board-passers"><span>做出来过的模型（${(measure.passers || []).length}）</span>${passers}</p>
        ${measure.modelCount ? `<p class="board-dim-note">覆盖 ${measure.modelCount} 个模型、${measure.attempts} 次尝试；未评与"题面已变更"不进通过率分母。</p>` : '<p class="board-dim-note">这道题还没有任何已判定结果，实测难度留空。</p>'}
      </section>
      <section class="detail-section"><h3>难度</h3>
        <div class="board-difficulty">
          <select id="boardDifficultySelect" aria-label="设置难度">
            ${['easy', 'medium', 'hard'].map((level) => `<option value="${level}"${card.difficultyEffective === level ? ' selected' : ''}>${level}</option>`).join('')}
          </select>
          <button class="ghost-button" id="boardDifficultyReset" type="button"${card.difficultyOverridden ? '' : ' disabled'}>恢复题面值（${esc(card.difficulty)}）</button>
          <small>看板可以覆盖题面难度用于排序；题面里那个才是判定依据。</small>
        </div>
      </section>
      <section class="detail-section"><h3>逐次运行 <small>${measure.recentTotal ?? 0} 次，列最近 ${attempts.length} 次${live && (live.status === 'running' || live.status === 'pending') ? ' · 含进行中的这一次' : ''}</small></h3>
        <table class="attempt-table"><tbody>${liveAttemptRow(card)}${attempts.map((attempt, index) => attemptMarkup(card, attempt, index)).join('')}${!liveAttemptRow(card) && !attempts.length ? '<tr><td class="board-dim-note">这道题还没有落盘过任何一次运行</td></tr>' : ''}</tbody></table>
      </section>
    </div>`;
    el('boardDrawerStatus').innerHTML = drawerStatus(card);
    el('boardDrawerTitle').textContent = card.title;
    el('boardDrawerTaskId').textContent = card.id;
    el('boardDrawerEyebrow').textContent = `${categoryFor(card).label} / ${card.difficultyEffective.toUpperCase()}`;
    document.querySelectorAll('[data-move-lane]').forEach((button) => {
      const movable = DROPPABLE.has(card.lane);
      button.disabled = !movable;
      button.title = movable ? '' : '这道题属于当前 run（在跑或本轮待跑），拖不动也移不进排队';
      button.classList.toggle('active', movable && button.dataset.moveLane === card.lane);
    });
    el('boardDrawerSpec').hidden = !taskById.has(card.id);
  }

  async function toggleAttempt(key, runId, taskId) {
    if (expanded.has(key)) {
      expanded.delete(key);
      renderDrawerBody();
      return;
    }
    expanded.add(key);
    if (!detailCache.has(key)) {
      renderDrawerBody();
      try {
        detailCache.set(key, await apiRequest(`/api/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}`));
      } catch (error) {
        detailCache.set(key, { error: `${error.message}（运行中的题要等落盘之后才有详情）` });
      }
    }
    renderDrawerBody();
  }

  function openDrawer(taskId) {
    openTaskId = taskId;
    renderDrawerBody();
    const layer = el('boardDrawer');
    layer.classList.add('open');
    layer.setAttribute('aria-hidden', 'false');
    document.body.classList.add('drawer-open');
    requestAnimationFrame(() => document.querySelector('#boardDrawer .drawer-close').focus());
  }

  function closeDrawer() {
    openTaskId = null;
    const layer = el('boardDrawer');
    layer.classList.remove('open');
    layer.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('#taskDrawer').classList.contains('open')) document.body.classList.remove('drawer-open');
  }

  // ---------- 事件 ----------
  function bindEvents() {
    el('view-board').addEventListener('click', (event) => {
      const card = event.target.closest('.board-card');
      if (card && !event.target.closest('select')) openDrawer(card.dataset.task);
    });
    el('view-board').addEventListener('keydown', (event) => {
      const card = event.target.closest('.board-card');
      if (card && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        openDrawer(card.dataset.task);
      }
    });
    el('boardRefresh').addEventListener('click', () => { load(); toast('看板已刷新'); });
    el('boardFilter').addEventListener('input', render);
    el('boardSorts').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-sort]');
      if (button) patch({ sort: button.dataset.sort });
    });
    el('boardJobs').addEventListener('input', () => { el('boardJobsValue').textContent = el('boardJobs').value; });
    el('boardJobs').addEventListener('change', () => { el('boardJobsValue').textContent = el('boardJobs').value; });
    el('boardJobsApply').addEventListener('click', async () => {
      const jobs = Number(el('boardJobs').value);
      const live = state.concurrency || {};
      try {
        if (live.applicable) {
          const response = await apiRequest('/api/concurrency', { method: 'POST', body: JSON.stringify({ jobs }) });
          state = response.board;
          render();
          toast(`已请求并发 ${response.concurrency.jobs ?? jobs}：在题目边界生效，不打断在跑的题`);
        } else {
          await patch({ jobs }, `已存为下次发车的默认并发 ${jobs}`);
        }
      } catch (error) {
        toast(`并发调整失败：${error.message}`);
      }
    });
    el('boardLaunch').addEventListener('click', () => {
      const queue = laneIds('queue');
      if (!queue.length) { toast('先把卡片拖进「排队」'); return; }
      openRunDialog('board', queue);
    });
    document.querySelectorAll('[data-close-board]').forEach((button) => button.addEventListener('click', closeDrawer));
    el('boardDrawerSpec').addEventListener('click', () => { if (openTaskId) openTask(openTaskId); });
    el('boardDrawerBody').addEventListener('click', (event) => {
      const row = event.target.closest('[data-attempt]');
      if (row) { toggleAttempt(row.dataset.attempt, row.dataset.run, row.dataset.task); return; }
      const select = event.target.closest('#boardDifficultySelect');
      if (select) return;
      if (event.target.closest('#boardDifficultyReset')) {
        patch({ clearDifficulty: openTaskId }, '已恢复题面难度');
      }
    });
    el('boardDrawerBody').addEventListener('change', (event) => {
      if (event.target.id === 'boardDifficultySelect') {
        patch({ difficulty: { [openTaskId]: event.target.value } }, `难度记为 ${event.target.value}`);
      }
    });
    document.querySelectorAll('[data-move-lane]').forEach((button) => button.addEventListener('click', () => {
      const lane = button.dataset.moveLane;
      const queue = laneIds('queue').filter((id) => id !== openTaskId);
      patch({ queued: lane === 'queue' ? [...queue, openTaskId] : queue },
        lane === 'queue' ? '已放入排队（还没发车）' : '已移出排队');
    }));
    bindDnd();
  }

  function ensureLoaded() {
    if (!state) load();
    if (polling) return;
    let tick = 0;
    polling = setInterval(() => {
      if (document.hidden) return;
      const visible = el('view-board').classList.contains('active');
      if (!visible || !state || !state.run.status || state.run.status === 'idle') return;
      const running = ['starting', 'running', 'pausing', 'paused', 'cancelling', 'finalizing'].includes(state.run.status);
      tick += 1;
      // 跑批时 4 秒一刷；已经结束的 run 只是防止别的窗口改了看板，32 秒够，不用每天重画两万一千次。
      if (!running && tick % 8) return;
      load();
    }, 4000);
  }

  window.Board = {
    load,
    render,
    closeDrawer,
    ensureLoaded,
    applyPayload(payload) { state = payload; render(); },
  };

  bindEvents();
  // 一开始不拉数据：看板不在默认视图，等用户点进来再取，省掉每次开页面的三次扫描。
  if (location.hash === '#board') ensureLoaded();
})();
