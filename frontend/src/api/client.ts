import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
  withCredentials: true,
})
