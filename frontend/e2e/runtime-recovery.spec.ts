import { execFileSync } from 'node:child_process'

import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRecoveryDatabase } from './helpers/runtime-infra'


const expectedOutput = 'Recovery E2E completed after the approved tool call.'

test('等待审批时 Worker 被 SIGKILL，接管后可继续并执行一次', async ({ page, request }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await page.getByRole('button', { name: '注册 MCP Server' }).click()
  await page.getByLabel('Server 名称').fill('恢复验收 MCP')
  await page.getByLabel('服务地址').fill('http://127.0.0.1:18093/mcp')
  await page.getByRole('dialog').getByRole('button', { name: '注册 Server' }).click()
  await page.getByRole('button', { name: '登记 Tool' }).click()
  const toolDialog = page.getByRole('dialog')
  await toolDialog.getByLabel('所属 Server').click()
  await page.getByText('恢复验收 MCP', { exact: true }).last().click()
  await toolDialog.getByLabel('Tool 名称').fill('recovery_external')
  await toolDialog.getByLabel('说明').fill('执行一次可审计的恢复验收外部操作')
  await toolDialog.getByLabel('输入 JSON Schema').fill(JSON.stringify({
    type: 'object',
    properties: { value: { type: 'string' } },
    required: ['value'],
    additionalProperties: false,
  }))
  await toolDialog.getByLabel('风险等级').click()
  await page.getByText('外部操作', { exact: true }).last().click()
  await toolDialog.getByRole('button', { name: '登记 Tool' }).click()

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('崩溃恢复验收专员')
  await page.getByLabel('岗位说明').fill('验证真实 Worker 崩溃后的任务恢复')
  await page.getByLabel('系统指令').fill('必须调用 recovery_external，然后输出恢复完成信息。')
  await page.getByLabel('Tools').click()
  await page.getByText('恢复验收 MCP / recovery_external', { exact: true }).last().click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('执行崩溃恢复和外部工具验收')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page.getByText('等待审批', { exact: true })).toBeVisible()
  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const before = queryRecoveryDatabase(
    `select epoch::text || '|' || owner_id from runtime_ownership where run_id='${runId}'`,
  )
  const [oldEpoch, oldOwner] = before.split('|')
  expect(oldEpoch).toBe('1')
  expect(oldOwner).not.toBe('')
  const leaseBeforeKill = queryRecoveryDatabase(
    `select id::text || '|' || sandbox_id || '|' || status from sandbox_leases where run_id='${runId}'`,
  )
  expect(leaseBeforeKill.split('|')[2]).toBe('active')
  expect(await (await request.get('http://127.0.0.1:18093/count')).json()).toEqual({ count: 0 })

  expect((await request.post('http://127.0.0.1:18092/kill')).ok()).toBeTruthy()
  expect((await request.post('http://127.0.0.1:18092/restart')).ok()).toBeTruthy()
  const early = await request.get('http://127.0.0.1:18092/status')
  expect((await early.json()).ready).toBe(false)
  await page.waitForTimeout(1_200)
  expect(queryRecoveryDatabase(
    `select epoch::text || '|' || owner_id from runtime_ownership where run_id='${runId}'`,
  )).toBe(`${oldEpoch}|${oldOwner}`)

  await expect.poll(async () => (await (await request.get('http://127.0.0.1:18092/status')).json()).ready)
    .toBe(true)
  expect(queryRecoveryDatabase(
    `select epoch::text from runtime_ownership where run_id='${runId}'`,
  )).toBe('2')
  expect(queryRecoveryDatabase(
    `select id::text || '|' || sandbox_id || '|' || status from sandbox_leases where run_id='${runId}'`,
  )).toBe(leaseBeforeKill)

  await page.reload()
  await expect(page.getByText('等待审批', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /批\s*准/ }).click()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  expect(queryRecoveryDatabase(
    `select count(*) from run_commands where run_id='${runId}' and action='approve' and processed_at is not null`,
  )).toBe('1')
  expect(await (await request.get('http://127.0.0.1:18093/count')).json()).toEqual({ count: 1 })
  expect(queryRecoveryDatabase(
    `select count(*) from tool_audit_events where run_id='${runId}' and event_type='tool.completed' and succeeded is true`,
  )).toBe('1')
  const lease = queryRecoveryDatabase(
    `select id::text || '|' || sandbox_id || '|' || status from sandbox_leases where run_id='${runId}'`,
  ).split('|')
  expect(`${lease[0]}|${lease[1]}`).toBe(leaseBeforeKill.split('|').slice(0, 2).join('|'))
  expect(lease[2]).toBe('deleted')
  expect(execFileSync('docker', [
    'ps', '-aq', '--filter', 'label=agent-platform.sandbox.managed=true',
    '--filter', `label=agent-platform.sandbox.lease-id=${lease[0]}`,
  ], { encoding: 'utf8' }).trim()).toBe('')
  expect(queryRecoveryDatabase(
    `select coalesce(owner_id, '') from runtime_ownership where run_id='${runId}'`,
  )).toBe('')
})

test('外部副作用后 Worker 被 SIGKILL 会稳定标记 uncertain 且不自动重放', async ({ page, request }) => {
  await registerAndLogin(page)
  expect((await request.post('http://127.0.0.1:18093/reset')).ok()).toBeTruthy()
  expect((await request.post('http://127.0.0.1:18092/kill')).ok()).toBeTruthy()
  expect((await request.post('http://127.0.0.1:18092/reset-model')).ok()).toBeTruthy()
  expect((await request.post('http://127.0.0.1:18092/restart')).ok()).toBeTruthy()
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:18092/status')).json()).ready)
    .toBe(true)

  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await page.getByRole('button', { name: '注册 MCP Server' }).click()
  await page.getByLabel('Server 名称').fill('不确定执行验收 MCP')
  await page.getByLabel('服务地址').fill('http://127.0.0.1:18093/mcp')
  await page.getByRole('dialog').getByRole('button', { name: '注册 Server' }).click()
  await page.getByRole('button', { name: '登记 Tool' }).click()
  const toolDialog = page.getByRole('dialog')
  await toolDialog.getByLabel('所属 Server').click()
  await page.getByText('不确定执行验收 MCP', { exact: true }).last().click()
  await toolDialog.getByLabel('Tool 名称').fill('recovery_external')
  await toolDialog.getByLabel('说明').fill('执行后阻塞响应的不确定外部操作')
  await toolDialog.getByLabel('输入 JSON Schema').fill(JSON.stringify({
    type: 'object',
    properties: { value: { type: 'string' } },
    required: ['value'],
    additionalProperties: false,
  }))
  await toolDialog.getByLabel('风险等级').click()
  await page.getByText('外部操作', { exact: true }).last().click()
  await toolDialog.getByRole('button', { name: '登记 Tool' }).click()

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('不确定执行验收专员')
  await page.getByLabel('岗位说明').fill('验证副作用已发生但响应未知时不自动重放')
  await page.getByLabel('系统指令').fill('必须调用 recovery_external，然后输出恢复完成信息。')
  await page.getByLabel('Tools').click()
  await page.getByText('不确定执行验收 MCP / recovery_external', { exact: true }).last().click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('执行副作用后崩溃的不确定性验收')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page.getByText('等待审批', { exact: true })).toBeVisible()
  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const leaseBeforeKill = queryRecoveryDatabase(
    `select id::text || '|' || sandbox_id || '|' || status from sandbox_leases where run_id='${runId}'`,
  )
  expect(leaseBeforeKill.split('|')[2]).toBe('active')
  expect((await request.post('http://127.0.0.1:18093/block-next')).ok()).toBeTruthy()
  await page.getByRole('button', { name: /批\s*准/ }).click()
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:18093/started')).json()).started)
    .toBe(true)
  expect(await (await request.get('http://127.0.0.1:18093/count')).json()).toEqual({ count: 1 })
  const invocationId = queryRecoveryDatabase(
    `select invocation_id::text from tool_audit_events where run_id='${runId}' and event_type='tool.started'`,
  )
  expect(invocationId).toMatch(/^[0-9a-f-]{36}$/)
  expect(await (await request.get('http://127.0.0.1:18093/last-invocation')).json())
    .toEqual({ invocation_id: invocationId })

  expect((await request.post('http://127.0.0.1:18092/kill')).ok()).toBeTruthy()
  expect((await request.post('http://127.0.0.1:18092/restart')).ok()).toBeTruthy()
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:18092/status')).json()).ready)
    .toBe(true)
  await expect.poll(() => queryRecoveryDatabase(
    `select status || '|' || coalesce(error_code, '') from runs where id='${runId}'`,
  )).toBe('failed|tool_execution_uncertain')
  expect((await request.post('http://127.0.0.1:18093/release')).ok()).toBeTruthy()

  await page.reload()
  await expect(page.getByText('失败', { exact: true })).toBeVisible()
  await page.waitForTimeout(1_500)
  expect(await (await request.get('http://127.0.0.1:18093/count')).json()).toEqual({ count: 1 })
  expect(queryRecoveryDatabase(
    `select count(*) from tool_audit_events where run_id='${runId}' and event_type='tool.started'`,
  )).toBe('1')
  expect(queryRecoveryDatabase(
    `select count(*) from tool_audit_events where run_id='${runId}' and event_type='tool.completed'`,
  )).toBe('0')
  expect(queryRecoveryDatabase(
    `select count(*) from run_commands where run_id='${runId}' and action='approve' and processed_at is not null`,
  )).toBe('1')
  expect(queryRecoveryDatabase(
    `select coalesce(owner_id, '') from runtime_ownership where run_id='${runId}'`,
  )).toBe('')
  const lease = queryRecoveryDatabase(
    `select id::text || '|' || sandbox_id || '|' || status from sandbox_leases where run_id='${runId}'`,
  ).split('|')
  expect(`${lease[0]}|${lease[1]}`).toBe(leaseBeforeKill.split('|').slice(0, 2).join('|'))
  expect(lease[2]).toBe('deleted')
  expect(execFileSync('docker', [
    'ps', '-aq', '--filter', 'label=agent-platform.sandbox.managed=true',
    '--filter', `label=agent-platform.sandbox.lease-id=${lease[0]}`,
  ], { encoding: 'utf8' }).trim()).toBe('')
})
