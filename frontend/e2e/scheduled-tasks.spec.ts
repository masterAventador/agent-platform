import { expect, test, type Page } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import {
  isServing,
  queryE2eDatabase,
  schedulerPort,
  startScheduler,
  waitForExit,
  waitForScheduler,
  type SchedulerProcess,
} from './helpers/scheduled-tasks'


const PASSWORD = 'correct horse battery staple'

/** 用真实用户路径建一个开启了定时任务能力的已发布数字员工。 */
async function publishSchedulableEmployee(page: Page, name: string): Promise<void> {
  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill(name)
  await page.getByLabel('岗位说明').fill('验证定时任务调度')
  await page.getByLabel('系统指令').fill('按计划执行巡检并汇报。')
  // C12 阶段二解除了前端对该能力的硬关闭；没有它后端会以 409 拒绝建定时任务。
  await page.getByRole('checkbox', { name: /定时任务/ }).check()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
}

async function openCreateForm(page: Page): Promise<void> {
  await page.getByRole('link', { name: '定时任务' }).click()
  await expect(page.getByRole('heading', { name: '定时任务中心' })).toBeVisible()
  await page.getByRole('button', { name: /创建定时任务/ }).click()
}

async function pickOption(page: Page, label: string, optionText: string): Promise<void> {
  await page.getByLabel(label, { exact: true }).click()
  await page.locator('.ant-select-dropdown:visible')
    .getByTitle(optionText, { exact: true })
    .click()
}

async function selectTimezone(page: Page, timezone: string): Promise<void> {
  // exact: true 是必需的——getByLabel 默认按子串匹配，而员工名里可能含「时区」
  // 二字（如「时区验收专员」），选中后会与本控件同时命中。
  const combo = page.getByLabel('时区', { exact: true })
  await combo.click()
  await page.keyboard.type(timezone)
  await page.locator('.ant-select-dropdown:visible')
    .getByTitle(timezone, { exact: true })
    .click()
}

// C12 完成定义第 5、6 条：客户端创建 + 时区，用户在页面选 IANA 时区提交后，
// 回显的「下次执行时间」必须与所选时区自洽（而不是浏览器本地时区）。
test('创建 Cron 定时任务后按所选时区回显自洽的下次执行时间', async ({ page }) => {
  test.setTimeout(180_000)
  await registerAndLogin(page)
  await publishSchedulableEmployee(page, '时区验收专员')

  await openCreateForm(page)
  const dialog = page.getByRole('dialog')
  await pickOption(page, '数字员工', '时区验收专员')
  await dialog.getByLabel('任务名称').fill('纽约工作日早九点巡检')
  await dialog.getByLabel('Cron 表达式').fill('0 9 * * 1-5')
  await selectTimezone(page, 'America/New_York')
  await dialog.getByRole('button', { name: /创\s*建/ }).click()

  const row = page.getByRole('row', { name: /纽约工作日早九点巡检/ })
  await expect(row).toBeVisible()
  await expect(row).toContainText('Cron 0 9 * * 1-5（America/New_York）')
  // 关键断言：无论浏览器在哪个时区，展示的当地时间必须正好是 Cron 写的 09:00。
  await expect(row).toContainText(/\d{4}-\d{2}-\d{2} 09:00 \(America\/New_York\)/)
  await expect(row).toContainText('启用中')

  // 后端存的是 UTC 瞬时，且与所选时区换算一致（09:00 纽约 = 13:00Z 或 14:00Z）。
  const nextRunAt = queryE2eDatabase(
    "select to_char(next_run_at at time zone 'UTC', 'HH24:MI') from scheduled_tasks"
    + " where name='纽约工作日早九点巡检'",
  )
  expect(['13:00', '14:00']).toContain(nextRunAt)
})

// C12 完成定义第 6 条（时区/DST）：跨夏令时边界的两个当地 09:00 必须换算成
// 不同的 UTC 瞬时，且都按所选时区回显为 09:00。
test('单次预约跨 DST 边界时换算正确且回显与所选时区自洽', async ({ page }) => {
  test.setTimeout(180_000)
  await registerAndLogin(page)
  await publishSchedulableEmployee(page, 'DST 验收专员')

  // 冬令时（EST，UTC-5）：09:00 当地 = 14:00Z
  await openCreateForm(page)
  let dialog = page.getByRole('dialog')
  await pickOption(page, '数字员工', 'DST 验收专员')
  await dialog.getByLabel('任务名称').fill('冬令时预约')
  await dialog.getByRole('radio', { name: '单次预约' }).click()
  await dialog.getByLabel('预约时间').fill('2027-01-15T09:00')
  await selectTimezone(page, 'America/New_York')
  await dialog.getByRole('button', { name: /创\s*建/ }).click()
  await expect(page.getByRole('row', { name: /冬令时预约/ })).toBeVisible()

  // 夏令时（EDT，UTC-4）：同样的当地 09:00 = 13:00Z
  await openCreateForm(page)
  dialog = page.getByRole('dialog')
  await pickOption(page, '数字员工', 'DST 验收专员')
  await dialog.getByLabel('任务名称').fill('夏令时预约')
  await dialog.getByRole('radio', { name: '单次预约' }).click()
  await dialog.getByLabel('预约时间').fill('2027-07-15T09:00')
  await selectTimezone(page, 'America/New_York')
  await dialog.getByRole('button', { name: /创\s*建/ }).click()
  await expect(page.getByRole('row', { name: /夏令时预约/ })).toBeVisible()

  // 同一当地时刻，因 DST 落在不同 UTC 瞬时——这正是「时区不是固定偏移」的证据。
  expect(queryE2eDatabase(
    "select to_char((run_at) at time zone 'UTC', 'HH24:MI') from scheduled_tasks"
    + " where name='冬令时预约'",
  )).toBe('14:00')
  expect(queryE2eDatabase(
    "select to_char((run_at) at time zone 'UTC', 'HH24:MI') from scheduled_tasks"
    + " where name='夏令时预约'",
  )).toBe('13:00')

  // 而用户看到的当地时间两条都是 09:00，与他填的一致。
  await expect(page.getByRole('row', { name: /冬令时预约/ }))
    .toContainText('2027-01-15 09:00 (America/New_York)')
  await expect(page.getByRole('row', { name: /夏令时预约/ }))
    .toContainText('2027-07-15 09:00 (America/New_York)')
})

// C12 完成定义第 6 条（重复触发 + 重启恢复）：两个调度副本竞争同一触发点只
// 产生一条执行记录；SIGKILL 后重启，用户看到的执行历史连续且不重复。
test('两副本竞争不重复触发，调度进程重启后执行历史连续无重复', async ({ page }) => {
  test.setTimeout(420_000)
  const schedulers: SchedulerProcess[] = []
  try {
    await registerAndLogin(page)
    await publishSchedulableEmployee(page, '重复触发验收专员')

    await openCreateForm(page)
    const dialog = page.getByRole('dialog')
    await pickOption(page, '数字员工', '重复触发验收专员')
    await dialog.getByLabel('任务名称').fill('每分钟巡检')
    await dialog.getByLabel('Cron 表达式').fill('* * * * *')
    await selectTimezone(page, 'Asia/Shanghai')
    await dialog.getByRole('button', { name: /创\s*建/ }).click()
    await expect(page.getByRole('row', { name: /每分钟巡检/ })).toBeVisible()

    const taskId = queryE2eDatabase("select id::text from scheduled_tasks where name='每分钟巡检'")
    expect(taskId).toMatch(/^[0-9a-f-]{36}$/)

    // 两个副本同时抢同一个触发点：这是多副本部署的真实形态。
    schedulers.push(startScheduler(schedulerPort(1)), startScheduler(schedulerPort(2)))
    await Promise.all(schedulers.map((scheduler) => waitForScheduler(scheduler)))

    await page.getByRole('link', { name: /每分钟巡检/ }).click()
    await expect(page.getByRole('heading', { name: '每分钟巡检' })).toBeVisible()

    // 等到真实触发（有界：Cron 每分钟一跳，最多等两跳）。
    await expect.poll(
      () => Number(queryE2eDatabase(
        `select count(*) from scheduled_task_executions where scheduled_task_id='${taskId}'`,
      )),
      { timeout: 150_000, intervals: [2_000] },
    ).toBeGreaterThan(0)

    const beforeRestart = Number(queryE2eDatabase(
      `select count(*) from scheduled_task_executions where scheduled_task_id='${taskId}'`,
    ))
    // 每个触发点最多一条执行记录——两个副本没有各自产生一条。
    expect(queryE2eDatabase(
      'select count(*) from (select scheduled_for from scheduled_task_executions'
      + ` where scheduled_task_id='${taskId}' group by scheduled_for having count(*) > 1) duplicates`,
    )).toBe('0')
    // 也没有产生比执行记录更多的 Run（不存在孤儿 Run）。
    expect(Number(queryE2eDatabase(
      'select count(*) from runs where id in (select run_id from scheduled_task_executions'
      + ` where scheduled_task_id='${taskId}' and run_id is not null)`,
    ))).toBeLessThanOrEqual(beforeRestart)

    // 用户视角：执行记录逐条可见，触发时间按任务时区渲染。
    await page.reload()
    const executions = page.getByRole('region', { name: '执行记录' })
    await expect(executions.getByRole('row').filter({ hasText: /\(Asia\/Shanghai\)/ }).first())
      .toBeVisible()

    // 崩溃：直接 SIGKILL 两个副本（整组），不给优雅退出的机会。
    for (const scheduler of schedulers) scheduler.kill()
    await Promise.all(schedulers.map((scheduler) => waitForExit(scheduler)))
    // 证明调度器**真的**停了：端口不再服务。否则下面的「重启后恢复」会因为
    // 旧副本其实还活着而假通过。
    for (const scheduler of schedulers) {
      expect(await isServing(scheduler.port)).toBe(false)
    }
    const afterKill = Number(queryE2eDatabase(
      `select count(*) from scheduled_task_executions where scheduled_task_id='${taskId}'`,
    ))
    // 停机窗口内历史必须冻结：没有任何副本在推进它。这是「重启后又开始推进」
    // 这一断言的对照组——没有它，用例无法区分「恢复了」和「根本没停过」。
    await new Promise((settle) => setTimeout(settle, 65_000))
    expect(Number(queryE2eDatabase(
      `select count(*) from scheduled_task_executions where scheduled_task_id='${taskId}'`,
    ))).toBe(afterKill)

    // 重启一个副本：调度状态只存在于数据库，重启必须从中恢复。
    const restarted = startScheduler(schedulerPort(3))
    schedulers.push(restarted)
    await waitForScheduler(restarted)

    await expect.poll(
      () => Number(queryE2eDatabase(
        `select count(*) from scheduled_task_executions where scheduled_task_id='${taskId}'`,
      )),
      { timeout: 150_000, intervals: [2_000] },
    ).toBeGreaterThan(afterKill)

    // 重启后仍然：没有任何触发点被重复执行（既没补重、也没丢）。
    expect(queryE2eDatabase(
      'select count(*) from (select scheduled_for from scheduled_task_executions'
      + ` where scheduled_task_id='${taskId}' group by scheduled_for having count(*) > 1) duplicates`,
    )).toBe('0')
    // 任务仍启用、下次执行时间仍在推进，没有因重启停摆。
    expect(queryE2eDatabase(`select enabled::text from scheduled_tasks where id='${taskId}'`))
      .toBe('true')
    expect(queryE2eDatabase(
      `select (next_run_at is not null)::text from scheduled_tasks where id='${taskId}'`,
    )).toBe('true')

    // 用户在页面上看到的历史是连续的：条数只增不减，且每个触发点一条。
    await page.reload()
    await expect(page.getByRole('heading', { name: '每分钟巡检' })).toBeVisible()
    await expect.poll(
      () => page.getByRole('region', { name: '执行记录' })
        .getByRole('row').filter({ hasText: /\(Asia\/Shanghai\)/ }).count(),
      { timeout: 30_000, intervals: [1_000] },
    ).toBeGreaterThanOrEqual(afterKill)
  } finally {
    for (const scheduler of schedulers) scheduler.kill()
    await Promise.all(schedulers.map((scheduler) => waitForExit(scheduler).catch(() => undefined)))
  }
})

// C12 完成定义第 6 条（权限）：无 runs.manage 的成员只能看到自己创建的定时任务；
// 访问他人任务按 404 处理（与 runs 语义一致），前端隐藏入口之外后端必须真的拒绝。
test('成员看不到他人的定时任务，直达详情与写操作一律 404', async ({ page, browser }) => {
  test.setTimeout(240_000)
  await registerAndLogin(page)
  await publishSchedulableEmployee(page, '权限验收专员')

  await openCreateForm(page)
  const dialog = page.getByRole('dialog')
  await pickOption(page, '数字员工', '权限验收专员')
  await dialog.getByLabel('任务名称').fill('所有者的私有巡检')
  await dialog.getByLabel('Cron 表达式').fill('0 9 * * 1-5')
  await selectTimezone(page, 'Asia/Shanghai')
  await dialog.getByRole('button', { name: /创\s*建/ }).click()
  await expect(page.getByRole('row', { name: /所有者的私有巡检/ })).toBeVisible()

  const taskId = queryE2eDatabase(
    "select id::text from scheduled_tasks where name='所有者的私有巡检'",
  )
  expect(taskId).toMatch(/^[0-9a-f-]{36}$/)

  // 邀请一个同企业的普通成员（member 角色有 runs.execute、无 runs.manage）。
  const memberEmail = `e2e-c12-member-${Date.now()}@example.com`
  await page.getByRole('link', { name: '企业成员' }).click()
  await page.getByLabel('邀请邮箱').fill(memberEmail)
  await page.getByRole('button', { name: '发送邀请' }).click()
  const token = (await page.getByTestId('invitation-token').innerText()).trim()

  const memberContext = await browser.newContext()
  const memberPage = await memberContext.newPage()
  try {
    await memberPage.goto('/register')
    await memberPage.getByLabel('邮箱').fill(memberEmail)
    await memberPage.getByLabel('密码', { exact: true }).fill(PASSWORD)
    await memberPage.getByLabel('确认密码').fill(PASSWORD)
    await memberPage.getByRole('button', { name: '创建账号' }).click()
    await memberPage.waitForURL(/\/login$/)
    await memberPage.getByLabel('邮箱').fill(memberEmail)
    await memberPage.getByLabel('密码', { exact: true }).fill(PASSWORD)
    await memberPage.getByRole('button', { name: /登\s*录/ }).click()
    await memberPage.waitForURL(/\/$/)

    await memberPage.getByRole('link', { name: '账号设置' }).click()
    await memberPage.getByLabel('邀请令牌').fill(token)
    await memberPage.getByRole('button', { name: '接受邀请' }).click()
    await expect(memberPage.getByText('已加入企业，可在左上角切换工作区')).toBeVisible()

    // 工作区名字由注册流程生成，不猜：直接从库里取这条任务所属企业的名字。
    const ownerWorkspaceName = queryE2eDatabase(
      'select t.name from tenants t join scheduled_tasks s on s.tenant_id = t.id'
      + ` where s.id='${taskId}'`,
    )
    expect(ownerWorkspaceName).not.toBe('')
    await memberPage.getByLabel('当前工作区').click()
    await memberPage.locator('.ant-select-dropdown:visible')
      .getByText(ownerWorkspaceName, { exact: true })
      .first()
      .click()

    // member 有 runs.execute，入口可见（体验裁剪只针对无权限者）。
    await memberPage.getByRole('link', { name: '定时任务' }).click()
    await expect(memberPage.getByRole('heading', { name: '定时任务中心' })).toBeVisible()
    // 但看不到他人创建的任务。
    await expect(memberPage.getByText('还没有定时任务')).toBeVisible()
    await expect(memberPage.getByRole('row', { name: /所有者的私有巡检/ })).toHaveCount(0)

    // 直达他人任务详情：按不存在处理，不泄露任务是否存在。
    await memberPage.goto(`/scheduled-tasks/${taskId}`)
    await expect(memberPage.getByText(/定时任务不存在或你无权访问/)).toBeVisible()

    // 前端隐藏不能替代后端授权：member 明确带上 owner 企业的租户头（他确实是该企业成员，
    // 所以能过租户校验）直接打接口，仍必须一律 404——这才是真正的越权尝试。
    const ownerTenantId = queryE2eDatabase(
      `select tenant_id::text from scheduled_tasks where id='${taskId}'`,
    )
    const headers = { 'X-Tenant-ID': ownerTenantId }
    for (const request of [
      memberPage.request.get(`/api/v1/scheduled-tasks/${taskId}`, { headers }),
      memberPage.request.get(`/api/v1/scheduled-tasks/${taskId}/executions`, { headers }),
      memberPage.request.post(`/api/v1/scheduled-tasks/${taskId}/pause`, { headers }),
      memberPage.request.post(`/api/v1/scheduled-tasks/${taskId}/resume`, { headers }),
      memberPage.request.delete(`/api/v1/scheduled-tasks/${taskId}`, { headers }),
    ]) {
      expect((await request).status()).toBe(404)
    }
    // 列表接口对同企业的 member 只返回他自己创建的任务（不是 404，而是看不到）。
    const listed = await memberPage.request.get('/api/v1/scheduled-tasks', { headers })
    expect(listed.status()).toBe(200)
    expect((await listed.json()).items).toEqual([])

    // 任务确实没被越权删除。
    expect(queryE2eDatabase(`select count(*) from scheduled_tasks where id='${taskId}'`)).toBe('1')
  } finally {
    await memberContext.close()
  }
})
