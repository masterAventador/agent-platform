import type { FrontendCapabilityModule } from '../../app/capability-registry/types'
import { SocialOperationsRoute } from './route'

const socialOperationsModule = {
  capabilityId: 'social-operations',
  frontendEntry: 'social.routes.v1',
  navigation: [
    { label: '设备与平台账号', path: '/video/account' },
  ],
  routes: [
    { path: '/video/account', Page: SocialOperationsRoute },
    { path: '/tiktok/account', Page: SocialOperationsRoute },
  ],
} satisfies FrontendCapabilityModule

export default socialOperationsModule
