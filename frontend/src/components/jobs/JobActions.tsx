/**
 * Job action buttons — pause / cancel / resume / dispatch-back / dispatch-offline / retry /
 * collect. Extracted from routes/jobs/$jobId.tsx (#8 decompose): each is a self-contained
 * {jobId}-in, mutation-out control, so they live together away from the page shell.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import {
  RefreshCw, Pause, XCircle, SkipForward, ArrowDownToLine, RotateCcw,
} from 'lucide-react'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'

export function PauseButton({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.pause(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.job(jobId) }),
  })
  return (
    <button
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-sky-500/20 text-sky-400 border border-sky-500/30 hover:bg-sky-500/30 disabled:opacity-50 transition-colors"
    >
      {mut.isPending ? <RefreshCw size={11} className="animate-spin" /> : <Pause size={11} />}
      Pause
    </button>
  )
}

export function CancelButton({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const mut = useMutation({
    mutationFn: () => jobsApi.cancel(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.job(jobId) })
      setOpen(false)
    },
  })

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 transition-colors"
      >
        <XCircle size={11} />
        Cancel
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Cancel job?"
        description="The job will be stopped. You can resume it later if it's a translate job."
        confirmLabel="Cancel job"
        variant="danger"
        loading={mut.isPending}
        onConfirm={() => mut.mutate()}
      />
    </>
  )
}

export function ResumeButton({ jobId }: { jobId: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.resume(jobId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      if (data.ok && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
  })

  return (
    <button
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50 transition-colors"
    >
      {mut.isPending ? <RefreshCw size={11} className="animate-spin" /> : <SkipForward size={11} />}
      Resume
    </button>
  )
}

export function DispatchBackButton({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const mut = useMutation({
    mutationFn: () => jobsApi.dispatchBack(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.job(jobId) })
      setOpen(false)
    },
  })

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-violet-500/20 text-violet-400 border border-violet-500/30 hover:bg-violet-500/30 transition-colors"
      >
        <ArrowDownToLine size={11} />
        Dispatch Back
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Dispatch back?"
        description="Workers will stop at the next batch boundary and deliver partial results to the host. The job will complete when all buffered results arrive."
        confirmLabel="Dispatch back"
        variant="danger"
        loading={mut.isPending}
        onConfirm={() => mut.mutate()}
      />
    </>
  )
}

export function DispatchOfflineButton({ jobId }: { jobId: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const mut = useMutation({
    mutationFn: () => jobsApi.dispatchOffline(jobId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      setOpen(false)
      if (data.ok && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
  })

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-violet-500/20 text-violet-400 border border-violet-500/30 hover:bg-violet-500/30 transition-colors"
      >
        <ArrowDownToLine size={11} />
        Dispatch Offline
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Dispatch offline?"
        description="The current job will be paused and remaining pending strings will be dispatched to the assigned workers as a standalone offline job. You will be taken to the new job."
        confirmLabel="Dispatch offline"
        loading={mut.isPending}
        onConfirm={() => mut.mutate()}
      />
    </>
  )
}

export function RetryButton({ jobId }: { jobId: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.retry(jobId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      if (data.ok && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
  })

  return (
    <button
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-warning/20 text-warning border border-warning/30 hover:bg-warning/30 disabled:opacity-50 transition-colors"
    >
      {mut.isPending ? <RefreshCw size={11} className="animate-spin" /> : <RotateCcw size={11} />}
      Retry
    </button>
  )
}

export function CollectButton({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const mut = useMutation({
    mutationFn: () => jobsApi.collect(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      setOpen(false)
    },
  })

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-success/20 text-success border border-success/30 hover:bg-success/30 transition-colors"
      >
        <ArrowDownToLine size={11} />
        Deploy What We Have
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Deploy partial results?"
        description="Apply every translated string for this job's mods to the game files now — even if some strings are still pending or a worker died. Pending strings stay pending and can be translated later."
        confirmLabel="Deploy"
        loading={mut.isPending}
        onConfirm={() => mut.mutate()}
      />
    </>
  )
}
