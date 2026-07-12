import axios from 'axios'

interface ApiErrorBody {
  detail?: {
    message?: string
  }
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<ApiErrorBody>(error)) {
    return error.response?.data.detail?.message ?? fallback
  }
  return fallback
}
