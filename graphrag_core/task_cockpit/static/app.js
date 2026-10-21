'use strict';

(() => {
  const element = id => document.getElementById(id);
  const statusNames = {queued: 'Queued', claimed: 'Claimed', review_pending: 'Awaiting review', completed: 'Completed', cancelled: 'Cancelled'};
  const categoryNames = {timeout: 'Timeout', generation_error: 'Generation error', finding_rejected: 'Finding rejected'};
  const stageNames = {generation: 'Generation', returned_finding: 'Finding validation'};
  let capability = '';
  let requestGeneration = 0;

  function text(id, value) { element(id).textContent = String(value); }
  function notice(message, error = false) {
    text('notice', message);
    element('notice').classList.toggle('error', error);
  }
  function clearTask() {
    element('task-view').hidden = true;
    text('project-side', 'Connect to view');
    for (const id of ['task-heading', 'task-id', 'checked-at', 'revision', 'attempts', 'submissions', 'reviews', 'claim', 'checkpoint', 'recoveries', 'assessments', 'reviewer']) text(id, '');
    element('failures').replaceChildren();
  }
  function disconnect(message = 'Disconnected. Reopen the private launch link or enter its access code.') {
    requestGeneration += 1;
    capability = '';
    element('token').value = '';
    clearTask();
    element('access').hidden = false;
    element('disconnect').hidden = true;
    element('refresh').disabled = true;
    notice(message);
  }
  function validStatus(value) {
    const counts = ['revision', 'attempts', 'submission_count', 'review_count', 'recovery_count', 'assessment_count'];
    return value && value.schema_version === 1 && value.read_only === true &&
      typeof value.task_id === 'string' && typeof value.project === 'string' &&
      Object.hasOwn(statusNames, value.status) &&
      counts.every(key => Number.isSafeInteger(value[key]) && value[key] >= 0) &&
      typeof value.claim_present === 'boolean' && typeof value.claim_expired === 'boolean' &&
      typeof value.checkpoint_present === 'boolean' && Array.isArray(value.failures) && value.failures.length <= 5;
  }
  function render(value) {
    text('project-side', value.project);
    text('task-heading', value.project);
    text('task-id', value.task_id);
    text('status', statusNames[value.status]);
    element('status').dataset.state = value.status;
    text('revision', value.revision);
    text('attempts', value.attempts);
    text('submissions', value.submission_count);
    text('reviews', value.review_count);
    text('recoveries', value.recovery_count);
    text('assessments', value.assessment_count);
    text('claim', value.claim_present ? (value.claim_expired ? 'Expired' : 'Present') : 'None');
    text('checkpoint', value.checkpoint_present ? 'Available' : 'Not recorded');
    const reviewers = {human: 'Human', agent: 'Agent', automated: 'Automated', unspecified: 'Not specified'};
    text('reviewer', value.review_count ? (reviewers[value.latest_review_actor_type] || 'Not specified') : 'No review recorded');
    text('review-policy', value.human_review_required === true ? 'Human review required' :
      value.human_review_required === false ? 'Standard review policy' : 'Review policy unavailable');
    text('checked-at', 'Checked ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}));
    const rows = value.failures.map(failure => {
      const row = document.createElement('tr');
      for (const item of [failure.attempt, categoryNames[failure.category] || 'Recorded failure', stageNames[failure.stage] || 'Not specified']) {
        const cell = document.createElement('td');
        cell.textContent = String(item);
        row.append(cell);
      }
      return row;
    });
    element('failures').replaceChildren(...rows);
    element('failure-table').hidden = rows.length === 0;
    element('failure-empty').hidden = rows.length !== 0;
    element('access').hidden = true;
    element('task-view').hidden = false;
    element('disconnect').hidden = false;
    notice('Connected · Selected task status loaded.');
  }
  async function refresh() {
    if (!capability) return;
    const generation = ++requestGeneration;
    element('refresh').disabled = true;
    notice('Checking the selected task…');
    try {
      const response = await fetch('/api/v1/task-status', {
        headers: {Authorization: 'Bearer ' + capability}, credentials: 'omit', cache: 'no-store', redirect: 'error'
      });
      if (generation !== requestGeneration) return;
      if (response.status === 401) { disconnect('Access code was not accepted. Enter the current launch code.'); return; }
      if (!response.ok) throw new Error('unavailable');
      const value = await response.json();
      if (generation !== requestGeneration) return;
      if (!validStatus(value)) throw new Error('invalid-status');
      render(value);
    } catch {
      if (generation === requestGeneration) {
        clearTask();
        notice('Task status is unavailable. Check the selected task and try refreshing.', true);
      }
    } finally {
      if (generation === requestGeneration) element('refresh').disabled = !capability;
    }
  }
  element('connect-form').addEventListener('submit', event => {
    event.preventDefault();
    const value = element('token').value;
    element('token').value = '';
    if (!/^[A-Za-z0-9._~+\-]{32,256}$/.test(value)) { notice('Enter the access code from the private launch record.', true); return; }
    capability = value;
    refresh();
  });
  element('refresh').addEventListener('click', refresh);
  element('disconnect').addEventListener('click', () => disconnect());
  window.addEventListener('pagehide', () => disconnect());
  // A fragment never reaches the HTTP server. Remove it before any API request;
  // retain the capability in memory only, without cookies or browser storage.
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const initial = fragment.get('token');
  if (window.location.hash) history.replaceState(null, '', window.location.pathname);
  if (initial && /^[A-Za-z0-9._~+\-]{32,256}$/.test(initial)) {
    capability = initial;
    refresh();
  }
})();
