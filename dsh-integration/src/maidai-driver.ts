import { realpathSync } from 'node:fs'
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

function normalizedPath(path: string): string {
  const value = realpathSync.native(path)
  return process.platform === 'win32' ? value.toLowerCase() : value
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
    const stopEvents = this.ctx.on('session/event', (session, event) => {
      if (session !== agent.session || event.seq < firstSeq) return
      if (event.type === 'tool/call') {
        this.emit({
          type: 'event',
          event: 'tool_activity',
          summary: `正在使用 ${event.data.name}`,
          tool: event.data.name,
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
    this.emit({
      type: 'result',
      finish_reason: reasonKind(outcome.finishReason),
      finish_detail: outcome.finishReason,
      summary: outcome.summary,
      usage: outcome.usage,
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
