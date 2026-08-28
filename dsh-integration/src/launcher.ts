#!/usr/bin/env node
import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { provisionMaidAiProfile } from './profile-install.js'

const require = createRequire(import.meta.url)

async function main(): Promise<number> {
  const dshHome = process.env.DSH_HOME
  if (dshHome === undefined || dshHome.trim() === '') {
    process.stderr.write('maidai-dsh-driver: DSH_HOME is required\n')
    return 2
  }
  await provisionMaidAiProfile(dshHome)
  const packageDir = dirname(require.resolve('@deepseek-ai/dsh/package.json'))
  const dshBin = join(packageDir, 'lib', 'bin.js')
  const env = {
    ...process.env,
    DSH_HOME: dshHome,
    DSH_PERMISSION_MODE: 'workspace-write',
    DSH_TELEMETRY_DISABLED: '1',
  }
  const child = spawn(process.execPath, [dshBin, '--profile', 'maidai'], {
    cwd: process.cwd(),
    env,
    stdio: 'inherit',
    windowsHide: true,
  })
  return await new Promise<number>((resolveResult, reject) => {
    child.once('error', reject)
    child.once('exit', (code, signal) => {
      if (signal !== null) resolveResult(1)
      else resolveResult(code ?? 1)
    })
  })
}

main().then(code => {
  process.exitCode = code
}).catch((error: unknown) => {
  process.stderr.write(`maidai-dsh-driver: ${error instanceof Error ? error.message : String(error)}\n`)
  process.exitCode = 1
})
