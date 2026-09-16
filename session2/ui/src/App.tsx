import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  FileAudio2,
  FileText,
  Film,
  FolderOpen,
  HardDrive,
  LoaderCircle,
  LockKeyhole,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { api, fileSize, isActive, time, upload } from './api';
import type { Health, Job } from './api';

const labels: Record<string, string> = {
  uploading: 'Uploading recording',
  decoding: 'Preparing audio',
  loading_model: 'Loading English model',
  transcribing: 'Transcribing audio',
  completed: 'Completed',
  failed: 'Needs attention',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
  processing: 'Processing',
};
const date = (value: string) =>
  new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Job | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [actionPending, setActionPending] = useState(false);
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const xhr = useRef<XMLHttpRequest | null>(null);
  const selectedRef = useRef(selected);
  const detailRef = useRef(detail);
  const refreshLock = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const restoreSelection = useRef(true);
  const savedSelection = useRef(localStorage.getItem('transcript-selection'));
  selectedRef.current = selected;
  detailRef.current = detail;

  const refresh = useCallback(async () => {
    if (refreshLock.current) return;
    refreshLock.current = true;
    try {
      const [list, readiness] = await Promise.all([api<Job[]>('/jobs'), api<Health>('/health')]);
      setJobs(list);
      setHealth(readiness);
      setOffline(false);
      let id = selectedRef.current;
      if (restoreSelection.current) {
        restoreSelection.current = false;
        const saved = savedSelection.current;
        id = list.find(isActive)?.id || (list.some((job) => job.id === saved) ? saved : null);
        setSelected(id);
        selectedRef.current = id;
      }
      if (id) {
        if (!list.some((job) => job.id === id)) {
          setSelected(null);
          setDetail(null);
        } else if (
          !detailRef.current ||
          detailRef.current.id !== id ||
          isActive(detailRef.current) ||
          isActive(list.find((job) => job.id === id))
        ) {
          const result = await api<Job>(`/jobs/${id}`);
          if (selectedRef.current === id) setDetail(result);
        }
      }
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
      refreshLock.current = false;
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => void refresh(), 1000);
    return () => clearInterval(interval);
  }, [refresh]);
  useEffect(() => {
    if (selected) localStorage.setItem('transcript-selection', selected);
    else localStorage.removeItem('transcript-selection');
    setCopied(false);
    setDeleting(false);
    if (!selected) {
      setDetail(null);
      return;
    }
    let live = true;
    void api<Job>(`/jobs/${selected}`)
      .then((result) => {
        if (live) setDetail(result);
      })
      .catch((err) => {
        if (live) setError(err.message);
      });
    return () => {
      live = false;
    };
  }, [selected]);
  useEffect(() => {
    if (deleting) dialog.current?.showModal();
    else dialog.current?.close();
  }, [deleting]);

  const active = jobs.find(isActive);
  const busy = !!active || uploading;
  const current = detail?.id === selected ? detail : null;
  const filtered = jobs.filter((job) => job.name.toLowerCase().includes(query.toLowerCase()));
  const completed = jobs.filter((job) => job.status === 'completed');
  const totalMinutes = Math.round(
    completed.reduce((sum, job) => sum + (job.duration || 0), 0) / 60,
  );

  function chooseFile(candidate?: File) {
    setError('');
    if (!candidate) return;
    if (!/\.(mp3|mp4)$/i.test(candidate.name)) {
      setError('Choose an MP3 audio file or an MP4 video file.');
      return;
    }
    if (candidate.size === 0) {
      setError('This file is empty. Choose a recording containing audio.');
      return;
    }
    if (candidate.size > (health?.max_bytes || 4 * 1024 ** 3)) {
      setError('This recording exceeds the 4 GiB limit.');
      return;
    }
    setFile(candidate);
  }

  async function startUpload() {
    if (!file || busy) return;
    setError('');
    setUploading(true);
    setUploadProgress(0);
    try {
      const job = await upload(file, setUploadProgress, (value) => {
        xhr.current = value;
      });
      setJobs((previous) => [job, ...previous.filter((item) => item.id !== job.id)]);
      setDetail(job);
      setSelected(job.id);
      setFile(null);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError'))
        setError((err as Error).message);
    } finally {
      setUploading(false);
      xhr.current = null;
      void refresh();
    }
  }

  async function action(kind: 'cancel' | 'retry' | 'delete') {
    if (!current) return;
    setActionPending(true);
    setError('');
    try {
      if (kind === 'delete') {
        await api(`/jobs/${current.id}`, 'DELETE');
        setSelected(null);
        setDetail(null);
        setDeleting(false);
      } else {
        const result = await api<Job>(`/jobs/${current.id}/${kind}`, 'POST');
        setDetail(result);
      }
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActionPending(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(
        current?.segments?.map((segment) => segment.text).join('\n\n') || '',
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Clipboard access is unavailable. Download the TXT file instead.');
    }
  }

  function newTranscript() {
    setSelected(null);
    setDetail(null);
    setError('');
    setFile(null);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to workspace
      </a>
      <aside className="sidebar" aria-label="Recording library">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            newTranscript();
          }}
        >
          <span className="brand-icon">
            <AudioLines size={23} />
          </span>
          <span>
            Local Transcript<span className="brand-subtitle">YOUR WORDS, IN WRITING</span>
          </span>
        </a>
        <button className="button primary new-button" onClick={newTranscript}>
          <Plus size={17} />
          New transcript
        </button>
        <div className="library-heading">
          <span>YOUR LIBRARY</span>
          <span className="count">{jobs.length}</span>
        </div>
        <label className="search">
          <Search size={16} />
          <input
            aria-label="Search recordings"
            placeholder="Search recordings…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="history" aria-busy={loading}>
          {loading ? (
            <div className="library-empty">
              <LoaderCircle className="spin" size={20} />
              <p>Loading your library…</p>
            </div>
          ) : filtered.length ? (
            filtered.map((job) => (
              <button
                key={job.id}
                className={`history-item ${selected === job.id ? 'selected' : ''}`}
                onClick={() => {
                  setSelected(job.id);
                  setError('');
                }}
              >
                <span className={`file-icon ${job.extension === '.mp4' ? 'video' : ''}`}>
                  {job.extension === '.mp4' ? <Film size={18} /> : <FileAudio2 size={18} />}
                </span>
                <span className="history-info">
                  <span className="history-name" title={job.name}>
                    {job.name}
                  </span>
                  <span className="history-meta">
                    {date(job.created_at)}
                    <span>·</span>
                    {job.duration ? time(job.duration) : job.extension.slice(1).toUpperCase()}
                  </span>
                </span>
                {isActive(job) ? (
                  <LoaderCircle
                    size={14}
                    className="spin status-processing"
                    aria-label="Processing"
                  />
                ) : (
                  <span className={`status-dot ${job.status}`} aria-label={labels[job.status]} />
                )}
              </button>
            ))
          ) : (
            <div className="library-empty">
              <FolderOpen size={27} strokeWidth={1.3} />
              <p>{query ? 'No matching recordings' : 'A fresh start'}</p>
              <span>
                {query ? 'Try a different file name.' : 'Your recordings will appear here.'}
              </span>
            </div>
          )}
        </div>
        <div className="storage-note">
          <span className="storage-icon">
            <HardDrive size={17} />
          </span>
          <div>
            <strong>Stored on this computer</strong>
            <span>
              {jobs.length
                ? `${fileSize(jobs.reduce((sum, job) => sum + job.size, 0))} of recordings`
                : 'Your recordings stay with you'}
            </span>
          </div>
        </div>
        <div className="sidebar-footer">
          <span className={`connection-dot ${offline ? 'disconnected' : ''}`} />
          {offline ? 'Server disconnected' : 'Local workspace'}
          <span>v1.0</span>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <FolderOpen size={16} />
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{selected ? 'Transcript' : 'New transcript'}</strong>
          </div>
          <span className="privacy-badge">
            <ShieldCheck size={14} />
            Private by design
          </span>
        </header>
        <main id="main" className="main" tabIndex={-1}>
          {offline && (
            <div className="notice warning" role="status">
              <CircleAlert size={18} />
              <span>
                Connection lost. Keep the local server running; this page will reconnect
                automatically.
              </span>
            </div>
          )}
          {error && (
            <div className="notice error" role="alert">
              <CircleAlert size={18} />
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError('')}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {health && !health.model_ready && (
            <div className="notice warning" role="status">
              <CircleAlert size={18} />
              <span>
                The English model is missing. Run <code>setup.ps1</code> to download it, then retry
                your recording.
              </span>
            </div>
          )}
          {!selected ? (
            <>
              <div className="page-heading">
                <div>
                  <span className="eyebrow">FROM RECORDING TO READABLE</span>
                  <h1>Give your words a place.</h1>
                  <p>Turn audio and video into a transcript you can keep.</p>
                </div>
                <span className="heading-icon">
                  <FileText size={27} strokeWidth={1.4} />
                </span>
              </div>
              {active && !uploading && (
                <button className="active-notice" onClick={() => setSelected(active.id)}>
                  <LoaderCircle size={17} className="spin" />
                  <span>
                    A recording is in progress: <strong>{active.name}</strong>
                  </span>
                  <ArrowRight size={17} />
                </button>
              )}
              <section className="upload-card" aria-labelledby="upload-title">
                <div className="card-heading">
                  <div>
                    <h2 id="upload-title">Start with a recording</h2>
                    <p>A lecture, an interview, a thought worth keeping.</p>
                  </div>
                  <span className="step-label">STEP 01 / 02</span>
                </div>
                <div
                  className={`drop-zone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
                  onDragOver={(event) => {
                    event.preventDefault();
                    if (!busy) setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    if (!busy) {
                      if (event.dataTransfer.files.length > 1)
                        setError('Choose one recording at a time.');
                      else chooseFile(event.dataTransfer.files[0]);
                    }
                  }}
                >
                  <div className="upload-symbol">
                    {file ? (
                      file.name.toLowerCase().endsWith('.mp4') ? (
                        <Film size={29} />
                      ) : (
                        <FileAudio2 size={29} />
                      )
                    ) : (
                      <Upload size={27} strokeWidth={1.6} />
                    )}
                  </div>
                  <h3>{file ? file.name : 'Drop your recording here'}</h3>
                  <p>
                    {file
                      ? `${fileSize(file.size)} · Ready to transcribe`
                      : 'or choose a file from your computer'}
                  </p>
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".mp3,.mp4,audio/mpeg,video/mp4"
                    aria-label="Choose recording"
                    className="visually-hidden"
                    tabIndex={-1}
                    onChange={(event) => {
                      chooseFile(event.target.files?.[0]);
                      event.target.value = '';
                    }}
                    disabled={busy}
                  />
                  <button
                    className="button secondary choose-button"
                    disabled={busy}
                    onClick={() => fileInput.current?.click()}
                  >
                    {file ? <RotateCcw size={15} /> : <Plus size={16} />}
                    {file ? 'Change file' : 'Choose a file'}
                  </button>
                  <div className="file-requirements">
                    <span>MP3</span>
                    <span>MP4</span>
                    <i />
                    Up to 4 GiB<span className="requirement-divider">·</span>2 hours max
                  </div>
                </div>
                <div className="upload-options">
                  <span>
                    <span className="language-icon">En</span>
                    <span>
                      <strong>English</strong>
                      <small>Transcription language</small>
                    </span>
                  </span>
                  <span className="processing-note">
                    <span className="tiny-dot" />
                    Processed on your computer
                  </span>
                </div>
                <div className="card-action">
                  <span>
                    <LockKeyhole size={14} />
                    Your files stay local.
                  </span>
                  <button
                    className="button primary"
                    onClick={() => void startUpload()}
                    disabled={!file || busy || offline || !health?.model_ready}
                  >
                    {uploading ? (
                      <>
                        <LoaderCircle size={16} className="spin" />
                        Uploading {uploadProgress}%
                      </>
                    ) : (
                      <>
                        Create transcript
                        <ArrowRight size={16} />
                      </>
                    )}
                  </button>
                </div>
                {uploading && (
                  <div className="upload-progress">
                    <div
                      className="progress-track"
                      role="progressbar"
                      aria-label="Upload progress"
                      aria-valuenow={uploadProgress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <span style={{ width: `${uploadProgress}%` }} />
                    </div>
                    <button className="text-button" onClick={() => xhr.current?.abort()}>
                      Cancel upload
                    </button>
                  </div>
                )}
              </section>
              <div className="benefits">
                <div>
                  <span className="benefit-icon">
                    <LockKeyhole size={18} />
                  </span>
                  <h3>Only on your computer</h3>
                  <p>
                    Local processing. Your recordings
                    <br className="desktop-break" /> never leave your device.
                  </p>
                </div>
                <div>
                  <span className="benefit-icon">
                    <Clock3 size={18} />
                  </span>
                  <h3>Find the moment</h3>
                  <p>
                    Timestamps make it easy to locate
                    <br className="desktop-break" /> each part of your recording.
                  </p>
                </div>
                <div>
                  <span className="benefit-icon">
                    <ArrowDownToLine size={18} />
                  </span>
                  <h3>Ready to take with you</h3>
                  <p>
                    Copy your words, download text,
                    <br className="desktop-break" /> or export SRT subtitles.
                  </p>
                </div>
              </div>
              <section className="workspace-summary">
                <div>
                  <span className="summary-icon">
                    <AudioLines size={18} />
                  </span>
                  <span>
                    <strong>Your words add up.</strong>
                    <small>
                      {completed.length
                        ? `${completed.length} transcript${completed.length === 1 ? '' : 's'} saved in your library`
                        : 'Your first transcript is one recording away.'}
                    </small>
                  </span>
                </div>
                <span className="minutes">
                  <strong>{totalMinutes}</strong> minutes transcribed
                </span>
              </section>
            </>
          ) : !current ? (
            <div className="detail-loading" role="status">
              <LoaderCircle className="spin" />
              Loading transcript…
            </div>
          ) : (
            <>
              <div className="page-heading detail-heading">
                <div>
                  <span className="eyebrow">YOUR RECORDING</span>
                  <h1 title={current.name}>{current.name}</h1>
                  <p>
                    {date(current.created_at)}
                    <span className="meta-separator">/</span>
                    {fileSize(current.size)}
                    <span className="meta-separator">/</span>English
                    {current.duration ? (
                      <>
                        <span className="meta-separator">/</span>
                        {time(current.duration)}
                      </>
                    ) : null}
                  </p>
                </div>
                <span className={`result-status ${current.status}`}>
                  {current.status === 'completed' ? (
                    <CheckCircle2 size={15} />
                  ) : isActive(current) ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <CircleAlert size={15} />
                  )}
                  {labels[current.status]}
                </span>
              </div>
              {isActive(current) ? (
                <section className="processing-card" aria-live="polite">
                  <div className="processing-orbit">
                    <AudioLines size={35} />
                  </div>
                  <span className="eyebrow">A LITTLE SPACE FOR YOUR WORDS</span>
                  <h2>{labels[current.stage] || 'Processing recording'}</h2>
                  <p>You can leave this page. Keep the local server running.</p>
                  <div className="processing-progress">
                    <div
                      className={`progress-track ${current.progress === null ? 'indeterminate' : ''}`}
                      role="progressbar"
                      aria-label={labels[current.stage]}
                      aria-valuenow={current.progress ?? undefined}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <span
                        style={
                          current.progress === null ? undefined : { width: `${current.progress}%` }
                        }
                      />
                    </div>
                    <span>
                      {current.progress === null
                        ? 'Preparing…'
                        : `About ${Math.round(current.progress)}% of this stage`}
                    </span>
                  </div>
                  <button
                    className="button secondary"
                    disabled={actionPending || offline}
                    onClick={() => void action('cancel')}
                  >
                    <X size={15} />
                    Cancel transcription
                  </button>
                  <small>Processing time depends on your recording and computer.</small>
                </section>
              ) : current.status !== 'completed' ? (
                <section className="processing-card failed-card">
                  <span className="failure-icon">
                    <CircleAlert size={29} />
                  </span>
                  <h2>
                    {current.status === 'cancelled'
                      ? 'Transcription cancelled'
                      : current.status === 'interrupted'
                        ? 'Let’s pick this up again.'
                        : 'This recording needs attention.'}
                  </h2>
                  <p>
                    {current.error ||
                      'Your recording is saved. You can start again whenever you’re ready.'}
                  </p>
                  <button
                    className="button primary"
                    onClick={() => void action('retry')}
                    disabled={busy || actionPending || offline || !health?.model_ready}
                  >
                    <RotateCcw size={16} />
                    Retry transcription
                  </button>
                </section>
              ) : (
                <section className="transcript-card">
                  <div className="transcript-toolbar">
                    <div>
                      <h2>Transcript</h2>
                      <span>
                        {current.segments?.length || 0} segments<span>·</span>
                        {current.segments?.reduce(
                          (count, segment) =>
                            count + segment.text.split(/\s+/).filter(Boolean).length,
                          0,
                        ) || 0}{' '}
                        words
                      </span>
                    </div>
                    <div className="export-actions">
                      <button
                        className="button secondary compact"
                        disabled={!current.segments?.length}
                        onClick={() => void copy()}
                      >
                        {copied ? <Check size={15} /> : <Copy size={15} />}
                        {copied ? 'Copied' : 'Copy text'}
                      </button>
                      <a
                        className="button secondary compact"
                        href={`/api/jobs/${current.id}/download/txt`}
                        download
                      >
                        <ArrowDownToLine size={15} />
                        TXT
                      </a>
                      <a
                        className="button secondary compact"
                        href={`/api/jobs/${current.id}/download/srt`}
                        download
                      >
                        <ArrowDownToLine size={15} />
                        SRT
                      </a>
                    </div>
                  </div>
                  <div className="transcript-content">
                    {current.segments?.length ? (
                      current.segments.map((segment, index) => (
                        <div className="segment" key={index}>
                          <span className="timestamp">{time(segment.start)}</span>
                          <p>{segment.text}</p>
                        </div>
                      ))
                    ) : (
                      <div className="no-speech">
                        <AudioLines size={28} />
                        <h3>No speech detected</h3>
                        <p>The recording was processed, but no spoken words were found.</p>
                      </div>
                    )}
                  </div>
                  <div className="transcript-bottom">
                    <CheckCircle2 size={14} />
                    Saved on this computer<span>English · Timestamped transcript</span>
                  </div>
                </section>
              )}
              {!isActive(current) && (
                <div className="recording-actions">
                  <a
                    className="text-button"
                    href={`/api/jobs/${current.id}/download/original`}
                    download
                  >
                    <ArrowDownToLine size={15} />
                    Download original recording
                  </a>
                  <button
                    className="text-button danger"
                    disabled={actionPending || offline}
                    onClick={() => setDeleting(true)}
                  >
                    <Trash2 size={15} />
                    Delete recording
                  </button>
                </div>
              )}
            </>
          )}
          <footer className="main-footer">
            <span>
              <LockKeyhole size={12} />A quieter place for your recordings.
            </span>
            <span>Local processing. Lasting words.</span>
          </footer>
        </main>
      </div>
      <dialog
        ref={dialog}
        className="delete-dialog"
        aria-labelledby="delete-title"
        onCancel={() => setDeleting(false)}
      >
        <span className="failure-icon">
          <Trash2 size={24} />
        </span>
        <h2 id="delete-title">Delete this recording?</h2>
        <p>
          This removes <strong>{current?.name}</strong> and its transcript from this app. Your
          original file outside the app stays where it is.
        </p>
        {error && <p role="alert">{error}</p>}
        <div>
          <button className="button secondary" autoFocus onClick={() => setDeleting(false)}>
            Keep recording
          </button>
          <button
            className="button destructive"
            disabled={actionPending}
            onClick={() => void action('delete')}
          >
            Delete recording
          </button>
        </div>
      </dialog>
    </div>
  );
}
