import { isAbsolute } from 'node:path'

export const DRIVER_PROTOCOL_VERSION = 1

export type DriverCommandType =
  | 'readiness'
  | 'start'
  | 'resume'
  | 'run_phase'
  | 'suspend'
  | 'terminate'

export interface DriverCommand {
  type: DriverCommandType
  request_id?: string
  session_id?: string
  workspace?: string
  task?: string
  phase?: string
}

export type DriverMessage = Record<string, unknown> & {
  type: 'ready' | 'status' | 'event' | 'result' | 'error'
}

const COMMANDS = new Set<DriverCommandType>([
  'readiness', 'start', 'resume', 'run_phase', 'suspend', 'terminate',
])

function optionalString(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${field} must be a non-empty string`)
  }
  return value
}

export function parseCommand(line: string): DriverCommand {
  let raw: unknown
  try {
    raw = JSON.parse(line)
  } catch {
    throw new Error('input must be one JSON object per line')
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('input must be a JSON object')
  }
  const record = raw as Record<string, unknown>
  if (typeof record.type !== 'string' || !COMMANDS.has(record.type as DriverCommandType)) {
    throw new Error('unsupported command type')
  }
  const command: DriverCommand = {
    type: record.type as DriverCommandType,
  }
  const requestId = optionalString(record.request_id, 'request_id')
  const sessionId = optionalString(record.session_id, 'session_id')
  const workspace = optionalString(record.workspace, 'workspace')
  const task = optionalString(record.task, 'task')
  const phase = optionalString(record.phase, 'phase')
  if (requestId !== undefined) command.request_id = requestId
  if (sessionId !== undefined) command.session_id = sessionId
  if (workspace !== undefined) command.workspace = workspace
  if (task !== undefined) command.task = task
  if (phase !== undefined) command.phase = phase

  if (command.type === 'start' || command.type === 'resume' || command.type === 'run_phase') {
    if (sessionId === undefined || workspace === undefined || task === undefined) {
      throw new Error(`${command.type} requires session_id, workspace, and task`)
    }
    if (!isAbsolute(workspace)) throw new Error('workspace must be an absolute path')
  }
  return command
}
