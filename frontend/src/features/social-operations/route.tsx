import { lazy } from 'react'

export const SocialOperationsRoute = lazy(() =>
  import('./pages/SocialOperationsPage').then((module) => ({
    default: module.SocialOperationsPage,
  })),
)
