import axios from 'axios'

// 创建axios实例，统一配置
const request = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json;charset=utf-8'
  }
})

// 响应拦截器：统一处理返回结果
request.interceptors.response.use(
  (res) => {
    return res.data
  },
  (err) => {
    console.error('接口请求失败：', err)
    return { code: 500, msg: '网络异常，请检查网络连接' }
  }
)

// 获取复核列表
export const getReviewListApi = (params) => {
  return request.get('/get_review_list', { params })
}

// 保存复核结果
export const saveReviewApi = (data) => {
  return request.post('/save_review', data)
}