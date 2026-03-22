export const SSE_RECONNECT_DELAY = 3000

export const DEFAULT_STRINGS_PER_PAGE = 100

export const HEARTBEAT_TTL = 45000

export const JOB_TERMINAL_STATUSES = ['done', 'failed', 'cancelled'] as const

export const SCOPES = ['all', 'esp', 'mcm', 'bsa', 'swf', 'review'] as const

export type JobTerminalStatus = (typeof JOB_TERMINAL_STATUSES)[number]
export type Scope = (typeof SCOPES)[number]
