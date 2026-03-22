import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback } from 'react'
import { configApi } from '@/api/config'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import {
  Save,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Loader2,
} from 'lucide-react'

type BannerStatus = 'success' | 'error' | null

function ConfigPage() {
  const [yaml, setYaml] = useState('')
  const [validationStatus, setValidationStatus] = useState<'idle' | 'valid' | 'invalid'>('idle')
  const [validationError, setValidationError] = useState('')
  const [banner, setBanner] = useState<BannerStatus>(null)
  const [bannerMsg, setBannerMsg] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const configQ = useQuery({
    queryKey: QK.config(),
    queryFn: configApi.getRaw,
    staleTime: Infinity,
  })

  // Initialise local state when server data arrives
  useEffect(() => {
    if (configQ.data !== undefined && yaml === '') {
      setYaml(configQ.data)
    }
  }, [configQ.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const validateMut = useMutation({
    mutationFn: (text: string) => configApi.validate(text),
    onSuccess: (res) => {
      if (res.ok) {
        setValidationStatus('valid')
        setValidationError('')
      } else {
        setValidationStatus('invalid')
        setValidationError(res.errors?.join('\n') ?? 'Validation failed')
      }
    },
    onError: (e: Error) => {
      setValidationStatus('invalid')
      setValidationError(e.message)
    },
  })

  const saveMut = useMutation({
    mutationFn: () => configApi.save(yaml),
    onSuccess: () => {
      setBanner('success')
      setBannerMsg('Configuration saved successfully.')
      setTimeout(() => setBanner(null), 4000)
    },
    onError: (e: Error) => {
      setBanner('error')
      setBannerMsg(e.message)
    },
  })

  const handleChange = useCallback(
    (text: string) => {
      setYaml(text)
      setValidationStatus('idle')
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        validateMut.mutate(text)
      }, 500)
    },
    [validateMut],
  )

  const handleReload = () => {
    configQ.refetch().then((r) => {
      if (r.data !== undefined) {
        setYaml(r.data)
        setValidationStatus('idle')
        setValidationError('')
      }
    })
  }

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const validationIcon = () => {
    if (validateMut.isPending) return <Loader2 className="w-4 h-4 animate-spin text-text-muted" />
    if (validationStatus === 'valid') return <CheckCircle className="w-4 h-4 text-success" />
    if (validationStatus === 'invalid') return <AlertCircle className="w-4 h-4 text-danger" />
    return null
  }

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Configuration</h1>
        <div className="flex gap-2">
          <button
            onClick={handleReload}
            disabled={configQ.isFetching}
            className="flex items-center gap-2 px-3 py-2 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={cn('w-4 h-4', configQ.isFetching && 'animate-spin')} />
            Reload from disk
          </button>
          <button
            onClick={() => validateMut.mutate(yaml)}
            disabled={validateMut.isPending}
            className="flex items-center gap-2 px-3 py-2 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-50 transition-colors"
          >
            <CheckCircle className="w-4 h-4" />
            Validate
          </button>
          <button
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending || validationStatus === 'invalid'}
            className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saveMut.isPending
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Save className="w-4 h-4" />}
            Save
          </button>
        </div>
      </div>

      {/* Save result banner */}
      {banner && (
        <div
          className={cn(
            'flex items-center gap-2 px-4 py-3 rounded border text-sm',
            banner === 'success'
              ? 'bg-success/10 border-success/30 text-success'
              : 'bg-danger/10 border-danger/30 text-danger',
          )}
        >
          {banner === 'success'
            ? <CheckCircle className="w-4 h-4 flex-shrink-0" />
            : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
          {bannerMsg}
        </div>
      )}

      {/* Editor card */}
      <div className="bg-bg-card border border-border-subtle rounded-lg">
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-2">
          <span className="text-sm font-medium text-text-muted">config.yaml</span>
          <div className="ml-auto flex items-center gap-2">
            {validationIcon()}
            {validationStatus === 'valid' && (
              <span className="text-xs text-success">Valid</span>
            )}
            {validationStatus === 'invalid' && (
              <span className="text-xs text-danger">Invalid</span>
            )}
          </div>
        </div>

        {configQ.isLoading ? (
          <div className="flex items-center justify-center py-20 text-text-muted text-sm">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading configuration…
          </div>
        ) : (
          <textarea
            value={yaml}
            onChange={(e) => handleChange(e.target.value)}
            spellCheck={false}
            rows={35}
            className="w-full bg-transparent px-5 py-4 text-sm font-mono text-text-main resize-none outline-none leading-relaxed"
            placeholder="# YAML configuration"
          />
        )}
      </div>

      {/* Validation error block */}
      {validationStatus === 'invalid' && validationError && (
        <div className="bg-danger/10 border border-danger/30 rounded-lg px-5 py-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertCircle className="w-4 h-4 text-danger flex-shrink-0" />
            <span className="text-sm font-medium text-danger">Validation Error</span>
          </div>
          <pre className="text-xs font-mono text-danger/80 whitespace-pre-wrap">{validationError}</pre>
        </div>
      )}
    </div>
  )
}

export const Route = createFileRoute('/config')({
  component: ConfigPage,
})
