import { createHash } from 'node:crypto'
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path'
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { createInterface } from 'node:readline'
import type { Context } from '@deepseek-ai/cordis'
import {
  installModelSelection,
  type Agent,
  type AgentHandle,
  type ModelSelectionRef,
} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-agent-default-model'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId, type SessionEvent } from '@deepseek-ai/dsh-session'
import {
  DRIVER_PROTOCOL_VERSION,
  parseCommand,
  type DriverCommand,
  type DriverMessage,
} from './protocol.js'

const DRIVER_VERSION = '0.3.0'

interface TurnSummary {
  finishReason: unknown
  summary: string
  usage: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
    cache_read_tokens: number
    cache_write_tokens: number
    reasoning_tokens: number
  }
}

interface FileTarget {
  absolute: string
  relative: string
}

interface ToolCallState {
  callId: string
  name: string
  category: string
  target?: FileTarget
}

interface ToolFailure {
  call_id: string
  tool: string
  code: string
}

interface ToolStateSummary {
  ok: boolean
  calls: number
  results: number
  pending_calls: string[]
  unresolved_failures: ToolFailure[]
  mutation_attempted: boolean
  workspace_changed: boolean
  changed_paths: string[]
}

const MUTATING_TOOLS = new Set(['write', 'edit'])
const SHELL_TOOLS = new Set(['bash', 'pwsh'])

function normalizedPath(path: string): string {
  const value = realpathSync.native(path)
  return process.platform === 'win32' ? value.toLowerCase() : value
}

function comparablePath(path: string): string {
  const value = resolve(path)
  return process.platform === 'win32' ? value.toLowerCase() : value
}

function isInside(root: string, candidate: string): boolean {
  const part = relative(root, candidate)
  return part === '' || (part !== '..' && !part.startsWith(`..${sep}`) && !isAbsolute(part))
}

function safeFileTarget(workspace: string, rawPath: string): FileTarget | undefined {
  const root = normalizedPath(workspace)
  const absolute = comparablePath(isAbsolute(rawPath) ? rawPath : resolve(workspace, rawPath))
  if (!isInside(root, absolute) || absolute === root) return undefined

  let ancestor = absolute
  while (!existsSync(ancestor)) {
    const parent = dirname(ancestor)
    if (parent === ancestor) return undefined
    ancestor = parent
  }
  try {
    if (!isInside(root, normalizedPath(ancestor))) return undefined
  } catch {
    return undefined
  }
  return {
    absolute,
    relative: relative(root, absolute).replaceAll('\\', '/'),
  }
}

function fingerprint(workspace: string, target: string): string {
  const root = normalizedPath(workspace)
  try {
    const real = normalizedPath(target)
    if (!isInside(root, real)) return 'outside'
    const stat = statSync(real)
    if (!stat.isFile()) return `other:${stat.size}:${stat.mtimeMs}`
    const digest = createHash('sha256').update(readFileSync(real)).digest('hex')
    return `file:${digest}`
  } catch (error: unknown) {
    if (error !== null && typeof error === 'object' && 'code' in error) {
      const code = (error as { code?: unknown }).code
      if (code === 'ENOENT') return 'missing'
      if (typeof code === 'string') return `unreadable:${code}`
    }
    return 'unreadable'
  }
}

function parsedFilePath(argumentsJson: string): string | undefined {
  try {
    const value: unknown = JSON.parse(argumentsJson)
    if (value !== null && typeof value === 'object' && 'file_path' in value) {
      const path = (value as { file_path?: unknown }).file_path
      if (typeof path === 'string' && path.trim() !== '') return path
    }
  } catch {
    // DSH itself will report malformed arguments through tool/result.
  }
  return undefined
}

function resultText(event: SessionEvent<'tool/result'>): string {
  const block = event.data.message.content[0]
  return block.content
    .filter(item => item.type === 'text')
    .map(item => item.text)
    .join('\n')
}

function resultErrorCode(event: SessionEvent<'tool/result'>, tool: string): string | undefined {
  const block = event.data.message.content[0]
  if (event.data.error !== undefined) return event.data.error.code
  if (block.isError === true) return 'TOOL_ERROR'
  if (!SHELL_TOOLS.has(tool)) return undefined

  const text = resultText(event)
  if (/\[timed out after [^\]]+\]/i.test(text)) return 'TIMED_OUT'
  if (/\[killed by signal: [^\]]+\]/i.test(text)) return 'KILLED'
  if (/\[sandbox: [^\]]*(?:denied|runner itself failed)[^\]]*\]/i.test(text)) return 'SANDBOX_DENIED'
  const match = text.match(/\[exit code:\s*(-?\d+)\]\s*$/i)
  if (match !== null && Number(match[1]) !== 0) return `EXIT_${match[1]}`
  return undefined
}

function toolCategory(name: string): string {
  if (MUTATING_TOOLS.has(name)) return 'mutation'
  if (SHELL_TOOLS.has(name)) return 'validation'
  return name
}

class ToolRunTracker {
  private readonly calls = new Map<string, ToolCallState>()
  private readonly baselines = new Map<string, { relative: string; fingerprint: string }>()
  private readonly unresolved = new Map<string, ToolFailure>()
  private callCount = 0
  private resultCount = 0
  private mutationAttempted = false

  constructor(private readonly workspace: string) {}

  call(event: SessionEvent<'tool/call'>): void {
    const callId = String(event.data.callId)
    const name = event.data.name
    const state: ToolCallState = { callId, name, category: toolCategory(name) }
    this.callCount += 1
    if (MUTATING_TOOLS.has(name)) {
      this.mutationAttempted = true
      const path = parsedFilePath(event.data.arguments)
      const target = path === undefined ? undefined : safeFileTarget(this.workspace, path)
      if (target !== undefined) {
        state.target = target
        if (!this.baselines.has(target.absolute)) {
          this.baselines.set(target.absolute, {
            relative: target.relative,
            fingerprint: fingerprint(this.workspace, target.absolute),
          })
        }
      }
    }
    this.calls.set(callId, state)
  }

  result(event: SessionEvent<'tool/result'>): { callId: string; tool: string; ok: boolean; code?: string } {
    const block = event.data.message.content[0]
    const callId = String(block.toolCallId)
    const state = this.calls.get(callId)
    const tool = state?.name ?? 'unknown'
    const category = state?.category ?? toolCategory(tool)
    const code = resultErrorCode(event, tool)
    this.resultCount += 1
    this.calls.delete(callId)

    if (code !== undefined) {
      this.unresolved.set(category, { call_id: callId, tool, code })
      return { callId, tool, ok: false, code }
    }

    if (category !== 'mutation') {
      this.unresolved.delete(category)
    } else if (state?.target !== undefined) {
      const baseline = this.baselines.get(state.target.absolute)
      const current = fingerprint(this.workspace, state.target.absolute)
      if (baseline !== undefined && current !== baseline.fingerprint && !current.startsWith('unreadable') && current !== 'outside') {
        this.unresolved.delete(category)
      }
    }
    return { callId, tool, ok: true }
  }

  summary(): ToolStateSummary {
    const changed = [...this.baselines.entries()]
      .filter(([path, baseline]) => {
        const current = fingerprint(this.workspace, path)
        return current !== baseline.fingerprint && !current.startsWith('unreadable') && current !== 'outside'
      })
      .map(([, baseline]) => baseline.relative)
      .sort()
    const pending = [...this.calls.keys()].sort()
    const failures = [...this.unresolved.values()]
    if (this.mutationAttempted && changed.length === 0 && !this.unresolved.has('mutation')) {
      failures.push({ call_id: '', tool: 'write/edit', code: 'NO_WORKSPACE_CHANGE' })
    }
    return {
      ok: pending.length === 0 && failures.length === 0,
      calls: this.callCount,
      results: this.resultCount,
      pending_calls: pending,
      unresolved_failures: failures,
      mutation_attempted: this.mutationAttempted,
      workspace_changed: changed.length > 0,
      changed_paths: changed,
    }
  }
}

function textFromEvent(event: SessionEvent): string {
  if (event.type !== 'assistant/message') return ''
  return event.data.message.content
    .filter(block => block.type === 'text')
    .map(block => block.text)
    .join('')
}

function summarize(events: readonly SessionEvent[], firstSeq: number): TurnSummary {
  let summary = ''
  let finishReason: unknown = { kind: 'error', error: { code: 'NO_TURN_END', message: 'turn did not finish' } }
  const usage = {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
  }
  for (const event of events) {
    if (event.seq < firstSeq) continue
    if (event.type === 'assistant/message') {
      const text = textFromEvent(event)
      if (text !== '') summary = text
      const item = event.data.usage
      if (item !== undefined) {
        usage.input_tokens += item.inputTokens
        usage.output_tokens += item.outputTokens
        usage.total_tokens += item.inputTokens + item.outputTokens
        usage.cache_read_tokens += item.cacheReadTokens ?? 0
        usage.cache_write_tokens += item.cacheWriteTokens ?? 0
        usage.reasoning_tokens += item.reasoningTokens ?? 0
      }
    } else if (event.type === 'turn/end') {
      finishReason = event.data.reason
    }
  }
  return { finishReason, summary, usage }
}

function reasonKind(reason: unknown): string {
  if (reason !== null && typeof reason === 'object' && 'kind' in reason) {
    const kind = (reason as { kind?: unknown }).kind
    if (typeof kind === 'string') return kind
  }
  return 'unknown'
}

export class MaidAiDriver {
  private handle: AgentHandle | undefined
  private sessionId: string | undefined
  private workspace: string | undefined
  private queue: Promise<void> = Promise.resolve()
  private closing = false

  constructor(private readonly ctx: Context) {}

  start(): void {
    const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
    lines.on('line', (line) => {
      if (line.trim() === '') return
      this.queue = this.queue
        .then(async () => this.dispatch(parseCommand(line)))
        .catch(async (error: unknown) => {
          this.emit({
            type: 'error',
            code: 'DRIVER_COMMAND_FAILED',
            message: error instanceof Error ? error.message : String(error),
          })
        })
    })
    lines.on('close', () => {
      this.queue = this.queue.then(async () => {
        if (!this.closing) await this.shutdown(false)
      })
    })
    this.emit({
      type: 'ready',
      protocol_version: DRIVER_PROTOCOL_VERSION,
      driver_version: DRIVER_VERSION,
      workspace: process.cwd(),
    })
  }

  private emit(message: DriverMessage): void {
    process.stdout.write(`${JSON.stringify(message)}\n`)
  }

  private requestFields(command: DriverCommand): Record<string, string> {
    return command.request_id === undefined ? {} : { request_id: command.request_id }
  }

  private ensureWorkspace(command: DriverCommand): void {
    if (command.workspace === undefined) throw new Error('workspace is required')
    const requested = normalizedPath(command.workspace)
    const processWorkspace = normalizedPath(process.cwd())
    if (requested !== processWorkspace) {
      throw new Error(`workspace mismatch: driver is confined to ${process.cwd()}`)
    }
    if (this.workspace !== undefined && normalizedPath(this.workspace) !== requested) {
      throw new Error('one driver process may serve only one workspace')
    }
    this.workspace = command.workspace
  }

  private modelSetup(): {
    provider: string
    model: string
    setup: (agentCtx: Context) => void
  } {
    const defaultModel = this.ctx.get('agentDefaultModel')
    if (defaultModel === undefined) throw new Error('DeepSeek Harness default model is unavailable')
    const selection = defaultModel.currentSelection()
    return {
      provider: selection.provider,
      model: selection.model,
      setup: (agentCtx: Context) => {
        const selected: ModelSelectionRef = { current: selection, assembled: undefined }
        installModelSelection(agentCtx, selected)
      },
    }
  }

  private async create(command: DriverCommand, resume: boolean): Promise<Agent> {
    this.ensureWorkspace(command)
    if (command.session_id === undefined) throw new Error('session_id is required')
    if (this.handle !== undefined) {
      if (this.sessionId !== command.session_id) throw new Error('driver already owns a different session')
      return this.handle.agent
    }
    const agents = this.ctx.get('agents')
    if (agents === undefined) throw new Error('DeepSeek Harness Agent Registry is unavailable')
    const model = this.modelSetup()
    const sessionId = SessionId(command.session_id)
    this.handle = resume
      ? await agents.resume({
          resumeSessionId: sessionId,
          agentOptions: { provider: model.provider, model: model.model },
          setup: model.setup,
        })
      : await agents.create({
          sessionId,
          meta: { cwd: process.cwd() },
          agentOptions: { provider: model.provider, model: model.model },
          setup: model.setup,
        })
    this.sessionId = command.session_id
    await this.handle.agent.whenIdle()
    this.emit({
      type: 'ready',
      session_id: command.session_id,
      resumed: resume,
      ...this.requestFields(command),
    })
    return this.handle.agent
  }

  private async runTask(command: DriverCommand, resume: boolean): Promise<void> {
    if (command.task === undefined) throw new Error('task is required')
    const agent = await this.create(command, resume)
    const firstSeq = agent.session.seq
    const tracker = new ToolRunTracker(command.workspace ?? process.cwd())
    const stopEvents = this.ctx.on('session/event', (session, event) => {
      if (session !== agent.session || event.seq < firstSeq) return
      if (event.type === 'tool/call') {
        tracker.call(event)
        this.emit({
          type: 'event',
          event: 'tool_activity',
          activity: 'call',
          summary: `正在使用 ${event.data.name}`,
          tool: event.data.name,
          call_id: event.data.callId,
          phase: command.phase ?? 'DEVELOPMENT',
          ...this.requestFields(command),
        })
      } else if (event.type === 'tool/result') {
        const result = tracker.result(event)
        this.emit({
          type: 'event',
          event: 'tool_activity',
          activity: 'result',
          summary: result.ok ? `${result.tool} 执行完成` : `${result.tool} 执行失败`,
          tool: result.tool,
          call_id: result.callId,
          ok: result.ok,
          ...result.code === undefined ? {} : { error_code: result.code },
          phase: command.phase ?? 'DEVELOPMENT',
          ...this.requestFields(command),
        })
      }
    })
    this.emit({
      type: 'status',
      phase: command.phase ?? 'DEVELOPMENT',
      status: 'running',
      session_id: command.session_id,
      ...this.requestFields(command),
    })
    try {
      agent.followup(createUserMessage({
        content: [{ type: 'text', text: command.task }],
        source: { kind: 'user' },
      }))
      await agent.whenIdle()
      const sessions = this.ctx.get('sessions')
      if (sessions === undefined) throw new Error('DeepSeek Harness Session store is unavailable')
      await sessions.flush(agent.session)
    } finally {
      stopEvents()
    }
    const outcome = summarize(agent.session.events, firstSeq)
    const toolState = tracker.summary()
    const turnReason = reasonKind(outcome.finishReason)
    const finishReason = turnReason === 'completed' && !toolState.ok ? 'tool_failed' : turnReason
    this.emit({
      type: 'result',
      finish_reason: finishReason,
      finish_detail: outcome.finishReason,
      summary: outcome.summary,
      usage: outcome.usage,
      tool_state: toolState,
      session_id: command.session_id,
      phase: command.phase ?? 'DEVELOPMENT',
      ...this.requestFields(command),
    })
  }

  private async dispatch(command: DriverCommand): Promise<void> {
    switch (command.type) {
      case 'readiness':
        this.emit({
          type: 'ready',
          protocol_version: DRIVER_PROTOCOL_VERSION,
          driver_version: DRIVER_VERSION,
          workspace: process.cwd(),
          ...this.requestFields(command),
        })
        return
      case 'start':
        await this.runTask(command, false)
        return
      case 'resume':
        await this.runTask(command, true)
        return
      case 'run_phase':
        if (this.handle === undefined || this.sessionId !== command.session_id) {
          throw new Error('run_phase requires the active cycle session')
        }
        await this.runTask(command, false)
        return
      case 'suspend':
        await this.shutdown(true, command)
        return
      case 'terminate':
        await this.shutdown(false, command)
        return
    }
  }

  private async shutdown(suspended: boolean, command?: DriverCommand): Promise<void> {
    if (this.closing) return
    this.closing = true
    if (this.handle !== undefined) {
      if (this.handle.agent.status !== 'idle') {
        this.handle.agent.cancel({ kind: 'user' })
        await this.handle.agent.whenIdle()
      }
      const sessions = this.ctx.get('sessions')
      if (sessions !== undefined) await sessions.flush(this.handle.agent.session)
      await this.handle.dispose()
      this.handle = undefined
    }
    this.emit({
      type: 'result',
      finish_reason: suspended ? 'suspended' : 'terminated',
      summary: suspended ? 'DSH session 已安全保存并暂停。' : 'DSH driver 已关闭。',
      session_id: this.sessionId,
      ...command === undefined ? {} : this.requestFields(command),
    })
    const exit = this.ctx.get('appExit') as ((code: number) => void) | undefined
    if (exit !== undefined) exit(0)
  }
}
