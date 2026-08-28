import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-agent-default-model'
import type {} from '@deepseek-ai/dsh-session'
import { MaidAiDriver } from './maidai-driver.js'

export const name = 'maidai-driver'
export const inject = ['agents', 'agentDefaultModel', 'sessions']

export function apply(ctx: Context): void {
  void (async () => {
    const loader = (ctx as unknown as { get(name: string): { await(): Promise<void> } | undefined }).get('loader')
    await loader?.await()
    new MaidAiDriver(ctx).start()
  })().catch((error: unknown) => {
    process.stdout.write(`${JSON.stringify({
      type: 'error',
      code: 'DRIVER_START_FAILED',
      message: error instanceof Error ? error.message : String(error),
    })}\n`)
    const exit = ctx.get('appExit') as ((code: number) => void) | undefined
    if (exit !== undefined) exit(1)
  })
}

export * from './protocol.js'
