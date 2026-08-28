import assert from 'node:assert/strict'
import test from 'node:test'
import { parseCommand } from '../lib/protocol.js'

test('accepts the minimal start command', () => {
  const command = parseCommand(JSON.stringify({
    type: 'start',
    session_id: 'maidai-rnd-cycle-001',
    workspace: process.cwd(),
    task: 'Read README.md',
  }))
  assert.equal(command.type, 'start')
  assert.equal(command.session_id, 'maidai-rnd-cycle-001')
})

test('rejects relative workspaces', () => {
  assert.throws(() => parseCommand(JSON.stringify({
    type: 'start', session_id: 's', workspace: '.', task: 'x',
  })), /absolute/)
})
