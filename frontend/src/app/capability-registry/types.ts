import type { ComponentType } from 'react'

export interface CapabilityPageProps {
  workspaceId: string
}

export interface CapabilityNavigationEntry {
  label: string
  path: string
}

export interface CapabilityRouteEntry {
  path: string
  Page: ComponentType<CapabilityPageProps>
}

export interface FrontendCapabilityModule {
  capabilityId: string
  frontendEntry: string
  navigation: readonly CapabilityNavigationEntry[]
  routes: readonly CapabilityRouteEntry[]
}
