import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  nexusApi,
  type MergeAction,
  type MergeCandidate,
  type ModHit,
  type SearchMode,
  type TransferPlan,
  type TranslationHit,
  type AssetKind,
} from '@/api/nexus'
import { modsApi } from '@/api/mods'
import { Button } from '@/components/shared/Button'
import { QK } from '@/lib/queryKeys'
import { cn, humanSize } from '@/lib/utils'
import { useSSE } from '@/hooks/useSSE'
import type { ModInfo } from '@/types'
import {
  AlertCircle,
  CheckCircle2,
  Download,
  Dices,
  FolderOpen,
  Languages,
  Loader2,
  LogIn,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  ShieldAlert,
  X,
} from 'lucide-react'

// ── account / session ─────────────────────────────────────────────────────────

function AccountPanel() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: QK.nexusAccount(),
    queryFn: nexusApi.account,
    refetchInterval: 60_000,
  })

  const verifyMut = useMutation({
    mutationFn: () => nexusApi.browser(true),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: QK.nexusAccount() })
      toast[s.logged_in ? 'success' : 'warning'](
        s.logged_in ? `Signed in as ${s.last_session?.name ?? 'your account'}` : 'No Nexus session in the browser profile',
      )
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // The server blocks on this until somebody signs in, so it is a long request on
  // purpose — the Chrome window is already open and waiting while it runs.
  const loginMut = useMutation({
    mutationFn: () => nexusApi.browserLogin(600),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.nexusAccount() })
      toast.success('Nexus session saved — downloads run unattended from now on')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const { data: settings } = useQuery({
    queryKey: QK.nexusSettings(),
    queryFn: nexusApi.settings,
  })
  const [dir, setDir] = useState<string | null>(null)
  const shownDir = dir ?? settings?.download_dir ?? ''

  // Persisted into config.yaml rather than held in the page: a download folder that
  // forgets itself on refresh is a preference, not a setting.
  const saveMut = useMutation({
    mutationFn: () => nexusApi.saveSettings({ download_dir: shownDir }),
    onSuccess: (r) => {
      setDir(null)
      qc.invalidateQueries({ queryKey: QK.nexusSettings() })
      qc.invalidateQueries({ queryKey: QK.nexusAccount() })
      toast.success(`Downloads now land in ${r.download_dir}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const unattended = data?.unattended ?? false
  const rl = data?.rate_limit

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        {isLoading ? (
          <Loader2 className="w-4 h-4 animate-spin text-text-muted" />
        ) : unattended ? (
          <span className="inline-flex items-center gap-1.5 text-success text-sm font-medium">
            <ShieldCheck className="w-4 h-4" /> Authorized — downloads need nobody
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-danger text-sm font-medium">
            <ShieldAlert className="w-4 h-4" /> Not authorized — one sign-in needed
          </span>
        )}

        {data?.name && (
          <span className="text-xs text-text-muted">
            {data.name}
            {data.is_premium ? ' · Premium' : data.is_supporter ? ' · Supporter' : ' · Free'}
            {data.link_mode ? ` · link_mode: ${data.link_mode}` : ''}
          </span>
        )}

        {rl && (
          <span className="text-xs text-text-muted">
            quota {rl.hourly_remaining}/{rl.hourly_limit} h · {rl.daily_remaining}/{rl.daily_limit} d
          </span>
        )}

        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />}
                  loading={verifyMut.isPending} onClick={() => verifyMut.mutate()}>
            Verify
          </Button>
          {!unattended && (
            <Button size="sm" variant="primary" icon={<LogIn className="w-3.5 h-3.5" />}
                    loading={loginMut.isPending} onClick={() => loginMut.mutate()}>
              {loginMut.isPending ? 'Waiting for sign-in…' : 'Sign in to Nexus'}
            </Button>
          )}
        </div>
      </div>

      {loginMut.isPending && (
        <p className="mt-2 text-xs text-text-muted">
          A Chrome window is open on nexusmods.com. Sign in there — it is the only manual
          step, and the session lives in the profile from then on.
        </p>
      )}

      {data?.browser?.chrome_error && (
        <p className="mt-2 text-xs text-danger">{data.browser.chrome_error}</p>
      )}

      <form
        className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3"
        onSubmit={(e) => { e.preventDefault(); saveMut.mutate() }}
      >
        <FolderOpen className="w-3.5 h-3.5 text-text-muted" />
        <label className="text-xs text-text-muted">Download folder</label>
        <input
          value={shownDir}
          onChange={(e) => setDir(e.target.value)}
          placeholder="cache/downloads"
          className="flex-1 min-w-[18rem] px-2 py-1 rounded border border-border-subtle bg-bg-card2 text-xs text-text-main"
        />
        <Button type="submit" size="sm" variant={dir === null ? 'ghost' : 'primary'}
                icon={<Save className="w-3.5 h-3.5" />}
                loading={saveMut.isPending} disabled={dir === null || !shownDir}>
          Save
        </Button>
        <span className="text-[11px] text-text-muted w-full">
          Saved to config.yaml and used from the next download on. Donor archives go to{' '}
          {settings?.donor_dir || 'cache/donors'} and are deleted once their strings are out.
        </span>
      </form>
    </div>
  )
}

// ── downloads ─────────────────────────────────────────────────────────────────

const STATE_COLOR: Record<string, string> = {
  done: 'text-success',
  skipped: 'text-text-muted',
  failed: 'text-danger',
  cancelled: 'text-text-muted',
  downloading: 'text-accent',
}

function DownloadPanel({ batchId, onClose }: { batchId: string; onClose: () => void }) {
  const [snap, setSnap] = useState<null | Awaited<ReturnType<typeof nexusApi.batch>>>(null)

  useQuery({
    queryKey: QK.nexusBatch(batchId),
    queryFn: () => nexusApi.batch(batchId).then((s) => { setSnap(s); return s }),
    refetchInterval: false,
  })

  // One event per item state change and per progress tick — the same stream the job
  // pages use, so a transfer here behaves like everything else in the app.
  useSSE(`/api/nexus/downloads/${batchId}/stream`, (raw) => {
    let d: unknown
    try { d = JSON.parse(raw) } catch { return }
    const asSnap = d as Awaited<ReturnType<typeof nexusApi.batch>>
    if (Array.isArray(asSnap?.items)) { setSnap(asSnap); return }
    const item = d as MergeCandidate & { id?: string }
    setSnap((prev) => {
      if (!prev) return prev
      const items = prev.items.map((x) => (x.id === (item as { id?: string }).id ? { ...x, ...(item as object) } : x))
      return { ...prev, items }
    })
  })

  if (!snap) return null

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Download className="w-4 h-4 text-accent" />
        <h3 className="text-sm font-medium text-text-main">
          Downloading — {snap.finished}/{snap.total} · {humanSize(snap.bytes_done)} of {humanSize(snap.bytes_total)}
        </h3>
        <span className="text-xs text-text-muted">via {snap.provider}</span>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => nexusApi.cancelBatch(batchId)}>Cancel</Button>
          <Button size="sm" variant="ghost" icon={<X className="w-3.5 h-3.5" />} onClick={onClose} />
        </div>
      </div>

      <div className="space-y-2">
        {snap.items.map((it) => (
          <div key={it.id} className="text-xs">
            <div className="flex items-center gap-2">
              <span className={cn('w-20 shrink-0 font-medium', STATE_COLOR[it.state] ?? 'text-text-muted')}>
                {it.state}
              </span>
              <span className="flex-1 truncate text-text-main">{it.file_name || it.name}</span>
              {it.progress && it.state === 'downloading' && (
                <span className="text-text-muted shrink-0">
                  {it.progress.pct.toFixed(0)}% · {humanSize(it.progress.speed_bps)}/s
                </span>
              )}
              {it.result && <span className="text-text-muted shrink-0">{humanSize(it.result.size_bytes)}</span>}
            </div>
            {it.progress && it.state === 'downloading' && (
              <div className="mt-1 h-1 rounded bg-bg-card2 overflow-hidden">
                <div className="h-full bg-accent transition-all" style={{ width: `${it.progress.pct}%` }} />
              </div>
            )}
            {it.error && <p className="mt-0.5 text-danger">{it.error}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── search ────────────────────────────────────────────────────────────────────

function SearchPanel({ onBatch }: { onBatch: (id: string) => void }) {
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<SearchMode>('stemmed')
  const [language, setLanguage] = useState('')
  const [submitted, setSubmitted] = useState('')

  const { data, isFetching } = useQuery({
    queryKey: QK.nexusSearch(submitted, mode, language),
    queryFn: () => nexusApi.search({ q: submitted, mode, language: language || undefined, count: 25 }),
    enabled: submitted.length > 0,
  })

  const randomMut = useMutation({
    mutationFn: () => nexusApi.random({ count: 10, min_downloads: 5000 }),
    onError: (e: Error) => toast.error(e.message),
  })

  const dlMut = useMutation({
    mutationFn: (m: ModHit) => nexusApi.download([{ mod_id: m.mod_id, label: m.name }]),
    onSuccess: (r) => { onBatch(r.batch_id); toast.success(`Queued — ${r.dest_dir}`) },
    onError: (e: Error) => toast.error(e.message),
  })

  const results = submitted ? data?.results : randomMut.data?.results

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-card p-4">
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => { e.preventDefault(); setSubmitted(q.trim()) }}
      >
        <div className="relative flex-1 min-w-[14rem]">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search Nexus — any words from the title"
            className="w-full pl-8 pr-2 py-1.5 rounded border border-border-subtle bg-bg-card2 text-sm text-text-main"
          />
        </div>
        <select value={mode} onChange={(e) => setMode(e.target.value as SearchMode)}
                className="px-2 py-1.5 rounded border border-border-subtle bg-bg-card2 text-sm text-text-main">
          <option value="stemmed">relevance</option>
          <option value="contains">substring</option>
          <option value="exact">exact title</option>
        </select>
        <input
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          placeholder="language"
          className="w-28 px-2 py-1.5 rounded border border-border-subtle bg-bg-card2 text-sm text-text-main"
        />
        <Button type="submit" variant="primary" loading={isFetching}>Search</Button>
        <Button type="button" variant="secondary" icon={<Dices className="w-4 h-4" />}
                loading={randomMut.isPending}
                onClick={() => { setSubmitted(''); randomMut.mutate() }}>
          Surprise me
        </Button>
      </form>

      {submitted && data && (
        <p className="mt-2 text-xs text-text-muted">
          {data.total.toLocaleString()} match{data.total === 1 ? '' : 'es'}, showing {data.count}
        </p>
      )}

      {results && results.length > 0 && (
        <div className="mt-3 divide-y divide-border-subtle">
          {results.map((m) => (
            <div key={m.mod_id} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <a href={`https://www.nexusmods.com/${m.game}/mods/${m.mod_id}`}
                     target="_blank" rel="noreferrer"
                     className="text-sm text-text-main hover:text-accent truncate">{m.name}</a>
                  {m.adult && <span className="text-[10px] px-1 rounded bg-danger/15 text-danger">adult</span>}
                </div>
                <p className="text-xs text-text-muted truncate">
                  {m.author} · {m.category} · v{m.version} · {m.downloads.toLocaleString()} downloads
                </p>
              </div>
              <Button size="sm" icon={<Download className="w-3.5 h-3.5" />}
                      loading={dlMut.isPending && dlMut.variables?.mod_id === m.mod_id}
                      onClick={() => dlMut.mutate(m)}>
                Download
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── translate from mod ────────────────────────────────────────────────────────

const ACTION_STYLE: Record<MergeAction, string> = {
  fill: 'text-success',
  conflict: 'text-accent',
  same: 'text-text-muted',
  rejected: 'text-danger',
  unmatched: 'text-text-muted',
}

const ACTION_HELP: Record<MergeAction, string> = {
  fill: 'we have nothing here — pure gain',
  conflict: 'we already have a different translation',
  same: 'identical to ours; nothing to do',
  rejected: 'the donor string is not actually translated',
  unmatched: 'no record of ours corresponds',
}

const KIND_LABEL: Record<string, string> = {
  esp: 'ESP', mcm: 'MCM', 'bsa-mcm': 'BSA', swf: 'SWF',
}

function CandidateRow({ c }: { c: MergeCandidate }) {
  return (
    <tr className="border-b border-border-subtle align-top">
      <td className={cn('py-1 pr-3 text-xs font-medium whitespace-nowrap', ACTION_STYLE[c.action])}>
        <span className="mr-1.5 text-[10px] px-1 rounded bg-bg-card2 text-text-muted">
          {KIND_LABEL[c.kind] ?? c.kind}
        </span>
        {c.action}
        {c.match === 'local_id' && <span className="ml-1 text-[10px] text-text-muted">(masked id)</span>}
      </td>
      <td className="py-1 pr-3 text-xs text-text-muted max-w-[22rem] truncate">{c.original}</td>
      <td className="py-1 pr-3 text-xs text-text-main max-w-[22rem] truncate">{c.donor_text}</td>
      <td className="py-1 text-xs text-text-muted max-w-[16rem] truncate">{c.current || c.reason}</td>
    </tr>
  )
}

function TransferPanel() {
  const qc = useQueryClient()
  const [mod, setMod] = useState('')
  const [language, setLanguage] = useState('Russian')
  const [plan, setPlan] = useState<TransferPlan | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [status, setStatus] = useState<'needs_review' | 'translated'>('needs_review')
  const [filter, setFilter] = useState<MergeAction | 'all'>('fill')

  // The transfer runs on the job queue: a donor can be hundreds of megabytes, and the
  // request that waits for one is a request that times out. Closing the page no longer
  // loses the run either — the job is in Jobs like everything else.
  const [job, setJob] = useState<{ id: string; planId: string } | null>(null)
  const [step, setStep] = useState('')

  const { data: mods } = useQuery({ queryKey: QK.mods(), queryFn: () => modsApi.list() })

  const donorsQuery = useQuery({
    queryKey: QK.nexusDonors(mod, language),
    queryFn: () => nexusApi.donors(mod, language, 8),
    enabled: mod.length > 0,
  })

  const startMut = useMutation({
    mutationFn: (opts: { donorId?: number; apply: boolean }) =>
      nexusApi.transferJob({
        mod, language, donor_mod_id: opts.donorId, apply: opts.apply,
        overwrite, status,
      }),
    onSuccess: (r) => { setPlan(null); setStep('queued'); setJob({ id: r.job_id, planId: r.plan_id }) },
    onError: (e: Error) => toast.error(e.message),
  })

  useSSE(
    job ? `/jobs/${job.id}/stream` : '',
    (raw) => {
      let j: { status?: string; progress?: { message?: string; sub_step?: string } }
      try { j = JSON.parse(raw) } catch { return }
      if (j.progress) setStep(`${j.progress.sub_step ?? ''} ${j.progress.message ?? ''}`.trim())
      if (j.status === 'done' && job) {
        const id = job.planId
        setJob(null)
        // The job stored the finished plan under its id; fetch the rows for the table.
        nexusApi.transferCandidates(id, undefined, 0, 400)
          .then((rows) => {
            setPlan({
              plan_id: id, ok: true, mod_name: mod, language,
              donor_mod_id: null, donor_name: '', archive: '', archive_bytes: 0,
              extracted: { files: 0, bytes: 0, tool: '' },
              harvest: { plugin_count: 0, source_count: 0, string_count: 0, by_kind: {}, failed: [], plugins: [] },
              plan: {
                mod_name: mod, language, donor_total: 0, our_total: 0,
                counts: rows.candidates.reduce((acc, c) => {
                  acc[c.action] = (acc[c.action] ?? 0) + 1
                  return acc
                }, {} as Record<string, number>),
                counts_by_kind: {}, usable: 0,
                candidates: rows.candidates, truncated: Math.max(0, rows.total - rows.candidates.length),
              },
              applied: {}, cleaned: true, elapsed: 0, error: '',
            } as TransferPlan)
            qc.invalidateQueries({ queryKey: ['mods', mod, 'strings'] })
            qc.invalidateQueries({ queryKey: QK.mod(mod) })
            toast.success(`${rows.total} candidate${rows.total === 1 ? '' : 's'}`)
          })
          .catch((e: Error) => toast.error(e.message))
      }
      if (j.status === 'failed') { setJob(null); toast.error('The transfer job failed — see Jobs') }
    },
    job !== null,
  )

  const applyMut = useMutation({
    mutationFn: () => nexusApi.transferApply({ plan_id: plan!.plan_id, overwrite, status }),
    onSuccess: (r) => {
      toast.success(`${r.applied} string${r.applied === 1 ? '' : 's'} written as ${r.status}`
        + (r.dict_entries ? ` · ${r.dict_entries} into the dictionary` : ''))
      qc.invalidateQueries({ queryKey: ['mods', mod, 'strings'] })
      qc.invalidateQueries({ queryKey: QK.mod(mod) })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const counts = plan?.plan?.counts ?? {}
  const shown = useMemo(() => {
    const all = plan?.plan?.candidates ?? []
    return filter === 'all' ? all : all.filter((c) => c.action === filter)
  }, [plan, filter])

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Languages className="w-4 h-4 text-accent" />
        <h3 className="text-sm font-medium text-text-main">Translate From Mod</h3>
        <span className="text-xs text-text-muted">
          take a published translation and fold it into our strings
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          list="our-mods"
          value={mod}
          onChange={(e) => { setMod(e.target.value); setPlan(null) }}
          placeholder="our mod (folder name)"
          className="flex-1 min-w-[16rem] px-2 py-1.5 rounded border border-border-subtle bg-bg-card2 text-sm text-text-main"
        />
        <datalist id="our-mods">
          {(mods as ModInfo[] | undefined)?.map((m) => <option key={m.folder_name} value={m.folder_name} />)}
        </datalist>
        <input
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="w-32 px-2 py-1.5 rounded border border-border-subtle bg-bg-card2 text-sm text-text-main"
        />
        <Button variant="secondary" disabled={!mod || job !== null}
                loading={startMut.isPending && startMut.variables?.apply === true}
                onClick={() => startMut.mutate({ apply: true })}>
          One-shot
        </Button>
      </div>

      {job && (
        <div className="mt-3 flex items-center gap-2 text-xs text-text-muted">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          <span>{step || 'running'}</span>
          <a href={`/app/jobs/${job.id}`} className="text-accent hover:underline">open in Jobs</a>
        </div>
      )}

      {donorsQuery.data && donorsQuery.data.results.length > 0 && (
        <div className="mt-3">
          <p className="text-xs text-text-muted mb-1">
            Published {language} translations, best match first
          </p>
          <div className="divide-y divide-border-subtle">
            {donorsQuery.data.results.map((d) => (
              <div key={d.mod_id} className="flex items-center gap-3 py-1.5">
                <span className="w-12 text-xs text-accent tabular-nums">{d.score.toFixed(2)}</span>
                <div className="min-w-0 flex-1">
                  <a href={`https://www.nexusmods.com/${d.game}/mods/${d.mod_id}`}
                     target="_blank" rel="noreferrer"
                     className="text-sm text-text-main hover:text-accent truncate block">{d.name}</a>
                  <p className="text-[11px] text-text-muted">
                    {d.downloads.toLocaleString()} downloads
                    {d.covers_title && ' · carries the title'}
                    {d.marked && ' · says translation'}
                    {d.extra_words > 0 && ` · ${d.extra_words} extra word(s)`}
                  </p>
                </div>
                <Button size="sm" disabled={job !== null}
                        loading={startMut.isPending && startMut.variables?.donorId === d.mod_id}
                        onClick={() => startMut.mutate({ donorId: d.mod_id, apply: false })}>
                  Plan
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {mod && donorsQuery.isFetched && donorsQuery.data?.results.length === 0 && (
        <p className="mt-3 text-xs text-text-muted">
          No published {language} translation found for this mod.
        </p>
      )}

      {plan?.error && (
        <p className="mt-3 text-xs text-danger flex items-center gap-1.5">
          <AlertCircle className="w-3.5 h-3.5" /> {plan.error}
        </p>
      )}

      {plan?.plan && (
        <div className="mt-4 border-t border-border-subtle pt-3">
          <div className="flex flex-wrap gap-1.5">
            {(['fill', 'conflict', 'same', 'rejected', 'unmatched'] as MergeAction[]).map((a) => (
              <button key={a} onClick={() => setFilter(a)}
                      title={ACTION_HELP[a]}
                      className={cn('px-2 py-0.5 rounded text-xs border',
                        filter === a ? 'border-accent text-accent' : 'border-border-subtle text-text-muted')}>
                {a} {counts[a] ?? 0}
              </button>
            ))}
            <button onClick={() => setFilter('all')}
                    className={cn('px-2 py-0.5 rounded text-xs border',
                      filter === 'all' ? 'border-accent text-accent' : 'border-border-subtle text-text-muted')}>
              all
            </button>
            {plan.plan.truncated > 0 && (
              <span className="text-[11px] text-text-muted self-center">
                +{plan.plan.truncated} more not shown
              </span>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-text-main">
              <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
              Overwrite existing translations ({counts.conflict ?? 0} conflicts)
            </label>
            <label className="flex items-center gap-1.5 text-xs text-text-main">
              Land as
              <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}
                      className="px-1.5 py-0.5 rounded border border-border-subtle bg-bg-card2">
                <option value="needs_review">needs review</option>
                <option value="translated">translated</option>
              </select>
            </label>
            <Button variant="primary" className="ml-auto"
                    icon={<CheckCircle2 className="w-4 h-4" />}
                    loading={applyMut.isPending}
                    disabled={(counts.fill ?? 0) + (overwrite ? (counts.conflict ?? 0) : 0) === 0}
                    onClick={() => applyMut.mutate()}>
              Apply {(counts.fill ?? 0) + (overwrite ? (counts.conflict ?? 0) : 0)}
            </Button>
          </div>

          <div className="mt-3 max-h-96 overflow-auto">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 bg-bg-card">
                <tr className="text-left text-[11px] text-text-muted">
                  <th className="py-1 pr-3 font-medium">action</th>
                  <th className="py-1 pr-3 font-medium">ours (original)</th>
                  <th className="py-1 pr-3 font-medium">donor</th>
                  <th className="py-1 font-medium">current / why</th>
                </tr>
              </thead>
              <tbody>
                {shown.slice(0, 300).map((c, i) => <CandidateRow key={`${c.esp_name}-${c.key}-${i}`} c={c} />)}
              </tbody>
            </table>
            {shown.length === 0 && (
              <p className="py-4 text-center text-xs text-text-muted">nothing in this bucket</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}


// ── page ──────────────────────────────────────────────────────────────────────

function NexusPage() {
  const [batchId, setBatchId] = useState<string | null>(null)

  return (
    <div className="p-6 space-y-4 max-w-[80rem]">
      <div>
        <h1 className="text-2xl font-semibold text-text-main">Nexus</h1>
        <p className="text-sm text-text-muted">
          Find and download mods, and pull existing translations into the store.
        </p>
      </div>

      <AccountPanel />
      <SearchPanel onBatch={setBatchId} />
      {batchId && <DownloadPanel batchId={batchId} onClose={() => setBatchId(null)} />}
      <TransferPanel />
    </div>
  )
}

export const Route = createFileRoute('/nexus')({
  component: NexusPage,
})
