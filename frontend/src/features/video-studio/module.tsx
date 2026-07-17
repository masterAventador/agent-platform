import type { FrontendCapabilityModule } from '../../app/capability-registry/types'
import { MediaLibraryPage } from './pages/MediaLibraryPage'

const videoStudioModule = {
  capabilityId: 'video-studio',
  frontendEntry: 'video.routes.v1',
  navigation: [
    { label: '素材库', path: '/video/materials' },
  ],
  routes: [
    { path: '/video/materials', Page: MediaLibraryPage },
  ],
} satisfies FrontendCapabilityModule

export default videoStudioModule
