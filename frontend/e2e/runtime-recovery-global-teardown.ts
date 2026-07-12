export default async function runtimeRecoveryGlobalTeardown() {
  try {
    await fetch('http://127.0.0.1:18093/release', { method: 'POST' })
  } catch {
    // MCP fixture may already be stopped; the shell harness owns final cleanup.
  }
}
