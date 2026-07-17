import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { openSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import {
  composeArgs,
  composeEnvironment,
  frontendRoot,
  minioApiPort,
  postgresDatabaseUrl,
  redisDatabaseUrl,
  repositoryRoot,
} from './compose-core'


const backendRoot = resolve(frontendRoot, '../backend')
export const e2eDatabaseName = 'agent_platform_e2e'
export const e2eDatabaseUrl = postgresDatabaseUrl(e2eDatabaseName)

/** 直接查默认 E2E 库，用于断言调度器真实写下的执行记录（不是界面自说自话）。 */
export function queryE2eDatabase(sql: string): string {
  return execFileSync(
    'docker',
    [
      ...composeArgs, 'exec', '-T', 'postgres',
      'psql', '-U', 'agent_platform', '-d', e2eDatabaseName,
      '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql,
    ],
    { cwd: repositoryRoot, encoding: 'utf8', env: composeEnvironment },
  ).trim()
}

export interface SchedulerProcess {
  port: number
  process: ChildProcess
  kill: () => void
}

/**
 * 调度进程端口从本轮 API 端口派生，跟随隔离栈的随机端口一起漂移，
 * 避免与并行的另一轮隔离验收栈抢固定端口。
 */
export function schedulerPort(offset: number): number {
  const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? process.env.PLATFORM_API_PORT ?? '18000')
  return apiPort + 900 + offset
}

/**
 * 拉起一个**只用来跑调度循环**的 API 进程。
 *
 * 调度器随 API lifespan 运行，因此没有独立的调度器入口；测试用独立进程承载它，
 * 才能真实地 SIGKILL / 重启来验证「重启恢复」，也能同时起两个副本验证
 * 「同一触发点只产生一条执行记录」。Playwright 管理的那个 API 进程关掉了
 * 调度器（见 playwright.config.ts），避免与本进程竞争、让断言不确定。
 */
export interface SchedulerOptions {
  /** 日志落盘路径；用于事后核对该副本**真的认领过触发点**（活着 ≠ 在调度）。 */
  logPath?: string
  /** 每跳最多认领多少个任务。设为 1 可让多副本交替认领，使竞争可观测且不靠运气。 */
  batchLimit?: number
}

export function startScheduler(port: number, options: SchedulerOptions = {}): SchedulerProcess {
  const logFd = options.logPath === undefined ? 'ignore' : openSync(options.logPath, 'a')
  const child = spawn(
    'uv',
    [
      'run', 'uvicorn', 'agent_platform.api.app:app',
      '--host', '127.0.0.1', '--port', String(port),
      // 让 scheduler_tick_completed 的 dispatched/skipped/settled 真正打进日志：
      // 后端未配置 dictConfig，默认 formatter 会把 extra 丢掉。
      '--log-config', resolve(frontendRoot, 'e2e/helpers/scheduler-log-config.json'),
    ],
    {
      cwd: backendRoot,
      // detached：让子进程自成进程组。`uv run` 会再 fork 出真正的 uvicorn 子进程，
      // 只 SIGKILL `uv` 会把 uvicorn 留成孤儿继续调度——那样「重启恢复」用例根本
      // 没杀掉调度器，会因为错误的原因通过。必须按进程组整组杀。
      detached: true,
      env: {
        ...composeEnvironment,
        AGENT_PLATFORM_DATABASE_URL: e2eDatabaseUrl,
        AGENT_PLATFORM_REDIS_URL: redisDatabaseUrl(2),
        AGENT_PLATFORM_MINIO_ENDPOINT: `127.0.0.1:${minioApiPort}`,
        AGENT_PLATFORM_SCHEDULER_ENABLED: 'true',
        // 1 秒节拍：让「到点触发」在测试时长内真实发生，不改变调度语义。
        AGENT_PLATFORM_SCHEDULER_TICK_INTERVAL_SECONDS: '1',
        ...(options.batchLimit === undefined
          ? {}
          : { AGENT_PLATFORM_SCHEDULER_TICK_BATCH_LIMIT: String(options.batchLimit) }),
      },
      stdio: logFd === 'ignore' ? 'ignore' : ['ignore', logFd, logFd],
    },
  )
  return {
    port,
    process: child,
    kill: () => {
      if (child.pid === undefined) return
      try {
        // 负号 = 整个进程组（uv 包装进程 + 真正的 uvicorn 子进程）。
        process.kill(-child.pid, 'SIGKILL')
      } catch {
        // 进程组已经不存在（正常结束或已被杀），无需处理。
      }
    },
  }
}

/** 有界等待调度进程就绪；超时即抛，不做无限轮询。 */
export async function waitForScheduler(
  scheduler: SchedulerProcess,
  timeoutMs = 60_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${scheduler.port}/api/v1/health/live`)
      if (response.ok) return
    } catch {
      // 进程还没起监听，继续等到超时上限为止。
    }
    await new Promise((settle) => setTimeout(settle, 200))
  }
  throw new Error(`调度进程 ${scheduler.port} 在 ${timeoutMs}ms 内未就绪`)
}

/**
 * 等调度器**真正**停止服务。
 *
 * 只看 `uv` 包装进程的 exitCode 不足以证明调度器死了：真正跑调度循环的是它 fork
 * 出来的 uvicorn 子进程。这里以「端口不再响应」为准——只要还能连上，就说明还有
 * 副本在 tick，此时断言「重启后恢复」就是自欺欺人。
 */
export async function waitForExit(
  scheduler: SchedulerProcess,
  timeoutMs = 30_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const wrapperExited = scheduler.process.exitCode !== null
      || scheduler.process.signalCode !== null
    if (wrapperExited && !(await isServing(scheduler.port))) return
    await new Promise((settle) => setTimeout(settle, 100))
  }
  throw new Error(`调度进程 ${scheduler.port} 在 ${timeoutMs}ms 内没有真正停止服务`)
}

/** 端口是否还有调度进程在服务。 */
export async function isServing(port: number): Promise<boolean> {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/v1/health/live`, {
      signal: AbortSignal.timeout(2_000),
    })
    return response.ok
  } catch {
    return false
  }
}


/**
 * 该副本认领过的触发点数量（dispatched + 业务 skipped）。
 *
 * 只看 `scheduler_tick_completed` 这条日志——它仅在本跳真的 dispatch/skip/settle 过
 * 才会打印，因此计数 > 0 直接证明**这个副本**认领过触发点，而不只是端口活着。
 * 良性竞态（NOOP）按阶段一的设计不计入任何业务指标，所以不会污染该计数。
 */
export function countClaimedTriggerPoints(logPath: string): number {
  let content = ''
  try {
    content = readFileSync(logPath, 'utf8')
  } catch {
    return 0
  }
  let claimed = 0
  for (const line of content.split('\n')) {
    const match = /scheduler_tick_completed dispatched=(\d+|-) skipped=(\d+|-)/.exec(line)
    if (match === null) continue
    claimed += (match[1] === '-' ? 0 : Number(match[1])) + (match[2] === '-' ? 0 : Number(match[2]))
  }
  return claimed
}
