// lyrembed's page. Everything it shows comes from the local server's JSON API
// (lyrembed/web.py), and every change to a file is a request to it.
import { html, render, useState, useEffect, useRef, useCallback, useMemo } from '/static/vendor/preact-htm.js';
import { Icon } from '/static/icons.js';
import { parse, currentLines, applyShift, stampLine, formatStamp, playTime, nearestTime } from '/static/lrc.js';

async function api(path, body, signal) {
  const options = { signal };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch('/api' + path, options);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new Error('lyrembed is not responding. Is it still running?');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

const fmtLength = (s) => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '');
const fmtClock = (s) => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, '0')}`;
const STATUS = {
  none: ['No lyrics', 'NONE'],
  sync: ['Synced', 'SYNC'],
  plain: ['Plain', 'PLAIN'],
  incomplete: ['Tags incomplete', 'TAGS'],
  unreadable: ['Unreadable', 'ERROR'],
};
const SCRIPTS = { latin: 'LATIN', original: 'ORIG', mixed: 'MIXED' };
// The way through a folder, in the header: view, name, icon.
const FLOW = [['folder', 'Folder', 'folder'], ['library', 'Tracks', 'list'], ['track', 'Lyrics', 'lyrics'], ['edit', 'Edit', 'pencil']];

function lengthLabel(c) {
  if (c.diff !== null) return c.diff ? `±${c.diff} s` : 'length OK';
  if (c.duration) return fmtLength(c.duration);
  return 'length unknown';
}

function StatusBadge({ status, short }) {
  const [long, brief] = STATUS[status] || [status, status];
  return html`<span class=${'badge b-' + status}>${short ? brief : long}</span>`;
}

function ScriptBadge({ script }) {
  return script ? html`<span class="badge b-script" title=${script === 'mixed' ? 'Several Asian scripts: romanize with “Mixed scripts”' : ''}>${SCRIPTS[script]}</span>` : null;
}

// The track's cover; ?v= changes with the cover, so the browser may keep it.
function Cover({ index, id, size = 40 }) {
  if (!id) return null;
  return html`<img class="cover" src=${`/api/tracks/${index}/cover?v=${id}`} alt="" loading="lazy"
    width=${size} height=${size} style=${{ width: size + 'px', height: size + 'px' }} />`;
}

function PlayIcon({ playing, size = 16 }) {
  return playing
    ? html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"></rect><rect x="14" y="4" width="4" height="16" rx="1"></rect></svg>`
    : html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5Z"></path></svg>`;
}

// --- app ----------------------------------------------------------------------
function App() {
  const [library, setLibrary] = useState(null);
  const [view, setView] = useState({ name: 'library' });
  const [picker, setPicker] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [stopped, setStopped] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [job, setJob] = useState(null);
  // The song list's preview: one track at a time, kept while moving around.
  const [preview, setPreview] = useState(null);
  // "Romanize when saving" stays as set while working through an album.
  const [romanizeOnSave, setRomanizeOnSave] = useState(null);
  const previewAudio = useRef(null);
  const pollFailed = useRef(false);
  // Set by the editor; checked before anything leaves it.
  const dirty = useRef(false);

  const notify = useCallback((message, kind = 'success') => {
    const id = Math.random();
    setToasts((list) => [...list, { id, message, kind }]);
    if (kind === 'success' || kind === 'info') {
      setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 6000);
    }
  }, []);

  useEffect(() => {
    // Back from cdimport, the tab arrives while cdimport is still ending and
    // has not yet said which album it imported: ask again until it has.
    const loadLibrary = (first) => api('/library')
      .then((lib) => {
        setLibrary(lib);
        if (first) setRomanizeOnSave(lib.autoRomanize);
        if (lib.imported) notify(`Imported ${lib.imported}: here it is.`);
        if (!lib.folder && !lib.importing) setPicker(true);
        if (lib.importing) setTimeout(() => loadLibrary(false), 1000);
      })
      .catch((err) => setLoadError(err.message));
    loadLibrary(true);
    // After a reload, a run that is still going shows again.
    api('/jobs/current').then((r) => { if (r.job && r.job.running) { setJob(r.job); setView({ name: 'bulk' }); } }).catch(() => {});
    // The server stops when the page is gone: it hears nothing for a while,
    // or the page says it is closing and does not come back (a reload does).
    const ping = setInterval(() => api('/ping', {}).then((r) => { if (r.update) setUpdate(r.update); }).catch(() => {}), 15000);
    api('/update').then(setUpdate).catch(() => {});
    const closing = () => navigator.sendBeacon('/api/closing', '');
    const unsaved = (event) => { if (dirty.current) event.preventDefault(); };
    addEventListener('pagehide', closing);
    addEventListener('beforeunload', unsaved);
    return () => {
      clearInterval(ping);
      removeEventListener('pagehide', closing);
      removeEventListener('beforeunload', unsaved);
    };
  }, []);

  const leaveOk = () => {
    if (dirty.current && !confirm('Discard your unsaved changes?')) return false;
    dirty.current = false;
    return true;
  };
  const go = (next) => { if (leaveOk()) { setPicker(false); setView(next); } };
  // The Lyrics step leads back to the track last worked on.
  const lastTrack = useRef(null);
  if (view.index !== undefined) lastTrack.current = view.index;

  const updateTrack = (track) => setLibrary((lib) => ({
    ...lib, tracks: lib.tracks.map((t) => (t.index === track.index ? { ...t, ...track } : t)),
  }));

  // Saves and reports; returns false when nothing was written.
  const save = async (index, lyrics, message) => {
    try {
      const result = await api(`/tracks/${index}/save`, { lyrics });
      updateTrack(result.track);
      notify(message);
      result.warnings.forEach((w) => notify(w, 'warn'));
      return true;
    } catch (err) {
      notify(err.message, 'error');
      return false;
    }
  };

  const nextTrack = (index) => {
    if (index + 1 < library.tracks.length) setView({ name: 'track', index: index + 1 });
    else { notify('That was the last track.', 'info'); setView({ name: 'library' }); }
  };

  // While a run goes, ask for its new rows; when it ends, the list changed.
  useEffect(() => {
    if (!job || !job.running) return undefined;
    const timer = setTimeout(async () => {
      try {
        const { job: next } = await api(`/jobs/current?since=${job.rows.length}`);
        pollFailed.current = false;
        if (!next) { setJob(null); return; }
        setJob({ ...next, rows: job.rows.concat(next.rows) });
        if (!next.running) api('/library').then(setLibrary).catch(() => {});
      } catch (err) {
        // One failed request must not end the updates: ask again, a bit later.
        if (!pollFailed.current) notify(err.message, 'error');
        pollFailed.current = true;
        setTimeout(() => setJob((current) => (current === job ? { ...job } : current)), 2000);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [job]);

  const playPreview = (index) => {
    const a = previewAudio.current;
    if (preview && preview.index === index && a && !preview.error) {
      if (a.paused) a.play().catch(() => {}); else a.pause();
      return;
    }
    setPreview({ index, playing: false, pos: 0, duration: 0, error: '' });
  };
  const previewFailed = (index) => {
    // The track page knows why (ALAC without ffmpeg, say).
    api(`/tracks/${index}`)
      .then((t) => setPreview((p) => (p && p.index === index
        ? { ...p, playing: false, error: t.playback.note || 'The browser could not play this file.' } : p)))
      .catch(() => {});
  };
  // The editor has its own player; the preview's bar closes rather than
  // showing a second one.
  useEffect(() => {
    if (view.name !== 'edit') return;
    if (previewAudio.current) previewAudio.current.pause();
    setPreview(null);
  }, [view.name]);

  const opened = (lib) => {
    setLibrary(lib);
    setPicker(false);
    setPreview(null);
    setView({ name: 'library' });
    notify(`Opened ${lib.name}: ${lib.tracks.length} tracks.`, 'info');
  };

  // A newer lyrembed (/api/update): looked for at every start or
  // from Settings, installed with pipx, then lyrembed starts again on the same
  // address and this page reloads.
  const [update, setUpdate] = useState(null);
  const [restarting, setRestarting] = useState('');
  const [hiddenUpdate, setHiddenUpdate] = useState(() => {
    try { return sessionStorage.getItem('lyrembed-hidden-update') || ''; } catch { return ''; }
  });
  const lastUpdate = useRef(null);
  useEffect(() => {
    const before = lastUpdate.current;
    lastUpdate.current = update;
    // Checking or installing: ask again until it is done.
    if (update && ['checking', 'installing'].includes(update.state)) {
      const timer = setTimeout(() => api('/update').then(setUpdate).catch(() => {}), 1500);
      return () => clearTimeout(timer);
    }
    // A check asked for in Settings always answers; the one at the start
    // only mentions a newer version.
    if (!update || !before || before.state !== 'checking' || update.quiet) return;
    if (update.state === 'current') notify(`lyrembed ${update.current} is the newest version.`, 'info');
    if (update.state === 'failed') notify(`Couldn't look for a newer lyrembed: ${update.message}`, 'error');
  }, [update]);
  const checkUpdates = () => api('/update/check', {}).then((u) => { setHiddenUpdate(''); setUpdate(u); })
    .catch((err) => notify(err.message, 'error'));
  const installUpdate = () => api('/update/install', {}).then(setUpdate).catch((err) => notify(err.message, 'error'));
  const hideUpdate = () => {
    setHiddenUpdate(update.version);
    try { sessionStorage.setItem('lyrembed-hidden-update', update.version); } catch { /* only for this tab anyway */ }
  };
  const restart = async () => {
    if (!leaveOk()) return;
    try {
      await api('/update/restart', {});
    } catch (err) {
      notify(err.message, 'error');
      return;
    }
    setRestarting('waiting');
    // The new lyrembed answers on this address with the same cookie; until
    // the old one has stopped, it is still the old one answering.
    const wanted = update.version;
    const started = Date.now();
    const wait = () => fetch('/api/update')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((u) => { if (u.current === wanted) location.reload(); else throw new Error('still the old one'); })
      .catch(() => { if (Date.now() - started < 60000) setTimeout(wait, 1000); else setRestarting('lost'); });
    setTimeout(wait, 1000);
  };

  const [importing, setImporting] = useState(false);
  // cdimport opens its own page; its Done leads back here with the album.
  const importCd = async () => {
    if (!leaveOk()) return;
    setImporting(true);
    try {
      const { url } = await api('/import', {});
      location.href = url;
    } catch (err) {
      notify(err.message, 'error');
      setImporting(false);
    }
  };

  const quit = async () => {
    if (job && job.running && !confirm('A bulk run is still going. Stop it after the current track?')) return;
    if (!leaveOk()) return;
    await api('/quit', {}).catch(() => {});
    // Opened by another program's page: the tab goes back there.
    if (library && library.returnUrl) location.href = library.returnUrl;
    else setStopped(true);
  };

  let body = null;
  if (view.name === 'settings' && !picker) {
    body = html`<${SettingsView} notify=${notify} update=${update} onCheckUpdates=${checkUpdates} />`;
  } else if (picker || (library && !library.folder)) {
    body = html`<${FolderPicker} library=${library} onOpened=${opened}
      onCancel=${library && library.folder ? () => setPicker(false) : null} />`;
  } else if (library && view.name === 'library') {
    body = html`<${LibraryView} library=${library} onOpen=${(index) => go({ name: 'track', index })}
      preview=${preview} onPlay=${playPreview} onChangeFolder=${() => { if (leaveOk()) setPicker(true); }} />`;
  } else if (library && view.name === 'track') {
    body = html`<${TrackView} key=${view.index} library=${library} index=${view.index} notify=${notify}
      preview=${preview} onPlay=${playPreview}
      romanize=${!!romanizeOnSave} onRomanize=${setRomanizeOnSave}
      onOpen=${(index) => go({ name: 'track', index })} onHome=${() => go({ name: 'library' })}
      onUpdated=${updateTrack} onNext=${() => nextTrack(view.index)}
      onSave=${async (lyrics, message, next) => {
        const ok = await save(view.index, lyrics, message);
        if (ok && next) nextTrack(view.index);
        return ok;
      }}
      onEdit=${(text, source, versions, chosen) => setView({ name: 'edit', index: view.index, text, source, versions, chosen })} />`;
  } else if (library && view.name === 'bulk') {
    body = html`<${BulkView} library=${library} job=${job} onJob=${setJob} notify=${notify}
      onOpen=${(index) => go({ name: 'track', index })} />`;
  } else if (library && view.name === 'edit') {
    body = html`<${Editor} key=${'edit' + view.index} track=${library.tracks[view.index]} initial=${view.text}
      source=${view.source} versions=${view.versions} chosen=${view.chosen} dirty=${dirty}
      onBack=${() => go({ name: 'track', index: view.index })}
      onNext=${() => nextTrack(view.index)}
      onSave=${async (lyrics, next) => {
        if (!(await save(view.index, lyrics, 'Lyrics saved.'))) return;
        dirty.current = false;
        if (next) nextTrack(view.index); else setView({ name: 'track', index: view.index });
      }} />`;
  }

  const folderOpen = !!(library && library.folder);
  const sidePage = !picker && ['bulk', 'settings'].includes(view.name);
  const place = sidePage ? null : picker || !folderOpen ? 0 : FLOW.findIndex(([name]) => name === view.name);
  const reached = place === null ? (folderOpen ? 2 : 0) : place;
  const stepAction = {
    folder: () => { if (leaveOk()) setPicker(true); },
    library: folderOpen && (() => go({ name: 'library' })),
    track: folderOpen && lastTrack.current !== null && (() => go({ name: 'track', index: lastTrack.current })),
  };
  const width = place === 0 || view.name === 'settings' ? 'narrow' : ['track', 'edit'].includes(view.name) ? 'wide' : '';

  return html`
    <header class="topbar">
      <div><button class="brand" onClick=${() => go({ name: 'library' })} title="Back to the track list">
        <img src="/static/logo.svg" alt="" width="28" height="28" /><span class="brand-name">lyrembed</span>
      </button></div>
      <ol class="steps" aria-label="Workflow">
        ${FLOW.map(([name, label, icon], i) => {
          const state = i === place ? 'now' : i < reached ? 'done' : '';
          return html`<li class=${'step ' + state} key=${name}>
            <button class="step-go" title=${label} disabled=${!stepAction[name] || i === place} aria-current=${i === place ? 'step' : null}
              onClick=${stepAction[name]}><span class="dot"><${Icon} name=${state === 'done' ? 'check' : icon} size=${14} /></span>
              <span class="step-name">${label}</span></button>
          </li>`;
        })}
      </ol>
      <div class="topbar-end">
        ${library && library.cdimport && html`<button class="btn btn-flat" disabled=${importing} onClick=${importCd} title="Import a CD">
          <${Icon} name="disc" /><span class="btn-text">${importing ? 'Starting cdimport…' : library.importing ? 'Back to the import' : 'Import a CD'}</span></button>`}
        <button class=${'btn btn-flat' + (view.name === 'bulk' && !picker ? ' btn-on' : '')} disabled=${!folderOpen} title="Bulk actions"
          onClick=${() => go({ name: 'bulk' })}><${Icon} name="layers" /><span class="btn-text">Bulk actions${job && job.running ? ' ●' : ''}</span></button>
        <button class=${'btn btn-flat btn-icon' + (view.name === 'settings' && !picker ? ' btn-on' : '')} title="Settings" aria-label="Settings"
          onClick=${() => go({ name: 'settings' })}><${Icon} name="settings" /></button>
        ${library && library.returnUrl
          ? html`<button class="btn btn-primary" onClick=${quit}><span class="btn-text">Done – back to ${library.returnName}</span></button>`
          : html`<button class="btn btn-flat" onClick=${quit} title="Quit lyrembed"><${Icon} name="power" /><span class="btn-text">Quit</span></button>`}
      </div>
    </header>
    <main class=${[width, preview ? 'with-player' : ''].filter(Boolean).join(' ')}>
      ${loadError && html`<p class="note note-error">${loadError}</p>`}
      ${(restarting || (update && update.version && update.version !== hiddenUpdate)) && html`<${UpdateNote}
        update=${update} restarting=${restarting} jobRunning=${!!(job && job.running)}
        onInstall=${installUpdate} onRestart=${restart} onHide=${hideUpdate} />`}
      ${library && library.returnUrl && view.name === 'library' && html`<p class="note note-info" style=${{ marginBottom: '20px' }}>
        ${library.returnName} opened this album here. When you're done, <b>Done – back to ${library.returnName}</b> takes you back.</p>`}
      ${body}
    </main>
    ${preview && library && library.tracks[preview.index] && html`<${MiniPlayer} track=${library.tracks[preview.index]}
      preview=${preview} audio=${previewAudio} onPlay=${() => playPreview(preview.index)}
      onOpen=${() => go({ name: 'track', index: preview.index })}
      onChange=${(change) => setPreview((p) => (p ? { ...p, ...change } : p))}
      onFailed=${() => previewFailed(preview.index)}
      onClose=${() => { if (previewAudio.current) previewAudio.current.pause(); setPreview(null); }} />`}
    <div class=${'toasts' + (preview ? ' above-player' : '')} aria-live="polite">
      ${toasts.map((t) => html`<div class=${'toast toast-' + t.kind} key=${t.id}>
        <span>${t.message}</span>
        <button aria-label="Dismiss" onClick=${() => setToasts((list) => list.filter((x) => x.id !== t.id))}>×</button>
      </div>`)}
    </div>
    ${stopped && html`<div class="overlay"><div class="card status">
      <img class="status-icon" src="/static/icon.svg" alt="" />
      <h1>lyrembed has stopped</h1>
      <p class="muted">You can close this tab. Start lyrembed again to continue.</p>
    </div></div>`}
  `;
}

// --- a newer lyrembed -------------------------------------------------------------
function UpdateNote({ update, restarting, jobRunning, onInstall, onRestart, onHide }) {
  const style = { marginBottom: '20px' };
  if (restarting === 'waiting') {
    return html`<p class="note note-info" style=${style}>Starting lyrembed ${update.version}… this page reloads once it answers.</p>`;
  }
  if (restarting === 'lost') {
    return html`<p class="note note-error" style=${style}>The new lyrembed did not answer on this address. Start lyrembed
      again (in a terminal or from the app menu); it opens a new page.</p>`;
  }
  const v = update.version;
  const notes = html`<a href=${update.notes} target="_blank" rel="noopener noreferrer">What's new <${Icon} name="link" size=${13} /></a>`;
  if (update.state === 'installing') {
    return html`<p class="note note-info" style=${style}>Updating to lyrembed ${v}: pipx downloads and installs it, which takes
      a minute or two. You can go on working meanwhile.</p>`;
  }
  if (update.state === 'installed') {
    return html`<div class="note note-success stack-tight" style=${style}>
      <span><b>lyrembed ${v} is installed.</b> Until it restarts, this page runs ${update.current}.</span>
      <div class="row-wrap"><button class="btn btn-small btn-primary" disabled=${jobRunning} onClick=${onRestart}>Restart lyrembed</button>
        ${jobRunning && html`<span class="small">After the bulk run.</span>`}</div></div>`;
  }
  if (update.state === 'install-failed') {
    return html`<div class="note note-error stack-tight" style=${style}>
      <span><b>The update to lyrembed ${v} didn't work</b> (pipx ended with code ${update.code}). Everything pipx said is in
        <span class="mono">${update.log}</span>. To try it in a terminal: <span class="mono">${update.command}</span></span>
      <pre class="mono small" style=${{ whiteSpace: 'pre-wrap', margin: 0 }}>${update.message}</pre>
      <div class="row-wrap"><button class="btn btn-small" onClick=${onInstall}>Try again</button>
        <button class="btn btn-small" onClick=${onHide}>Not now</button></div></div>`;
  }
  if (update.state !== 'available') return null;
  return html`<div class="note note-info stack-tight" style=${style}>
    <span><b>lyrembed ${v} is available</b> (this is ${update.current}). ${notes}</span>
    ${update.canInstall
      ? html`<span class="small">Update runs <span class="mono">${update.command}</span>, then lyrembed starts again.</span>`
      : html`<span class="small">Update it with <span class="mono">${update.command}</span> in a terminal, then start lyrembed again.</span>`}
    <div class="row-wrap">
      ${update.canInstall && html`<button class="btn btn-small btn-primary" onClick=${onInstall}>Update</button>`}
      <button class="btn btn-small" onClick=${onHide}>Not now</button></div></div>`;
}

// --- folders --------------------------------------------------------------------
function FolderPicker({ library, onOpened, onCancel }) {
  const [listing, setListing] = useState(null);
  const [path, setPath] = useState('');
  const [error, setError] = useState(library && library.problem ? library.problem : '');
  const [busy, setBusy] = useState(false);

  const load = (target) => api('/folders?path=' + encodeURIComponent(target))
    .then((l) => { setListing(l); setPath(l.path); })
    .catch((err) => setError(err.message));
  useEffect(() => { load(''); }, []);

  const open = async (target) => {
    setBusy(true);
    setError('');
    try {
      onOpened(await api('/folder', { path: target }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return html`<div class="stack">
    <div class="status">
      <img class="status-icon" src="/static/icon.svg" alt="" />
      <h1>Open a music folder</h1>
    </div>
    <section class="card pad stack">
    ${error && html`<p class="note note-warn" style=${{ whiteSpace: 'pre-line' }}>${error}</p>`}
    <form class="row-wrap" onSubmit=${(e) => { e.preventDefault(); load(path); }}>
      <label class="sr" for="folder-path">Folder</label>
      <input id="folder-path" type="text" class="mono" value=${path} onInput=${(e) => setPath(e.target.value)}
        style=${{ flex: '1 1 320px', minWidth: 0 }} />
      <button class="btn" type="submit"><${Icon} name="search" /> Show</button>
      <button class="btn btn-primary" type="button" disabled=${busy} onClick=${() => open(path)}><${Icon} name="folder" /> Open this folder</button>
      ${onCancel && html`<button class="btn" type="button" onClick=${onCancel}>Cancel</button>`}
    </form>
    ${listing && html`<div class="stack-tight">
      <div class="row-wrap">
        <button class="btn btn-small" disabled=${!listing.parent} onClick=${() => load(listing.parent)}><${Icon} name="up" /> Up</button>
        <button class="btn btn-small" onClick=${() => load(listing.home)}><${Icon} name="home" /> Home</button>
        <span class="muted small">${listing.musicFiles
          ? `${listing.musicFiles} music file${listing.musicFiles === 1 ? '' : 's'} directly in this folder`
          : 'No music files directly in this folder'}</span>
      </div>
      <div class="folders">
        ${listing.dirs.map((d) => html`<button class="list-row" key=${d.path} onClick=${() => load(d.path)}
          onDblClick=${() => open(d.path)}><${Icon} name="folder" /> ${d.name}</button>`)}
        ${!listing.dirs.length && html`<p class="muted small" style=${{ padding: '8px 14px' }}>No subfolders.</p>`}
        ${listing.truncated && html`<p class="muted small" style=${{ padding: '8px 14px' }}>Only the first 500 folders are listed.</p>`}
      </div>
      <p class="muted small">Click a folder to look inside, double-click to open it. Subfolders such as CD 1 and CD 2 are included when you open their album.</p>
    </div>`}
    </section>
  </div>`;
}

// --- track list -------------------------------------------------------------------
function LibraryView({ library, onOpen, preview, onPlay, onChangeFolder }) {
  const tracks = library.tracks;
  const count = (status) => tracks.filter((t) => t.status === status).length;
  const first = tracks.find((t) => t.status === 'none' && !t.instrumental);
  const groups = new Set(tracks.map((t) => t.group)).size > 1;
  // A disc folder's own cover goes with its heading when the album has none in common.
  const groupCover = {};
  if (!library.cover) {
    for (const t of tracks) {
      groupCover[t.group] = t.group in groupCover && groupCover[t.group] !== t.cover ? '' : t.cover;
    }
  }
  let lastGroup = null;

  return html`<div class="stack">
    <div class="spread">
      <div class="row-wrap grow" style=${{ gap: '18px', flexWrap: 'nowrap', flexBasis: '420px' }}>
        ${library.cover && html`<${Cover} index=${0} id=${library.cover} size=${112} />`}
        <div class="stack-tight">
          <h1>${library.name}</h1>
          <span class="path mono">${library.folder}</span>
          <div class="row-wrap">
      <span class="badge b-sync">${count('sync')} synced</span>
      <span class="badge b-plain">${count('plain')} plain</span>
      <span class="badge b-none">${count('none')} without lyrics</span>
      ${count('incomplete') > 0 && html`<span class="badge b-incomplete">${count('incomplete')} with incomplete tags</span>`}
      ${count('unreadable') > 0 && html`<span class="badge b-unreadable">${count('unreadable')} unreadable</span>`}
          </div>
        </div>
      </div>
      <div class="row-wrap">
        <button class="btn" onClick=${onChangeFolder}><${Icon} name="folder" /> Change folder…</button>
        <button class="btn btn-primary" disabled=${!first} onClick=${() => onOpen(first.index)}>
          <${Icon} name=${first ? 'lyrics' : 'check'} /> ${first ? 'First track without lyrics' : 'Every track has lyrics'}
        </button>
      </div>
    </div>
    <div class="card table-box">
      <table>
        <thead><tr>
          <th class="label num">#</th><th><span class="sr">Listen</span></th><th class="label">Title</th><th class="label">Length</th>
          <th class="label">Format</th><th class="label">Lyrics</th><th><span class="sr">Open</span></th>
        </tr></thead>
        <tbody>
          ${tracks.map((t) => {
            const header = groups && t.group !== lastGroup;
            lastGroup = t.group;
            const playing = preview && preview.index === t.index && preview.playing;
            const thumb = !library.cover && !(groups && groupCover[t.group]) && t.cover;
            return html`
              ${header && html`<tr class="group"><td colspan="7"><span class="row-wrap">
                ${groupCover[t.group] ? html`<${Cover} index=${t.index} id=${groupCover[t.group]} size=${48} />` : html`<${Icon} name="folder" />`}
                ${t.group || library.name}</span></td></tr>`}
              <tr class=${'track' + (preview && preview.index === t.index ? ' previewing' : '')} key=${t.index} onClick=${() => onOpen(t.index)}>
                <td class="num mono">${t.index + 1}</td>
                <td class="play-cell">
                  <button class="btn btn-round" onClick=${(e) => { e.stopPropagation(); onPlay(t.index); }}
                    aria-label=${(playing ? 'Pause ' : 'Listen to ') + (t.title || t.file)}><${PlayIcon} playing=${playing} size=${14} /></button>
                </td>
                <td><div class="row-wrap" style=${{ flexWrap: 'nowrap' }}>
                  ${thumb && html`<${Cover} index=${t.index} id=${t.cover} size=${40} />`}
                  <div><div>${t.title || t.file}</div><div class="muted small">${t.artist}</div></div>
                </div></td>
                <td class="mono muted">${fmtLength(t.duration)}</td>
                <td class="muted">${t.format}</td>
                <td><span class="row-wrap">
                  <${StatusBadge} status=${t.status} />
                  <${ScriptBadge} script=${t.script} />
                  ${t.instrumental && t.status === 'none' && html`<span class="badge b-instr">INSTRUMENTAL?</span>`}
                </span></td>
                <td class="open-cell">
                  <button class="btn btn-flat btn-icon" aria-label=${'Open ' + (t.title || t.file)} title="Open"
                    onClick=${(e) => { e.stopPropagation(); onOpen(t.index); }}><${Icon} name="right" /></button>
                </td>
              </tr>`;
          })}
        </tbody>
      </table>
    </div>
  </div>`;
}

function MiniPlayer({ track, preview, audio, onPlay, onOpen, onChange, onFailed, onClose }) {
  return html`<div class="mini-player" role="region" aria-label="Preview">
    <audio key=${track.index} ref=${audio} src=${`/api/tracks/${track.index}/audio`} autoplay
      onPlay=${() => onChange({ playing: true })} onPause=${() => onChange({ playing: false })}
      onEnded=${() => onChange({ playing: false })}
      onLoadedMetadata=${(e) => onChange({ duration: e.target.duration })}
      onTimeUpdate=${(e) => onChange({ pos: e.target.currentTime })} onError=${onFailed}></audio>
    <${Cover} index=${track.index} id=${track.cover} size=${44} />
    <button class="btn btn-primary btn-round play" onClick=${onPlay} disabled=${!!preview.error}
      aria-label=${preview.playing ? 'Pause' : 'Play'}><${PlayIcon} playing=${preview.playing} /></button>
    <div class="grow stack-tight" style=${{ gap: '2px' }}>
      <button class="btn-link mini-title" onClick=${onOpen}>${track.title || track.file}</button>
      ${preview.error
        ? html`<span class="small" style=${{ color: 'var(--warn)' }}>${preview.error}</span>`
        : html`<span class="muted small">${track.artist}</span>`}
    </div>
    ${!preview.error && html`
      <span class="mono small muted">${fmtClock(preview.pos)} / ${fmtClock(preview.duration)}</span>
      <label class="sr" for="preview-seek">Position</label>
      <input id="preview-seek" type="range" min="0" max=${preview.duration || 0} step="0.5" value=${preview.pos}
        onInput=${(e) => { if (audio.current) audio.current.currentTime = Number(e.target.value); }}
        style=${{ flex: '0 1 260px', minWidth: '100px' }} />`}
    <button class="btn btn-flat btn-icon" onClick=${onClose} aria-label="Close the preview"><${Icon} name="x" /></button>
  </div>`;
}

// --- one track ------------------------------------------------------------------------
function TrackView({ library, index, notify, onOpen, onHome, onUpdated, onNext, onSave, onEdit, preview, onPlay,
  romanize, onRomanize }) {
  const [track, setTrack] = useState(null);
  const [error, setError] = useState('');
  const [searching, setSearching] = useState(false);
  const total = library.tracks.length;

  const load = () => api(`/tracks/${index}`).then(setTrack).catch((err) => setError(err.message));
  useEffect(() => { load(); }, [index]);

  // Saving without moving on shows the track again, now with its lyrics.
  const saveHere = async (lyrics, message, next) => {
    if ((await onSave(lyrics, message, next)) && !next) {
      setSearching(false);
      load();
    }
  };

  const remove = async () => {
    if (!confirm(`Delete the lyrics from “${track.label}”?`)) return;
    const sidecar = track.staleSidecar && !track.writesSidecar
      ? confirm(`Also delete '${track.staleSidecar}'? Most players would keep showing those lyrics.`)
      : false;
    try {
      const result = await api(`/tracks/${index}/delete`, { sidecar });
      setTrack(result.track);
      onUpdated(result.track);
      notify('Lyrics deleted.');
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const summary = library.tracks[index];
  return html`<div class="split">
    <aside class="card side" aria-label="Tracks">
      <div class="label" style=${{ padding: '8px 10px' }}>${library.name}</div>
      ${library.tracks.map((t) => html`<button key=${t.index} class=${'list-row' + (t.index === index ? ' current' : '')}
        onClick=${() => onOpen(t.index)} aria-current=${t.index === index ? 'true' : null}>
        <span class="mono muted small" style=${{ width: '22px' }}>${t.index + 1}</span>
        <span class="title">${t.title || t.file}</span>
        <${StatusBadge} status=${t.status} short=${true} />
      </button>`)}
    </aside>
    <section class="content stack">
      <div class="spread">
        <div class="row-wrap" style=${{ gap: '16px', flexWrap: 'nowrap', minWidth: 0 }}>
        <${Cover} index=${index} id=${summary.cover} size=${96} />
        <div class="stack-tight" style=${{ minWidth: 0 }}>
          <span class="label">Track ${index + 1} of ${total}</span>
          <h1>${summary.title || summary.file}</h1>
          <span class="muted">${[summary.artist, summary.album, fmtLength(summary.duration), summary.format].filter(Boolean).join(' · ')}</span>
          <span class="muted small mono">${summary.group ? summary.group + '/' : ''}${summary.file}</span>
        </div>
        </div>
        <div class="row-wrap">
          <button class="btn" onClick=${() => onPlay(index)}>
            <${PlayIcon} playing=${preview && preview.index === index && preview.playing} />
            ${preview && preview.index === index && preview.playing ? 'Pause' : 'Listen'}</button>
          <button class="btn" disabled=${index === 0} onClick=${() => onOpen(index - 1)}><${Icon} name="left" /> Previous</button>
          <button class="btn" onClick=${onNext}>Next <${Icon} name="right" /></button>
        </div>
      </div>
      ${error && html`<p class="note note-error">${error}</p>`}
      ${!track && !error && html`<p class="muted">Reading the track…</p>`}
      ${track && html`
        ${track.error && html`<p class="note note-error">The file could not be read: ${track.error}</p>`}
        ${track.status === 'incomplete' && html`<p class="note note-warn">The title or artist tag is missing, and lyrembed searches with both. Enter them below to search anyway.</p>`}
        ${track.instrumentalHint && html`<p class="note note-gray">♪ Probably instrumental: ${track.instrumentalHint}.</p>`}
        ${track.notice && html`<p class="note note-warn">${track.notice}</p>`}
        ${track.lyrics && html`<div class="card pad stack">
          <div class="row-wrap">
            <h2 style=${{ marginRight: '6px' }}>Embedded lyrics</h2>
            <${StatusBadge} status=${track.status} short=${true} />
            <${ScriptBadge} script=${track.script} />
          </div>
          <div class="lyrics-preview mono">${track.lyrics}</div>
          <div class="row-wrap">
            <button class="btn btn-primary" onClick=${() => onEdit(track.lyrics, 'Embedded')}><${Icon} name="pencil" /> Check and edit</button>
            <button class="btn" disabled=${searching} onClick=${() => setSearching(true)}><${Icon} name="search" /> Search for other versions</button>
            <button class="btn btn-danger" onClick=${remove}><${Icon} name="trash" /> Delete lyrics…</button>
          </div>
        </div>`}
        ${!track.error && (!track.lyrics || searching) && html`<${SearchPanel} key=${'s' + index} track=${track}
          onSave=${saveHere} onEdit=${onEdit} notify=${notify} romanize=${romanize} onRomanize=${onRomanize}
          below=${library.romanizeBelow} />`}
      `}
    </section>
  </div>`;
}

function SearchPanel({ track, onSave, onEdit, notify, romanize, onRomanize, below }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const [showUnrelated, setShowUnrelated] = useState(false);
  const [title, setTitle] = useState(track.title);
  const [artist, setArtist] = useState(track.searchArtist);
  const [custom, setCustom] = useState(false);
  const pending = useRef(null);

  const run = async (body = {}) => {
    if (pending.current) pending.current.abort();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    try {
      const data = await api(`/tracks/${track.index}/search`, body, controller.signal);
      setResult(data);
      setShowUnrelated(false);
      setSelected(data.highlight || (data.candidates.length ? data.candidates[0].number : null));
    } catch (err) {
      if (err.name !== 'AbortError') setError(err.message);
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  };
  const cancel = () => {
    if (pending.current) pending.current.abort();
    pending.current = null;
    setBusy(false);
    setError('Search cancelled. Search again to start it over.');
  };
  useEffect(() => {
    if (track.status !== 'incomplete') run();
    return () => { if (pending.current) pending.current.abort(); };
  }, []);

  const query = custom ? { title, artist } : {};
  const all = result ? result.candidates.concat(showUnrelated ? result.unrelated : []) : [];
  const chosen = all.find((c) => c.number === selected);
  const source = (c) => `${c.providerName}: ${c.artist ? c.artist + ' - ' : ''}${c.title}`;
  const [saving, setSaving] = useState(false);
  // With "Romanize when saving": what batch mode's auto_romanize does, on the
  // server, so the page and --auto never differ.
  const saveChosen = async (next) => {
    let lyrics = chosen.text;
    let note = '';
    setSaving(true);
    try {
      if (romanize && chosen.script !== 'latin') {
        const r = await api('/auto-romanize', { lyrics });
        if (r.blocked) {
          notify(`Not saved: ${r.blocked}, so the lyrics can't be romanized. Untick "Romanize when saving" to save them as they are.`, 'warn');
          return;
        }
        if (r.lyrics) {
          lyrics = r.lyrics;
          note = `, romanized (${r.language})`;
        }
      }
      await onSave(lyrics, `Saved lyrics from ${source(chosen)}${note}.`, next);
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  return html`<div class="stack">
    <div class="row-wrap">
      <h2 style=${{ marginRight: '8px' }}>Search results</h2>
      ${result && result.cached && !busy && html`<span class="muted small">from this session's cache</span>`}
      ${busy && html`<span class="row-wrap muted small"><span class="spinner" aria-hidden="true"></span>
        Searching LRCLIB, NetEase and QQ Music… <button class="btn-link" onClick=${cancel}>Cancel</button></span>`}
    </div>
    ${error && html`<p class="note note-warn">${error}</p>`}
    ${result && !busy && html`
      ${Object.entries(result.errors).map(([provider, messages]) => messages.map((m) =>
        html`<p class="note note-warn small">${provider}: ${m}</p>`))}
      ${Object.keys(result.rateLimited).length > 0 && html`<p class="note note-warn small">
        ${Object.keys(result.rateLimited).join(', ')} is limiting requests right now. Search again in a minute.</p>`}
      ${result.fallback.length > 0 && html`<p class="note note-info small">
        No synced lyrics from LRCLIB, NetEase or QQ Music - also searched ${result.fallback.join(' and ')}.</p>`}
      ${result.instrumental && html`<p class="note note-gray">♪ Marked as instrumental by ${result.instrumental} - this track most likely has no lyrics.</p>`}
    `}
    <div class="results">
      <div class="card list">
        ${result && !all.length && !busy && html`<p class="muted" style=${{ padding: '10px 14px' }}>No results match this track's title and artist.</p>`}
        ${all.map((c) => html`<button key=${c.number} class=${'list-row cand' + (c.number === selected ? ' selected' : '')}
          onClick=${() => setSelected(c.number)}>
          <span class="key">${c.number}</span>
          <span class="body">
            <span class="row-wrap">
              <span class=${'badge p-' + c.provider}>${c.providerName}</span>
              <span class=${'badge ' + (c.synced ? 'b-sync' : 'b-plain')}>${c.synced ? 'SYNC' : 'PLAIN'}</span>
              <${ScriptBadge} script=${c.script} />
              ${c.number === result.highlight && html`<span class="best">Best match</span>`}
              ${c.exact && c.number !== result.highlight && html`<span class="best">Exact match</span>`}
              ${c.mismatch.length > 0 && html`<span class="flag-bad">≠ ${c.mismatch.join('/')}</span>`}
            </span>
            <span><b>${c.title}</b> <span class="muted">– ${c.artist || 'unknown artist'}</span></span>
            <span class="muted small">${lengthLabel(c)} · ${c.album}${c.unverified ? ' · not verified' : ''}</span>
          </span>
        </button>`)}
        ${result && result.unrelated.length > 0 && html`<button class="btn-link" style=${{ padding: '8px 14px', textAlign: 'left' }}
          onClick=${() => setShowUnrelated(!showUnrelated)}>
          ${showUnrelated ? 'Hide' : 'Show'} ${result.unrelated.length} result${result.unrelated.length === 1 ? '' : 's'} for other titles or artists</button>`}
        <form class="query stack-tight" onSubmit=${(e) => { e.preventDefault(); setCustom(true); run({ title, artist }); }}>
          <span class="label">Search with other terms</span>
          <div class="row-wrap">
            <label class="sr" for="q-title">Title</label>
            <input id="q-title" type="text" placeholder="Title" value=${title} onInput=${(e) => setTitle(e.target.value)} style=${{ flex: '2 1 160px' }} />
            <label class="sr" for="q-artist">Artist</label>
            <input id="q-artist" type="text" placeholder="Artist" value=${artist} onInput=${(e) => setArtist(e.target.value)} />
            <button class="btn" type="submit" disabled=${busy}><${Icon} name="search" /> Search</button>
          </div>
          <div class="row-wrap">
            <button class="btn-link" type="button" disabled=${busy} onClick=${() => run({ ...query, refresh: true })}>Search again, without the cache</button>
            ${result && result.unsearchedAliases.length > 0 && html`<button class="btn-link" type="button" disabled=${busy}
              onClick=${() => run({ ...query, aliases: true })}>Also search as ${result.unsearchedAliases.join(', ')}</button>`}
            <button class="btn-link" type="button" onClick=${() => onEdit('', 'Typed in')}>Type or paste lyrics yourself</button>
          </div>
        </form>
      </div>

      ${chosen && html`<div class="card pad stack preview">
        <div class="row-wrap">
          <span class="label" style=${{ marginRight: '6px' }}>Preview</span>
          <span class=${'badge p-' + chosen.provider}>${chosen.providerName}</span>
          <span class=${'badge ' + (chosen.synced ? 'b-sync' : 'b-plain')}>${chosen.synced ? 'SYNC' : 'PLAIN'}</span>
          <${ScriptBadge} script=${chosen.script} />
        </div>
        ${chosen.url && html`<a href=${chosen.url} target="_blank" rel="noopener noreferrer" class="small row-wrap">
          The lyrics' page at ${chosen.providerName} <${Icon} name="link" size=${14} /></a>`}
        <div class="lyrics-preview mono">${chosen.text}</div>
        <label class="check small">
          <input type="checkbox" checked=${romanize} disabled=${chosen.script === 'latin'} onChange=${() => onRomanize(!romanize)} />
          <span><b>Romanize when saving</b>${chosen.script === 'latin' ? html` <span class="muted">(already Latin)</span>` : ''}<br />
            <span class="muted">${'Detected script; ' + (below ? 'the original stays, the romanized line goes below it' : 'romanized lines replace the original')
              + ' (Settings: romanization layout).'}</span></span>
        </label>
        <div class="row-wrap">
          <button class="btn btn-primary" disabled=${saving} onClick=${() => saveChosen(true)}><${Icon} name="save" /> Save and go to the next track</button>
          <button class="btn" disabled=${saving} onClick=${() => saveChosen(false)}><${Icon} name="save" /> Save</button>
          <button class="btn" onClick=${() => onEdit(chosen.text, chosen.providerName, all, chosen.number)}><${Icon} name="pencil" /> Check and edit</button>
        </div>
      </div>`}
    </div>
  </div>`;
}

// --- bulk actions ------------------------------------------------------------------------
const ACTIONS = {
  fetch: {
    label: 'Fetch lyrics automatically',
    about: 'Saves the confident match for each track, like lyrembed --auto. Tracks that already have lyrics are skipped '
      + 'unless you allow replacing them; instrumentals and tracks with incomplete tags are skipped too.',
  },
  romanize: {
    label: 'Romanize embedded lyrics',
    about: 'Romanizes each track in the script lyrembed detects; Han characters nothing else decides are read as Chinese. '
      + 'The layout follows the setting "romanization layout". The previous lyrics are backed up first.',
  },
  sidecars: {
    label: 'Write .lrc files from embedded lyrics',
    about: 'Writes each track\'s embedded lyrics to a .lrc file next to it. A differing .lrc file is backed up first.',
  },
  delete: {
    label: 'Delete embedded lyrics',
    about: 'Deletes the embedded lyrics. They are backed up first.',
  },
};

function rowBadge(row) {
  if (row.kind === 'pause') return ['b-plain', 'WAITING'];
  if (row.kind === 'notice') return ['b-plain', 'NOTE'];
  if (row.result === 'done') return ['b-sync', 'DONE'];
  if (row.result === 'planned') return ['b-script', 'WOULD SAVE'];
  if (row.result === 'failed') return ['b-none', 'FAILED'];
  return [row.notable ? 'b-plain' : 'b-instr', 'SKIPPED'];
}

function BulkView({ library, job, onJob, onOpen, notify }) {
  const [action, setAction] = useState('fetch');
  const [chosen, setChosen] = useState(() => new Set(library.tracks.map((t) => t.index)));
  const [overwrite, setOverwrite] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [romanize, setRomanize] = useState('setting');
  const [sidecars, setSidecars] = useState(false);
  const [starting, setStarting] = useState(false);

  const tracks = library.tracks;
  const pick = (test) => setChosen(new Set(tracks.filter(test).map((t) => t.index)));
  const toggle = (index) => {
    const next = new Set(chosen);
    if (next.has(index)) next.delete(index); else next.add(index);
    setChosen(next);
  };
  const withSidecar = tracks.filter((t) => chosen.has(t.index) && t.hasSidecar).length;

  const start = async () => {
    if (action === 'delete' && !confirm(`Delete the lyrics of ${chosen.size} track${chosen.size === 1 ? '' : 's'}?`)) return;
    setStarting(true);
    try {
      const result = await api('/jobs', {
        action, tracks: [...chosen], overwrite, dryRun,
        romanize: romanize === 'setting' ? null : romanize === 'yes', deleteSidecars: sidecars,
      });
      onJob(result.job);
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setStarting(false);
    }
  };

  if (job) return html`<${JobView} job=${job} onOpen=${onOpen} onDone=${() => onJob(null)} />`;

  return html`<div class="stack">
    <div class="stack-tight">
      <span class="label">Bulk actions · ${library.name}</span>
      <h1>Run one action on many tracks</h1>
      <span class="muted">Runs without further questions; you can stop it between tracks.</span>
    </div>
    <div class="card pad stack">
      <div class="stack-tight">
        ${Object.entries(ACTIONS).map(([key, a]) => html`<label class="check">
          <input type="radio" name="action" checked=${action === key} onChange=${() => setAction(key)} />
          <span><b>${a.label}</b>${action === key ? html`<br /><span class="muted small">${a.about}</span>` : ''}</span>
        </label>`)}
      </div>
      ${action === 'fetch' && html`<div class="stack-tight options">
        <label class="check"><input type="checkbox" checked=${overwrite} onChange=${() => setOverwrite(!overwrite)} />
          <span>Also replace lyrics that are already there <span class="muted">(backed up first)</span></span></label>
        <label class="check"><input type="checkbox" checked=${dryRun} onChange=${() => setDryRun(!dryRun)} />
          <span>Dry run: search and report, write nothing</span></label>
        <label class="row-wrap small"><span>Romanize what it saves</span>
          <select value=${romanize} onChange=${(e) => setRomanize(e.target.value)}>
            <option value="setting">as the setting says</option><option value="yes">yes</option><option value="no">no</option>
          </select></label>
      </div>`}
      ${action === 'delete' && withSidecar > 0 && html`<label class="check options">
        <input type="checkbox" checked=${sidecars} onChange=${() => setSidecars(!sidecars)} />
        <span>Also delete the .lrc files next to ${withSidecar} of these tracks <span class="muted">(most players would keep showing them)</span></span>
      </label>`}
    </div>

    <div class="card pad stack-tight">
      <div class="spread" style=${{ alignItems: 'center' }}>
        <span class="label">Tracks: ${chosen.size} of ${tracks.length}</span>
        <div class="row-wrap">
          <button class="btn btn-small" onClick=${() => pick(() => true)}>All</button>
          <button class="btn btn-small" onClick=${() => pick(() => false)}>None</button>
          <button class="btn btn-small" onClick=${() => pick((t) => t.status === 'none')}>Without lyrics</button>
          <button class="btn btn-small" onClick=${() => pick((t) => t.status === 'sync' || t.status === 'plain')}>With lyrics</button>
        </div>
      </div>
      <div class="pick-list">
        ${tracks.map((t) => html`<label class="check pick" key=${t.index}>
          <input type="checkbox" checked=${chosen.has(t.index)} onChange=${() => toggle(t.index)} />
          <span class="mono muted small" style=${{ width: '24px' }}>${t.index + 1}</span>
          <span class="grow">${t.title || t.file} <span class="muted small">${t.artist}</span></span>
          <${StatusBadge} status=${t.status} short=${true} />
        </label>`)}
      </div>
    </div>
    <div class="row-wrap">
      <button class="btn btn-primary" disabled=${!chosen.size || starting} onClick=${start}>
        <${Icon} name="layers" /> ${action === 'fetch' && dryRun ? 'Start the dry run' : 'Start'}</button>
    </div>
  </div>`;
}

function JobView({ job, onOpen, onDone }) {
  const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  const t = job.tally;
  return html`<div class="stack">
    <div class="stack-tight">
      <span class="label">Bulk actions${job.dryRun ? ' · dry run' : ''}</span>
      <h1>${job.label}</h1>
    </div>
    <div class="card pad stack">
      <div class="spread" style=${{ alignItems: 'center' }}>
        <b>${job.running ? (job.stopping ? 'Stopping after this track…' : 'Running…') : job.stopped ? 'Stopped' : 'Done'}</b>
        <span class="mono muted small">${job.processed} / ${job.total}</span>
      </div>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${percent}>
        <div style=${{ width: percent + '%' }}></div>
      </div>
      ${job.current && html`<span class="row-wrap muted small"><span class="spinner" aria-hidden="true"></span>${job.current}</span>`}
      ${job.error && html`<p class="note note-error">${job.error}</p>`}
      <div class="job-rows">
        ${job.rows.map((row, i) => {
          const [cls, label] = rowBadge(row);
          return html`<div class="job-row" key=${i}>
            <span class=${'badge ' + cls}>${label}</span>
            <span class="grow">
              ${row.index !== null && row.index !== undefined
                ? html`<button class="btn-link" onClick=${() => onOpen(row.index)}>${row.prefix}</button>`
                : html`<span>${row.prefix}</span>`}
              <span class="muted"> ${row.message}</span>
              ${(row.warnings || []).map((w) => html`<br /><span class="small" style=${{ color: 'var(--warn)' }}>${w}</span>`)}
            </span>
          </div>`;
        })}
      </div>
      ${!job.running && html`<div class="bar" style=${{ borderTop: '1px solid var(--border)', paddingTop: '14px' }}>
        <span class="grow">${job.dryRun ? 'Would save' : 'Done'}: <b>${t.done}</b> · Skipped: <b>${t.skipped}</b> · Failed: <b>${t.failed}</b>
          ${job.backupRoot && html`<br /><span class="muted small">The previous lyrics were backed up to <span class="mono">${job.backupRoot}</span></span>`}</span>
        <button class="btn" onClick=${onDone}><${Icon} name="refresh" /> New run</button>
      </div>`}
      ${job.running && html`<div><button class="btn btn-danger" disabled=${job.stopping} onClick=${() => api('/jobs/current/stop', {})}><${Icon} name="stop" /> Stop</button></div>`}
    </div>
  </div>`;
}

// --- settings ------------------------------------------------------------------------------
const NAMES = [[/\bid3 v2\b/i, 'ID3v2'], [/\buslt\b/i, 'USLT'], [/\blrc\b/i, '.lrc'], [/\blrclib\b/i, 'LRCLIB'],
  [/\bnetease\b/i, 'NetEase'], [/\bqqmusic\b/i, 'QQ Music'], [/\bmegalobiz\b/i, 'Megalobiz'],
  [/\bmusicbrainz\b/i, 'MusicBrainz'], [/\bapi\b/i, 'API']];
const humanize = (key) => NAMES.reduce((label, [pattern, name]) => label.replace(pattern, name),
  key.replace(/_/g, ' ')).replace(/^./, (c) => c.toUpperCase());

function fieldText(setting, value) {
  if (setting.kind === 'list') return value.join('\n');
  if (setting.kind === 'bool') return value;
  return String(value);
}

// The typed text back into the setting's type; throws a message when it does not fit.
function fieldValue(setting, raw) {
  if (setting.kind === 'bool') return raw;
  if (setting.kind === 'list') return raw.split('\n').map((s) => s.trim()).filter(Boolean);
  if (setting.kind === 'int' || setting.kind === 'float') {
    const number = Number(String(raw).trim());
    if (String(raw).trim() === '' || Number.isNaN(number)) throw new Error('needs a number');
    if (setting.kind === 'int' && !Number.isInteger(number)) throw new Error('needs a whole number');
    if (setting.minimum !== null && number < setting.minimum) throw new Error(`must be at least ${setting.minimum}`);
    return number;
  }
  return raw;
}

function SettingsView({ notify, update, onCheckUpdates }) {
  const [data, setData] = useState(null);
  const [edits, setEdits] = useState({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => { api('/settings').then(setData).catch((err) => setError(err.message)); }, []);
  if (!data) return error ? html`<p class="note note-error">${error}</p>` : html`<p class="muted">Loading the settings…</p>`;

  const id = (s) => `${s.section}.${s.key}`;
  const all = data.groups.flatMap((g) => g.settings);
  const problems = {};
  const changes = [];
  for (const s of all) {
    if (!(id(s) in edits)) continue;
    try {
      const value = fieldValue(s, edits[id(s)]);
      if (JSON.stringify(value) !== JSON.stringify(s.value)) changes.push({ section: s.section, key: s.key, value });
    } catch (err) {
      problems[id(s)] = err.message;
    }
  }
  const set = (s, raw) => setEdits({ ...edits, [id(s)]: raw });

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const result = await api('/settings', { changes });
      setData(result);
      setEdits({});
      notify(`Saved to ${result.path}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const field = (s) => {
    const current = id(s) in edits ? edits[id(s)] : fieldText(s, s.value);
    const changed = changes.some((c) => c.section === s.section && c.key === s.key);
    const notDefault = JSON.stringify(s.value) !== JSON.stringify(s.default);
    const control = s.kind === 'bool'
      ? html`<input type="checkbox" class="switch" role="switch" id=${id(s)} checked=${current} onChange=${(e) => set(s, e.target.checked)} />`
      : s.choices
        ? html`<select id=${id(s)} value=${String(current)} onChange=${(e) => set(s, s.kind === 'int' ? Number(e.target.value) : e.target.value)}>
            ${s.choices.map((c) => html`<option value=${String(c)}>${String(c)}</option>`)}</select>`
        : s.kind === 'list'
          ? html`<textarea id=${id(s)} class="mono" rows=${Math.max(2, String(current).split('\n').length)} value=${current}
              onInput=${(e) => set(s, e.target.value)}></textarea>`
          : html`<input type="text" id=${id(s)} class=${s.section === 'cleaning' ? 'mono' : ''} value=${current}
              inputmode=${s.kind === 'int' || s.kind === 'float' ? 'decimal' : null} onInput=${(e) => set(s, e.target.value)} />`;
    return html`<div class="setting" key=${id(s)}>
      <div class="stack-tight grow">
        <label for=${id(s)}><b>${humanize(s.key)}</b> <span class="mono muted small">${id(s)}</span>
          ${changed && html` <span class="badge b-script">changed</span>`}</label>
        ${s.help && html`<span class="muted small">${s.help}</span>`}
        ${s.kind !== 'bool' && control}
        ${s.kind === 'list' && html`<span class="muted small">One per line.</span>`}
        ${problems[id(s)] && html`<span class="small" style=${{ color: 'var(--bad)' }}>${humanize(s.key)} ${problems[id(s)]}.</span>`}
        ${notDefault && html`<span><button class="btn-link" onClick=${() => set(s, fieldText(s, s.default))}>Back to the default (${s.kind === 'list' ? s.default.join(', ') || 'empty' : String(s.default) || 'empty'})</button></span>`}
      </div>
      ${s.kind === 'bool' && control}
    </div>`;
  };

  return html`<div class="stack">
    <div class="stack-tight">
      <h1>Settings</h1>
      <span class="muted">Saved to <span class="mono">${data.path}</span>. Options given on the command line (such as --sidecar) still apply to this run.</span>
      <div class="row-wrap"><span class="muted">This is lyrembed ${update ? update.current : ''}.</span>
        <button class="btn btn-small" disabled=${!!(update && ['checking', 'installing'].includes(update.state))}
          onClick=${onCheckUpdates}><${Icon} name="refresh" /> ${update && update.state === 'checking' && !update.quiet ? 'Looking…' : 'Check for updates'}</button></div>
    </div>
    ${data.groups.map((g, i) => (i < 2
      ? html`<section class="group" key=${g.name}><h2>${g.name}</h2><div class="card">${g.settings.map(field)}</div></section>`
      : html`<details class="card" key=${g.name}><summary><h2>${g.name}</h2></summary>${g.settings.map(field)}</details>`))}
    <${AliasEditor} notify=${notify} />
    <div class="card pad bar save-bar">
      <span class="grow">${changes.length ? `${changes.length} unsaved change${changes.length === 1 ? '' : 's'}` : 'No unsaved changes'}
        ${Object.keys(problems).length > 0 && html`<span style=${{ color: 'var(--bad)' }}> · fix the marked fields first</span>`}</span>
      ${error && html`<span class="note note-error small">${error}</span>`}
      <button class="btn" disabled=${!Object.keys(edits).length} onClick=${() => setEdits({})}><${Icon} name="undo" /> Undo changes</button>
      <button class="btn btn-primary" disabled=${!changes.length || Object.keys(problems).length > 0 || saving} onClick=${save}><${Icon} name="save" /> Save settings</button>
    </div>
  </div>`;
}

function AliasEditor({ notify }) {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState('');

  const asText = (groups) => groups.map((g) => g.join(' = ')).join('\n');
  useEffect(() => {
    api('/aliases').then((d) => { setData(d); setDraft(asText(d.user)); }).catch((err) => setError(err.message));
  }, []);
  if (!data) return error ? html`<p class="note note-error">${error}</p>` : null;

  const groups = draft.split('\n').map((line) => line.split('=').map((n) => n.trim()).filter(Boolean)).filter((g) => g.length);
  const changed = asText(groups) !== asText(data.user);
  const lonely = groups.find((g) => g.length === 1);
  const needle = filter.trim().toLowerCase();
  const shown = needle ? data.builtin.filter((g) => g.some((n) => n.toLowerCase().includes(needle))) : data.builtin;

  const save = async () => {
    setError('');
    try {
      const d = await api('/aliases', { groups });
      setData(d);
      setDraft(asText(d.user));
      notify('Artist aliases saved.');
    } catch (err) {
      setError(err.message);
    }
  };

  return html`<section class="group">
    <h2>Artist aliases</h2>
    <div class="card pad stack">
    <span class="muted small">Names that mean the same artist, so a search for one finds the others, for example
      “Hoshino Gen = 星野源”. Your groups are added to the built-in ones; a group sharing a name with one is merged into it.</span>
    <label for="aliases" class="small"><b>Your groups</b>, one per line, names separated by “=”</label>
    <textarea id="aliases" class="mono" rows=${Math.max(3, groups.length + 1)} value=${draft} onInput=${(e) => setDraft(e.target.value)}></textarea>
    ${lonely && html`<span class="small" style=${{ color: 'var(--bad)' }}>“${lonely[0]}” needs at least one other name.</span>`}
    ${error && html`<p class="note note-error small">${error}</p>`}
    <div><button class="btn" disabled=${!changed || !!lonely} onClick=${save}><${Icon} name="save" /> Save aliases</button></div>
    <details>
      <summary class="small">${data.builtin.length} built-in groups</summary>
      <div class="stack-tight" style=${{ marginTop: '10px' }}>
        <label class="sr" for="alias-filter">Filter</label>
        <input id="alias-filter" type="text" placeholder="Filter by name" value=${filter} onInput=${(e) => setFilter(e.target.value)} />
        <div class="alias-list small">${shown.map((g) => html`<div>${g.join(' = ')}</div>`)}</div>
      </div>
    </details>
    </div>
  </section>`;
}

// --- editing ----------------------------------------------------------------------------
const EMBEDDED = 'Embedded';

// Where the lyrics in the editor came from: the file, a search result
// (one of *versions*), or typed in.
function originOf(label, version) {
  return version
    ? { label: version.providerName, provider: version.provider, url: version.url, number: version.number, text: version.text }
    : { label, provider: '', url: '', number: null, text: '' };
}

function timeLabel(value) {
  return formatStamp(value).slice(1, -1);
}

function Editor({ track, initial, source, versions, chosen, dirty, onBack, onNext, onSave }) {
  const firstVersion = versions && versions.find((v) => v.number === chosen);
  const [origin, setOrigin] = useState(() => ({ ...originOf(source, firstVersion), text: initial }));
  const [text, setText] = useState(initial);
  const [shift, setShift] = useState(0);
  const [mode, setMode] = useState(initial.trim() ? 'lines' : 'text');
  const [info, setInfo] = useState(null);
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState(null);
  const [romanizing, setRomanizing] = useState(false);
  // player
  const audio = useRef(null);
  const [playback, setPlayback] = useState(null);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(track.duration || 0);
  const [audioError, setAudioError] = useState('');
  const rows = useRef({});

  const parsed = useMemo(() => parse(text), [text]);
  const final = shift ? applyShift(text, shift) : text;
  // Lyrics taken from a search are not in the file yet, edited or not.
  const unsaved = origin.label !== EMBEDDED || final !== initial;
  dirty.current = unsaved && final.trim() !== '';
  const timed = parsed.lines.some((l) => l.times.length);
  const lit = currentLines(parsed, pos, shift);
  const litKey = [...lit].join(',');

  useEffect(() => {
    api(`/tracks/${track.index}`).then((t) => setPlayback(t.playback)).catch((err) => setAudioError(err.message));
  }, [track.index]);

  useEffect(() => {
    const timer = setTimeout(() => api('/inspect', { lyrics: text }).then(setInfo).catch(() => {}), 300);
    return () => clearTimeout(timer);
  }, [text]);

  // timeupdate fires only a few times a second; follow playback per frame.
  useEffect(() => {
    if (!playing) return undefined;
    let frame;
    const tick = () => {
      if (audio.current) setPos(audio.current.currentTime);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  useEffect(() => {
    if (!playing || !lit.size) return;
    const row = rows.current[Math.min(...lit)];
    if (row) row.scrollIntoView({ block: 'nearest' });
  }, [litKey]);

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) a.play().catch((err) => setAudioError(`The browser could not play this file: ${err.message}`));
    else a.pause();
  };
  const seek = (target) => {
    const a = audio.current;
    if (!a) return;
    a.currentTime = Math.max(0, Math.min(target, a.duration || target));
    setPos(a.currentTime);
  };
  const nextLyricLine = (after) => {
    const next = parsed.lines.find((l) => l.n > after && !l.meta && l.text.trim());
    return next ? next.n : null;
  };
  // "Now": the line starts at the current position, then the next one is up.
  const stamp = (n) => {
    if (n === null || n === undefined) return;
    const written = pos + parsed.offset / 1000 - shift / 1000;
    setText(stampLine(text, n, written));
    setSelected(nextLyricLine(n));
  };
  const pick = (line) => {
    setSelected(line.n);
    if (line.times.length && audio.current) {
      seek(playTime(nearestTime(line, pos, parsed, shift), parsed, shift));
    }
  };

  const toText = () => {
    // The text shows real timestamps: a pending shift goes into them.
    if (shift) { setText(applyShift(text, shift)); setShift(0); }
    setMode('text');
  };
  const switchVersion = (number) => {
    const version = versions.find((v) => v.number === Number(number));
    if (!version) return;
    if ((text !== origin.text || shift) && !confirm(`Replace your changes with version ${version.number}?`)) return;
    setOrigin(originOf('', version));
    setText(version.text);
    setShift(0);
    setSelected(null);
  };
  const save = async (next) => {
    setSaving(true);
    await onSave(final, next);
    setSaving(false);
  };

  // Keys while the line view is open; never while typing in a field.
  const keys = useRef(null);
  keys.current = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      if (final.trim() && !saving) save(false);
      return;
    }
    if (mode !== 'lines' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    if (e.key === ' ') { e.preventDefault(); toggle(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(pos - 5); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seek(pos + 5); }
    else if (e.key === 'n' || e.key === 'N') { e.preventDefault(); stamp(selected ?? nextLyricLine(-1)); }
  };
  useEffect(() => {
    const handler = (e) => keys.current(e);
    addEventListener('keydown', handler);
    return () => removeEventListener('keydown', handler);
  }, []);

  const canPlay = playback && playback.mode !== 'none';
  const sync = info && info.script ? info.synced : timed;

  return html`<div class="stack">
    <div class="spread" style=${{ alignItems: 'center' }}>
      <div class="row-wrap" style=${{ minWidth: 0 }}>
        <button class="btn" onClick=${onBack} aria-label="Back to the track"><${Icon} name="left" /> Back</button>
        <div class="stack-tight" style=${{ minWidth: 0 }}>
          <h1>${track.title || track.file}</h1>
          <span class="row-wrap">
            <span class=${'badge ' + (origin.provider ? 'p-' + origin.provider : 'b-incomplete')}>${origin.label}</span>
            <span class=${'badge ' + (sync ? 'b-sync' : 'b-plain')}>${sync ? 'SYNC' : 'PLAIN'}</span>
            <${ScriptBadge} script=${info && info.script} />
            ${origin.url && html`<a href=${origin.url} target="_blank" rel="noopener noreferrer" class="small">The lyrics' page at ${origin.label}</a>`}
          </span>
        </div>
      </div>
      ${versions && versions.length > 1 && html`<label class="row-wrap small">
        <span class="muted">Version</span>
        <select value=${origin.number ?? ''} onChange=${(e) => switchVersion(e.target.value)}>
          ${origin.number === null && html`<option value="">Edited text</option>`}
          ${versions.map((v) => html`<option value=${v.number}>${v.number}. ${v.providerName} · ${v.synced ? 'synced' : 'plain'} · ${lengthLabel(v)} · ${v.title}</option>`)}
        </select>
      </label>`}
    </div>

    <div class="card pad player">
      ${!playback && !audioError && html`<span class="muted small">Getting the track ready…</span>`}
      ${canPlay && html`
        <audio ref=${audio} src=${`/api/tracks/${track.index}/audio`} preload="metadata"
          onPlay=${() => setPlaying(true)} onPause=${() => setPlaying(false)} onEnded=${() => setPlaying(false)}
          onLoadedMetadata=${(e) => setDuration(e.target.duration)}
          onTimeUpdate=${(e) => { if (e.target.paused) setPos(e.target.currentTime); }}
          onError=${() => setAudioError('The browser could not play this file.')}></audio>
        <button class="btn btn-primary play" onClick=${toggle} aria-label=${playing ? 'Pause' : 'Play'}>
          ${playing
            ? html`<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"></rect><rect x="14" y="4" width="4" height="16" rx="1"></rect></svg>`
            : html`<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5Z"></path></svg>`}
        </button>
        <button class="btn btn-small" onClick=${() => seek(pos - 5)} aria-label="Back 5 seconds">−5 s</button>
        <button class="btn btn-small" onClick=${() => seek(pos + 5)} aria-label="Forward 5 seconds">+5 s</button>
        <span class="mono small" style=${{ minWidth: '110px' }}>${fmtClock(pos)} / ${fmtClock(duration)}</span>
        <label class="sr" for="seek">Position</label>
        <input id="seek" type="range" min="0" max=${duration || 0} step="0.1" value=${pos}
          onInput=${(e) => seek(Number(e.target.value))} style=${{ flex: '1 1 220px', minWidth: '140px' }} />
      `}
      ${playback && playback.note && html`<span class="muted small" style=${{ flexBasis: '100%' }}>${playback.note}</span>`}
      ${audioError && html`<span class="note note-warn small" style=${{ flexBasis: '100%' }}>${audioError}</span>`}
    </div>

    <div class="split">
      <div class="card content lines-box">
        ${mode === 'lines' && html`<div class="lines" role="list">
          ${parsed.lines.filter((l) => !l.meta && (l.text.trim() || l.times.length)).map((l) => {
            const on = lit.has(l.n);
            return html`<div role="listitem" key=${l.n} ref=${(el) => { rows.current[l.n] = el; }}
              class=${'line' + (on ? ' lit' : '') + (selected === l.n ? ' chosen' : '')}>
              <button class="line-main" onClick=${() => pick(l)}>
                <span class="mono stamp">${l.times.length ? l.times.map((t) => timeLabel(playTime(t, parsed, shift))).join(' ') : '--:--.--'}</span>
                <span class=${l.text.trim() ? 'words' : 'words muted'}>${l.text.trim() || '♪'}</span>
              </button>
              <button class="btn btn-small" disabled=${!canPlay} onClick=${() => stamp(l.n)}
                title="Start this line at the current position" aria-label="Start this line at the current position">Now</button>
            </div>`;
          })}
          ${!parsed.lines.some((l) => !l.meta && l.text.trim()) && html`<p class="muted" style=${{ padding: '12px' }}>No lyrics yet: switch to text to type or paste them.</p>`}
        </div>`}
        ${mode === 'text' && html`<div>
          <label class="sr" for="lyrics">Lyrics</label>
          <textarea id="lyrics" class="editor mono" spellcheck="false" value=${text}
            placeholder="Paste or type the lyrics. Lines like [01:23.45] make them synced."
            onInput=${(e) => setText(e.target.value)}></textarea>
        </div>`}
      </div>

      <aside class="side tools stack">
        <div class="card pad stack-tight">
          <span class="label">Timing</span>
          <div class="row-wrap">
            ${[-250, -50].map((d) => html`<button class="btn btn-small" disabled=${!timed} onClick=${() => setShift(shift + d)}>${'−' + Math.abs(d)}</button>`)}
            <span class="mono shift">${(shift > 0 ? '+' : shift < 0 ? '−' : '±') + Math.abs(shift)} ms</span>
            ${[50, 250].map((d) => html`<button class="btn btn-small" disabled=${!timed} onClick=${() => setShift(shift + d)}>+${d}</button>`)}
          </div>
          <span class="muted small">Moves all lines; negative is earlier. To fix one line, press <b>Now</b> on it while it should start.</span>
          ${parsed.offset !== 0 && html`<span class="muted small">The lyrics carry an [offset:${parsed.offset}] tag, already counted in the times shown. A timing change writes it into the timestamps.</span>`}
          <div><button class="btn btn-small" disabled=${!shift} onClick=${() => setShift(0)}><${Icon} name="undo" size=${14} /> Reset timing</button></div>
        </div>
        <div class="card pad stack-tight">
          <span class="label">Text</span>
          <div class="row-wrap">
            <button class="btn btn-small" disabled=${!text.trim()} onClick=${() => setRomanizing(!romanizing)}><${Icon} name="type" size=${14} /> Romanize…</button>
            ${mode === 'lines'
              ? html`<button class="btn btn-small" onClick=${toText}><${Icon} name="pencil" size=${14} /> Edit as text</button>`
              : html`<button class="btn btn-small" onClick=${() => setMode('lines')}><${Icon} name="list" size=${14} /> Back to the lines</button>`}
          </div>
          ${romanizing && html`<${Romanizer} text=${text} onDone=${(result) => { setText(result); setRomanizing(false); }}
            onCancel=${() => setRomanizing(false)} />`}
        </div>
        ${!timed && text.trim() && html`<div class="card pad stack-tight">
          <span class="label">Plain lyrics</span>
          <span class="small">No timestamps yet. Play the track and press <b>Now</b> (or N) on each line as it starts to make them synced.</span>
        </div>`}
        ${mode === 'lines' && canPlay && html`<p class="muted small">Keys: Space plays and pauses, ← and → jump 5 s, N starts the chosen line now.</p>`}
      </aside>
    </div>

    <div class="card pad bar">
      <div class="grow stack-tight">
        <b>${unsaved ? (origin.label === EMBEDDED ? 'Unsaved changes' : 'Not saved yet') : 'No changes'}</b>
        <span class="muted small">${unsaved ? 'Nothing is written to the file until you save. Ctrl+S saves.' : 'This is what the file has.'}</span>
      </div>
      <button class="btn" disabled=${(text === origin.text && !shift) || saving} onClick=${() => { setText(origin.text); setShift(0); }}><${Icon} name="undo" /> Undo changes</button>
      <button class="btn" disabled=${!final.trim() || !unsaved || saving} onClick=${() => save(false)}><${Icon} name="save" /> Save</button>
      <button class="btn btn-primary" disabled=${!final.trim() || saving} onClick=${() => (unsaved ? save(true) : onNext())}>
        <${Icon} name=${unsaved ? 'save' : 'right'} /> ${unsaved ? 'Save and go to the next track' : 'Next track'}</button>
    </div>
  </div>`;
}

function Romanizer({ text, onDone, onCancel }) {
  const [choices, setChoices] = useState(null);
  const [key, setKey] = useState('');
  const [han, setHan] = useState('zh');
  const [below, setBelow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api('/romanizers', { lyrics: text }).then((c) => {
      setChoices(c);
      setBelow(c.below);
      const usable = c.options.filter((o) => o.available);
      const detected = usable.find((o) => o.key === c.detected);
      setKey(detected ? detected.key : usable.length ? usable[0].key : '');
    }).catch((err) => setError(err.message));
  }, []);

  const apply = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await api('/romanize', { lyrics: text, key, hanDefault: han, keepOriginal: below });
      if (result.lyrics === text) setError('Nothing changed: the lyrics have nothing this romanization converts.');
      else onDone(result.lyrics);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!choices) return error ? html`<p class="note note-warn small">${error}</p>` : html`<p class="muted small">Loading…</p>`;
  return html`<div class="stack-tight romanizer">
    ${choices.options.map((o) => html`<label class=${'check' + (o.available ? '' : ' muted')}>
      <input type="radio" name="roman" disabled=${!o.available} checked=${key === o.key} onChange=${() => setKey(o.key)} />
      <span>${o.label}${o.key === choices.detected ? html` <b>(detected)</b>` : ''}${!o.available ? html`<br /><span class="small">Needs: pip install ${o.package}</span>` : ''}</span>
    </label>`)}
    ${key === 'mixed' && html`<div class="stack-tight">
      <span class="small"><b>Han characters</b> that nothing else decides:</span>
      <label class="check"><input type="radio" name="han" checked=${han === 'zh'} onChange=${() => setHan('zh')} /><span>Chinese → Pīnyīn</span></label>
      <label class="check"><input type="radio" name="han" checked=${han === 'ja'} onChange=${() => setHan('ja')} /><span>Japanese → Rōmaji</span></label>
    </div>`}
    <span class="small"><b>Layout</b></span>
    <label class="check"><input type="radio" name="layout" checked=${!below} onChange=${() => setBelow(false)} /><span>Replace the original</span></label>
    <label class="check"><input type="radio" name="layout" checked=${below} onChange=${() => setBelow(true)} /><span>Keep the original, romanized line below</span></label>
    ${error && html`<p class="note note-warn small">${error}</p>`}
    <div class="row-wrap">
      <button class="btn btn-primary btn-small" disabled=${!key || busy} onClick=${apply}>${busy ? 'Converting…' : 'Apply'}</button>
      <button class="btn btn-small" onClick=${onCancel}>Cancel</button>
    </div>
  </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
