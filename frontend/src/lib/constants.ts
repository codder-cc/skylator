export const SSE_RECONNECT_DELAY = 3000

export const DEFAULT_STRINGS_PER_PAGE = 100

export const HEARTBEAT_TTL = 45000

export const JOB_TERMINAL_STATUSES = ['done', 'failed', 'cancelled', 'paused'] as const

// Statuses that require active polling / SSE stream (not terminal, not settled)
export const JOB_ACTIVE_STATUSES = ['running', 'pending', 'offline_dispatched'] as const

export const SCOPES = ['all', 'esp', 'mcm', 'bsa', 'swf', 'review', 'untranslatable', 'reserved'] as const

export type JobTerminalStatus = (typeof JOB_TERMINAL_STATUSES)[number]
export type Scope = (typeof SCOPES)[number]

export const STRING_STATUSES = ['pending', 'translated', 'needs_review'] as const
export type StringStatus = (typeof STRING_STATUSES)[number]

export const STRING_SOURCES = ['ai', 'cache', 'dict', 'manual', 'untranslatable', 'imported'] as const
export type StringSource = (typeof STRING_SOURCES)[number]

export const SOURCE_COLORS: Record<string, string> = {
  ai:             'text-violet-400',
  cache:          'text-sky-400',
  dict:           'text-teal-400',
  manual:         'text-amber-400',
  untranslatable: 'text-slate-300',
  imported:       'text-slate-400',
}

export const TRANSLATION_MODES = ['untranslated', 'needs_review', 'force_all'] as const
export type TranslationMode = (typeof TRANSLATION_MODES)[number]

export const DEPLOY_MODES = ['all', 'skip_untranslated', 'skip_partial', 'skip_issues'] as const
export type DeployMode = (typeof DEPLOY_MODES)[number]
