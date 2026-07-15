export function showBootstrapError(error: unknown): void {
  console.error('frontend_bootstrap_failed', error)

  const root = document.getElementById('root')
  if (root === null) return

  const alert = document.createElement('main')
  alert.className = 'bootstrap-error'
  alert.setAttribute('role', 'alert')
  if (import.meta.env.MODE === 'tauri-test') {
    const diagnostic = error instanceof Error
      ? `${error.name}:${error.message}`
      : `Unknown:${String(error)}`
    alert.dataset.testDiagnostic = diagnostic.slice(0, 500)
  }

  const title = document.createElement('h1')
  title.textContent = '客户端启动失败'

  const message = document.createElement('p')
  message.textContent = '请重新启动客户端；如果问题持续出现，请联系管理员检查客户端 API 配置。'

  alert.append(title, message)
  root.replaceChildren(alert)
}
