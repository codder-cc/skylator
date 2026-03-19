/* Nolvus Translator Web UI — main JS */

'use strict';

// ── API helpers ──────────────────────────────────────────────────────────────

async function api(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  return r.json();
}

const GET  = url        => api('GET',  url);
const POST = (url, b)   => api('POST', url, b);
const DEL  = url        => api('DELETE', url);

// ── Toast notifications ──────────────────────────────────────────────────────

function toast(msg, type = 'info') {
  const container = document.getElementById('toast-container') || _mkToastContainer();
  const el = document.createElement('div');
  const iconMap = { success: 'check-circle-fill', danger: 'exclamation-triangle-fill', info: 'info-circle-fill', warning: 'exclamation-circle-fill' };
  el.className = `toast align-items-center text-bg-${type} border-0 show`;
  el.setAttribute('role', 'alert');
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body"><i class="bi bi-${iconMap[type]||'info-circle-fill'} me-2"></i>${msg}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="this.closest('.toast').remove()"></button>
    </div>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function _mkToastContainer() {
  const c = document.createElement('div');
  c.id = 'toast-container';
  c.className = 'toast-container position-fixed bottom-0 end-0 p-3';
  c.style.zIndex = 9999;
  document.body.appendChild(c);
  return c;
}

// ── Job creation helpers ─────────────────────────────────────────────────────

async function startTranslateMod(modName, options = {}) {
  const r = await POST('/jobs/create', { type: 'translate_mod', mods: [modName], options });
  if (r.job_id) {
    toast(`Job started: ${modName}`, 'success');
    return r.job_id;
  }
  toast('Failed to start job: ' + (r.error || 'unknown'), 'danger');
  return null;
}

async function startTranslateAll(options = {}) {
  const r = await POST('/jobs/create', { type: 'translate_all', options });
  if (r.job_id) {
    toast('Translate-all job started', 'success');
    return r.job_id;
  }
  toast('Failed: ' + (r.error || 'unknown'), 'danger');
  return null;
}

async function startScan() {
  const r = await POST('/jobs/create', { type: 'scan_mods' });
  if (r.job_id) {
    toast('Mod scan started', 'info');
    return r.job_id;
  }
  return null;
}

async function cancelJob(jobId) {
  await POST(`/jobs/${jobId}/cancel`);
  toast('Job cancelled', 'warning');
}

// ── Job progress tracker ─────────────────────────────────────────────────────

class JobTracker {
  constructor(jobId, opts = {}) {
    this.jobId   = jobId;
    this.onUpdate = opts.onUpdate || (() => {});
    this.onDone   = opts.onDone   || (() => {});
    this._es      = null;
  }

  start() {
    this._es = new EventSource(`/jobs/${this.jobId}/stream`);
    this._es.onmessage = (e) => {
      const d = JSON.parse(e.data);
      this.onUpdate(d);
      if (['done','failed','cancelled'].includes(d.status)) {
        this._es.close();
        this.onDone(d);
      }
    };
    this._es.onerror = () => this._es.close();
  }

  stop() { if (this._es) this._es.close(); }
}

// ── Progress bar component ───────────────────────────────────────────────────

function renderProgressBar(pct, message, status) {
  const colorMap = { running: 'bg-primary', done: 'bg-success', failed: 'bg-danger', cancelled: 'bg-secondary' };
  const color = colorMap[status] || 'bg-primary';
  return `
    <div class="d-flex justify-content-between mb-1">
      <small class="text-muted">${message || ''}</small>
      <small class="text-accent">${pct.toFixed(1)}%</small>
    </div>
    <div class="progress" style="height:8px">
      <div class="progress-bar ${color} ${status==='running'?'progress-bar-striped progress-bar-animated':''}"
           style="width:${pct}%"></div>
    </div>`;
}

// ── Status badge helper ──────────────────────────────────────────────────────

function statusBadge(status) {
  const iconMap = {
    done:       'check-circle-fill',
    partial:    'circle-half',
    pending:    'clock-fill',
    no_strings: 'dash-circle',
    running:    'arrow-repeat',
    failed:     'x-circle-fill',
    cancelled:  'slash-circle',
    unknown:    'question-circle',
  };
  const icon = iconMap[status] || 'question-circle';
  return `<span class="badge-status ${status}"><i class="bi bi-${icon}"></i> ${status}</span>`;
}

// ── Inline string editing ────────────────────────────────────────────────────

function makeStringEditable(cell, key, esp, modName) {
  const current = cell.textContent.trim();
  cell.innerHTML = `<textarea class="string-edit-area" rows="2">${current}</textarea>
    <div class="mt-1 d-flex gap-1">
      <button class="btn btn-sm btn-primary" onclick="saveString(this,'${key}','${esp}','${modName}')">Save</button>
      <button class="btn btn-sm btn-outline-secondary" onclick="cancelEdit(this,'${current}')">Cancel</button>
    </div>`;
  cell.querySelector('textarea').focus();
}

async function saveString(btn, key, esp, modName) {
  const cell = btn.closest('td');
  const text = cell.querySelector('textarea').value;
  const r = await POST(`/mods/${modName}/strings/update`, { key, translation: text, esp });
  if (r.ok) {
    cell.innerHTML = `<span class="string-row-trans edited" ondblclick="makeStringEditable(this,'${key}','${esp}','${modName}')">${text}</span>`;
    toast('Saved', 'success');
  } else {
    toast('Save failed: ' + r.error, 'danger');
  }
}

function cancelEdit(btn, original) {
  const cell = btn.closest('td');
  cell.innerHTML = `<span class="string-row-trans">${original}</span>`;
}

// ── Confirm modal helper ─────────────────────────────────────────────────────

function confirmAction(message, onConfirm) {
  if (document.getElementById('confirm-modal')) {
    document.getElementById('confirm-modal').remove();
  }
  const el = document.createElement('div');
  el.id        = 'confirm-modal';
  el.className = 'modal fade';
  el.innerHTML = `
    <div class="modal-dialog modal-sm modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h6 class="modal-title">Confirm</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body"><p>${message}</p></div>
        <div class="modal-footer">
          <button class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
          <button class="btn btn-sm btn-danger" id="confirm-ok">Confirm</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(el);
  const modal = new bootstrap.Modal(el);
  modal.show();
  document.getElementById('confirm-ok').addEventListener('click', () => {
    modal.hide();
    onConfirm();
  });
}

// ── Copy to clipboard ────────────────────────────────────────────────────────

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast('Copied!', 'success'));
}

// ── Filter / search table ────────────────────────────────────────────────────

function filterTable(inputId, tableId) {
  const q   = document.getElementById(inputId).value.toLowerCase();
  const trs = document.querySelectorAll(`#${tableId} tbody tr`);
  trs.forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
