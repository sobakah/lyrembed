// lyrembed's page. Everything it shows comes from the local server's JSON API
// (lyrembed/web.py), and every change to a file is a request to it.
import { html, render, useState, useEffect, useRef, useCallback, useMemo } from '/static/vendor/preact-htm.js';
import { parse, currentLines, applyShift, stampLine, formatStamp, playTime } from '/static/lrc.js';

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

function Icon({ name, size = 16 }) {
  const paths = {
    note: html`<path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle>`,
    left: html`<path d="m15 18-6-6 6-6"></path>`,
    right: html`<path d="m9 18 6-6-6-6"></path>`,
    up: html`<path d="m18 15-6-6-6 6"></path>`,
    folder: html`<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>`,
    link: html`<path d="M15 3h6v6"></path><path d="M10 14 21 3"></path><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>`,
  };
  return html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
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
  const previewAudio = useRef(null);
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
    api('/library')
      .then((lib) => { setLibrary(lib); if (!lib.folder) setPicker(true); })
      .catch((err) => setLoadError(err.message));
    // After a reload, a run that is still going shows again.
    api('/jobs/current').then((r) => { if (r.job && r.job.running) { setJob(r.job); setView({ name: 'bulk' }); } }).catch(() => {});
    // The server stops when the page is gone: it hears nothing for a while,
    // or the page says it is closing and does not come back (a reload does).
    const ping = setInterval(() => api('/ping', {}).catch(() => {}), 15000);
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
        if (!next) { setJob(null); return; }
        setJob({ ...next, rows: job.rows.concat(next.rows) });
        if (!next.running) api('/library').then(setLibrary).catch(() => {});
      } catch (err) {
        notify(err.message, 'error');
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

  const quit = async () => {
    if (!leaveOk()) return;
    await api('/quit', {}).catch(() => {});
    setStopped(true);
  };

  let body = null;
  if (picker || (library && !library.folder)) {
    body = html`<${FolderPicker} library=${library} onOpened=${opened}
      onCancel=${library && library.folder ? () => setPicker(false) : null} />`;
  } else if (library && view.name === 'library') {
    body = html`<${LibraryView} library=${library} onOpen=${(index) => go({ name: 'track', index })}
      preview=${preview} onPlay=${playPreview} />`;
  } else if (library && view.name === 'track') {
    body = html`<${TrackView} key=${view.index} library=${library} index=${view.index} notify=${notify}
      preview=${preview} onPlay=${playPreview}
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
  } else if (view.name === 'settings') {
    body = html`<${SettingsView} notify=${notify} />`;
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

  return html`
    <header class="topbar">
      <button class="brand" onClick=${() => go({ name: 'library' })} title="Back to the track list">
        <${Icon} name="note" size=${24} /> lyrembed
      </button>
      <nav class="row-wrap" aria-label="Main">
        ${[['library', 'Library'], ['bulk', 'Bulk actions'], ['settings', 'Settings']].map(([name, label]) => html`
          <button class=${'nav' + ((view.name === name || (name === 'library' && ['track', 'edit'].includes(view.name))) && !picker ? ' nav-on' : '')}
            disabled=${name !== 'settings' && !(library && library.folder)}
            onClick=${() => go({ name })}>${label}${name === 'bulk' && job && job.running ? ' ●' : ''}</button>`)}
      </nav>
      <span class="path mono">${library ? library.folder : ''}</span>
      <button class="btn btn-small" onClick=${() => { if (leaveOk()) setPicker(true); }}><${Icon} name="folder" /> Change folder…</button>
      <button class="btn btn-small" onClick=${quit}>Quit</button>
    </header>
    <main class=${preview ? 'with-player' : ''}>
      ${loadError && html`<p class="note note-error">${loadError}</p>`}
      ${body}
    </main>
    ${preview && library && library.tracks[preview.index] && html`<${MiniPlayer} track=${library.tracks[preview.index]}
      preview=${preview} audio=${previewAudio} onPlay=${() => playPreview(preview.index)}
      onOpen=${() => go({ name: 'track', index: preview.index })}
      onChange=${(change) => setPreview((p) => (p ? { ...p, ...change } : p))}
      onFailed=${() => previewFailed(preview.index)}
      onClose=${() => { if (previewAudio.current) previewAudio.current.pause(); setPreview(null); }} />`}
    <div class=${'toasts' + (preview ? ' above-player' : '')} aria-live="polite">
      ${toasts.map((t) => html`<div class=${'toast note-' + t.kind} key=${t.id}>
        <span>${t.message}</span>
        <button aria-label="Dismiss" onClick=${() => setToasts((list) => list.filter((x) => x.id !== t.id))}>×</button>
      </div>`)}
    </div>
    ${stopped && html`<div class="overlay"><div class="card pad stack">
      <h2>lyrembed has stopped</h2>
      <p class="muted">You can close this tab. Start lyrembed again to continue.</p>
    </div></div>`}
  `;
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

  return html`<section class="card pad stack" style=${{ maxWidth: '900px' }}>
    <div class="spread">
      <h1>Open a music folder</h1>
      ${onCancel && html`<button class="btn" onClick=${onCancel}>Cancel</button>`}
    </div>
    ${error && html`<p class="note note-warn" style=${{ whiteSpace: 'pre-line' }}>${error}</p>`}
    <form class="row-wrap" onSubmit=${(e) => { e.preventDefault(); load(path); }}>
      <label class="sr" for="folder-path">Folder</label>
      <input id="folder-path" type="text" class="mono" value=${path} onInput=${(e) => setPath(e.target.value)}
        style=${{ flex: '1 1 320px', minWidth: 0 }} />
      <button class="btn" type="submit">Show</button>
      <button class="btn btn-primary" type="button" disabled=${busy} onClick=${() => open(path)}>Open this folder</button>
    </form>
    ${listing && html`<div class="stack-tight">
      <div class="row-wrap">
        <button class="btn btn-small" disabled=${!listing.parent} onClick=${() => load(listing.parent)}><${Icon} name="up" /> Up</button>
        <button class="btn btn-small" onClick=${() => load(listing.home)}>Home</button>
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
  </section>`;
}

// --- track list -------------------------------------------------------------------
function LibraryView({ library, onOpen, preview, onPlay }) {
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
      <div class="row-wrap" style=${{ gap: '18px', alignItems: 'flex-end' }}>
        ${library.cover && html`<${Cover} index=${0} id=${library.cover} size=${128} />`}
        <div class="stack-tight">
          <span class="label">Folder</span>
          <h1>${library.name}</h1>
          <span class="muted">${tracks.length} tracks</span>
        </div>
      </div>
      <button class="btn btn-primary" disabled=${!first} onClick=${() => onOpen(first.index)}>
        ${first ? 'Start with the first track without lyrics' : 'Every track has lyrics'}
      </button>
    </div>
    <div class="row-wrap">
      <span class="badge b-sync">${count('sync')} synced</span>
      <span class="badge b-plain">${count('plain')} plain</span>
      <span class="badge b-none">${count('none')} without lyrics</span>
      ${count('incomplete') > 0 && html`<span class="badge b-incomplete">${count('incomplete')} with incomplete tags</span>`}
      ${count('unreadable') > 0 && html`<span class="badge b-unreadable">${count('unreadable')} unreadable</span>`}
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
                  <button class="btn btn-small btn-round" onClick=${(e) => { e.stopPropagation(); onPlay(t.index); }}
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
                <td style=${{ textAlign: 'right' }}>
                  <button class="btn btn-small" onClick=${(e) => { e.stopPropagation(); onOpen(t.index); }}>Open</button>
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
        ? html`<span class="small" style=${{ color: 'var(--plain-fg)' }}>${preview.error}</span>`
        : html`<span class="muted small">${track.artist}</span>`}
    </div>
    ${!preview.error && html`
      <span class="mono small muted">${fmtClock(preview.pos)} / ${fmtClock(preview.duration)}</span>
      <label class="sr" for="preview-seek">Position</label>
      <input id="preview-seek" type="range" min="0" max=${preview.duration || 0} step="0.5" value=${preview.pos}
        onInput=${(e) => { if (audio.current) audio.current.currentTime = Number(e.target.value); }}
        style=${{ flex: '0 1 260px', minWidth: '100px' }} />`}
    <button class="btn btn-small" onClick=${onClose} aria-label="Close the preview">×</button>
  </div>`;
}

// --- one track ------------------------------------------------------------------------
function TrackView({ library, index, notify, onOpen, onHome, onUpdated, onNext, onSave, onEdit, preview, onPlay }) {
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
            <button class="btn btn-primary" onClick=${() => onEdit(track.lyrics, 'Embedded')}>Check and edit</button>
            <button class="btn" disabled=${searching} onClick=${() => setSearching(true)}>Search for other versions</button>
            <button class="btn btn-danger" onClick=${remove}>Delete lyrics…</button>
          </div>
        </div>`}
        ${!track.error && (!track.lyrics || searching) && html`<${SearchPanel} key=${'s' + index} track=${track}
          onSave=${saveHere} onEdit=${onEdit} />`}
      `}
    </section>
  </div>`;
}

function SearchPanel({ track, onSave, onEdit }) {
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
          <span class="mono muted" style=${{ width: '20px', paddingTop: '1px' }}>${c.number}</span>
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
            <button class="btn" type="submit" disabled=${busy}>Search</button>
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
        <div class="row-wrap">
          <button class="btn btn-primary" onClick=${() => onSave(chosen.text, `Saved lyrics from ${source(chosen)}.`, true)}>Save and go to the next track</button>
          <button class="btn" onClick=${() => onSave(chosen.text, `Saved lyrics from ${source(chosen)}.`, false)}>Save</button>
          <button class="btn" onClick=${() => onEdit(chosen.text, chosen.providerName, all, chosen.number)}>Check and edit</button>
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

  return html`<div class="stack" style=${{ maxWidth: '980px' }}>
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
        ${action === 'fetch' && dryRun ? 'Start the dry run' : 'Start'}</button>
    </div>
  </div>`;
}

function JobView({ job, onOpen, onDone }) {
  const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  const t = job.tally;
  return html`<div class="stack" style=${{ maxWidth: '980px' }}>
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
              ${(row.warnings || []).map((w) => html`<br /><span class="small" style=${{ color: 'var(--plain-fg)' }}>${w}</span>`)}
            </span>
          </div>`;
        })}
      </div>
      ${!job.running && html`<div class="bar" style=${{ borderTop: '1px solid var(--border)', paddingTop: '14px' }}>
        <span class="grow">${job.dryRun ? 'Would save' : 'Done'}: <b>${t.done}</b> · Skipped: <b>${t.skipped}</b> · Failed: <b>${t.failed}</b>
          ${job.backupRoot && html`<br /><span class="muted small">The previous lyrics were backed up to <span class="mono">${job.backupRoot}</span></span>`}</span>
        <button class="btn" onClick=${onDone}>New run</button>
      </div>`}
      ${job.running && html`<div><button class="btn btn-danger" disabled=${job.stopping} onClick=${() => api('/jobs/current/stop', {})}>Stop</button></div>`}
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

function SettingsView({ notify }) {
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
      ? html`<input type="checkbox" id=${id(s)} checked=${current} onChange=${(e) => set(s, e.target.checked)} />`
      : s.choices
        ? html`<select id=${id(s)} value=${String(current)} onChange=${(e) => set(s, s.kind === 'int' ? Number(e.target.value) : e.target.value)}>
            ${s.choices.map((c) => html`<option value=${String(c)}>${String(c)}</option>`)}</select>`
        : s.kind === 'list'
          ? html`<textarea id=${id(s)} class="mono" rows=${Math.max(2, String(current).split('\n').length)} value=${current}
              onInput=${(e) => set(s, e.target.value)}></textarea>`
          : html`<input type="text" id=${id(s)} class=${s.section === 'cleaning' ? 'mono' : ''} value=${current}
              inputmode=${s.kind === 'int' || s.kind === 'float' ? 'decimal' : null} onInput=${(e) => set(s, e.target.value)} />`;
    return html`<div class=${'setting' + (s.kind === 'bool' ? ' setting-bool' : '')} key=${id(s)}>
      ${s.kind === 'bool' && control}
      <div class="stack-tight grow">
        <label for=${id(s)}><b>${humanize(s.key)}</b> <span class="mono muted small">${id(s)}</span>
          ${changed && html` <span class="badge b-script">changed</span>`}</label>
        ${s.help && html`<span class="muted small">${s.help}</span>`}
        ${s.kind !== 'bool' && control}
        ${s.kind === 'list' && html`<span class="muted small">One per line.</span>`}
        ${problems[id(s)] && html`<span class="small" style=${{ color: 'var(--none-fg)' }}>${humanize(s.key)} ${problems[id(s)]}.</span>`}
        ${notDefault && html`<span><button class="btn-link" onClick=${() => set(s, fieldText(s, s.default))}>Back to the default (${s.kind === 'list' ? s.default.join(', ') || 'empty' : String(s.default) || 'empty'})</button></span>`}
      </div>
    </div>`;
  };

  return html`<div class="stack" style=${{ maxWidth: '980px' }}>
    <div class="stack-tight">
      <h1>Settings</h1>
      <span class="muted">Saved to <span class="mono">${data.path}</span>. Options given on the command line (such as --sidecar) still apply to this run.</span>
    </div>
    ${data.groups.map((g, i) => (i < 2
      ? html`<section class="card pad stack" key=${g.name}><h2>${g.name}</h2>${g.settings.map(field)}</section>`
      : html`<details class="card pad" key=${g.name}><summary><h2 style=${{ display: 'inline' }}>${g.name}</h2></summary>
          <div class="stack" style=${{ marginTop: '14px' }}>${g.settings.map(field)}</div></details>`))}
    <${AliasEditor} notify=${notify} />
    <div class="card pad bar save-bar">
      <span class="grow">${changes.length ? `${changes.length} unsaved change${changes.length === 1 ? '' : 's'}` : 'No unsaved changes'}
        ${Object.keys(problems).length > 0 && html`<span style=${{ color: 'var(--none-fg)' }}> · fix the marked fields first</span>`}</span>
      ${error && html`<span class="note note-error small">${error}</span>`}
      <button class="btn" disabled=${!Object.keys(edits).length} onClick=${() => setEdits({})}>Undo changes</button>
      <button class="btn btn-primary" disabled=${!changes.length || Object.keys(problems).length > 0 || saving} onClick=${save}>Save settings</button>
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

  return html`<section class="card pad stack">
    <h2>Artist aliases</h2>
    <span class="muted small">Names that mean the same artist, so a search for one finds the others, for example
      “Hoshino Gen = 星野源”. Your groups are added to the built-in ones; a group sharing a name with one is merged into it.</span>
    <label for="aliases" class="small"><b>Your groups</b>, one per line, names separated by “=”</label>
    <textarea id="aliases" class="mono" rows=${Math.max(3, groups.length + 1)} value=${draft} onInput=${(e) => setDraft(e.target.value)}></textarea>
    ${lonely && html`<span class="small" style=${{ color: 'var(--none-fg)' }}>“${lonely[0]}” needs at least one other name.</span>`}
    ${error && html`<p class="note note-error small">${error}</p>`}
    <div><button class="btn" disabled=${!changed || !!lonely} onClick=${save}>Save aliases</button></div>
    <details>
      <summary class="small">${data.builtin.length} built-in groups</summary>
      <div class="stack-tight" style=${{ marginTop: '10px' }}>
        <label class="sr" for="alias-filter">Filter</label>
        <input id="alias-filter" type="text" placeholder="Filter by name" value=${filter} onInput=${(e) => setFilter(e.target.value)} />
        <div class="alias-list small">${shown.map((g) => html`<div>${g.join(' = ')}</div>`)}</div>
      </div>
    </details>
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
      const nearest = line.times.reduce((a, b) => (Math.abs(b - pos) < Math.abs(a - pos) ? b : a));
      seek(playTime(nearest, parsed, shift));
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
          <div><button class="btn btn-small" disabled=${!shift} onClick=${() => setShift(0)}>Reset timing</button></div>
        </div>
        <div class="card pad stack-tight">
          <span class="label">Text</span>
          <div class="row-wrap">
            <button class="btn btn-small" disabled=${!text.trim()} onClick=${() => setRomanizing(!romanizing)}>Romanize…</button>
            ${mode === 'lines'
              ? html`<button class="btn btn-small" onClick=${toText}>Edit as text</button>`
              : html`<button class="btn btn-small" onClick=${() => setMode('lines')}>Back to the lines</button>`}
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
      <button class="btn" disabled=${(text === origin.text && !shift) || saving} onClick=${() => { setText(origin.text); setShift(0); }}>Undo changes</button>
      <button class="btn" disabled=${!final.trim() || !unsaved || saving} onClick=${() => save(false)}>Save</button>
      <button class="btn btn-primary" disabled=${!final.trim() || saving} onClick=${() => (unsaved ? save(true) : onNext())}>
        ${unsaved ? 'Save and go to the next track' : 'Next track'}</button>
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
