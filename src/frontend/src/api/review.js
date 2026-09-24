import axios from 'axios'

export const API_BASE_URL = 'http://localhost:8000'

const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 90000,
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json'
  }
})

request.interceptors.response.use(
  (res) => {
    return res.data || { code: 200, msg: '操作成功' }
  },
  (err) => {
    console.error('接口请求错误：', err)
    const status = err.response?.status
    const raw = err.response?.data
    if (raw && typeof raw === 'object' && typeof raw.code === 'number') {
      return Promise.resolve(raw)
    }
    let msg = err.message || '服务请求失败'
    if (raw && typeof raw === 'object') {
      if (raw.msg) msg = raw.msg
      else if (raw.detail) msg = typeof raw.detail === 'string' ? raw.detail : JSON.stringify(raw.detail)
    } else if (typeof raw === 'string') {
      msg = raw.length > 300 ? raw.slice(0, 300) + '…' : raw
      try {
        const j = JSON.parse(raw)
        if (j.msg) msg = j.msg
      } catch (_) {}
    }
    if (err.code === 'ECONNABORTED') {
      msg = '请求超时，请稍后重试或缩小筛选范围'
    }
    const code = status === 429 ? 429 : status === 503 ? 503 : 500
    return Promise.resolve({ code, msg, timeout: status === 503 || err.code === 'ECONNABORTED' })
  }
)

/** 列表仅分页数据；超时与后端 wait_for 对齐并留余量 */
export const getReviewListApi = (params, config = {}) =>
  request.get('/get_review_list', { params, timeout: 70000, ...config })

export const getOpinionDetailApi = (params) =>
  request.get('/api/opinion_detail', { params, timeout: 20000 })

export const saveReviewApi = (data) => request.post('/save_review', data, { timeout: 30000 });

export const batchSaveReviewApi = (data) => request.post('/batch_save_review', data, { timeout: 60000 });

/** 暂存本页/分批一二级（不标记已复核、不回流） */
export const draftSaveReviewsApi = (data) => request.post('/draft_save_reviews', data);

/** 大文件上传：延长超时，保证始终解析为 JSON 对象（避免 network error 后无结构） */
export const uploadCsvApi = (file, onProgress) => {
  const formData = new FormData()
  formData.append('file', file)
  return axios
    .post(`${API_BASE_URL}/upload_csv`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
        Accept: 'application/json'
      },
      timeout: 300000,
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total)
          onProgress(percent)
        }
      }
    })
    .then((res) => {
      const d = res.data
      if (d && typeof d === 'object') return d
      return { code: 500, msg: '服务器返回非 JSON，请检查后端日志' }
    })
    .catch((err) => {
      if (err.code === 'ECONNABORTED') {
        return { code: 500, msg: '上传超时（已等待 5 分钟），请缩小文件或稍后重试' }
      }
      const d = err.response?.data
      if (d && typeof d === 'object' && 'code' in d) return d
      const status = err.response?.status
      const msg =
        (typeof d === 'object' && d?.msg) ||
        (typeof d === 'string' ? d : null) ||
        err.message ||
        '上传失败'
      return { code: status === 429 ? 429 : 500, msg }
    })
}

export const getYearlySummaryApi = (params) => request.get('/get_yearly_summary', { params });

export const confirmReviewApi = (data) => request.post('/confirm_review', data, { timeout: 120000 });

/** 确认所选行并写入年度 CSV */
export const confirmAndWriteCsvApi = (data) => request.post('/api/confirm_and_write_csv', data, { timeout: 120000 });

/** 查询批次完成状态与准确率 */
export const batchStatusApi = (data) => request.post('/api/batch_status', data, { timeout: 30000 });

/** 整批确认：对某导入批次内全部已可确认的行一次性确认（不受分页 20 条限制） */
export const confirmReviewBatchApi = (data) => request.post('/confirm_review_batch', data, { timeout: 180000 });

/** 整批确认前预览：返回未就绪条目 */
export const confirmReviewBatchPreviewApi = (params) =>
  request.get('/confirm_review_batch/preview', { params, timeout: 60000 });

export const previewKeywordsApi = (data) => request.post('/api/preview_keywords', data);

export const getVisualData = () => request.get('/get_visual_data');

export const getUploadBatchesApi = () => request.get('/get_upload_batches');

export const saveToYearlyTableApi = (data) => request.post('/save_to_yearly_table', data);

export const exportYearlyCsvApi = () => request.get('/export_yearly_csv', { responseType: 'blob' });

/** 读取 data/annual 下按年 CSV 列表，供年度筛选 */
export const listAnnualCsvApi = () => request.get('/list_annual_csv');

/** 数据汇报：按月一级对比、二级趋势、Top 二级堆叠 */
export const getMonthlyOverviewApi = (params) =>
  request.get('/api/get_monthly_overview', { params, timeout: 120000 });

export const getMonthlySubtagTrendApi = (params) =>
  request.get('/api/get_monthly_subtag_trend', { params, timeout: 120000 });

export const getTopSubtagMonthlyApi = (params) =>
  request.get('/api/get_top_subtag_monthly', { params, timeout: 120000 });

export const getSingleIssueTrendApi = (params) =>
  request.get('/api/get_single_issue_trend', { params, timeout: 120000 });

export const getOpinionSummaryApi = (params) =>
  request.get('/api/get_opinion_summary', { params, timeout: 300000 });

export const batchClearDuplicatesApi = (data) => request.post('/batch_clear_duplicates', data);

export const getKeywordsApi = () => request.get('/api/keywords');

export const getKeywordsStatisticsApi = () => request.get('/api/keywords/statistics');

export const getClassificationAccuracyApi = () => request.get('/api/classification/accuracy');

export const getClassificationLogsApi = (limit = 100) => request.get('/api/classification/logs', { params: { limit } });

export const healthCheckApi = () => request.get('/api/health');

export const getStressTestReportApi = () => request.get('/api/stress_test/report');

/** V1.5：仪表盘统计（饼图/柱图/趋势/待复核） */
/** 概览优先读服务端缓存，正常应快速返回 */
export const getDashboardStatsApi = (config = {}) =>
  request.get('/dashboard_stats', { timeout: 30000, ...config })

/** 批量智能分类（大批量时后端返回 202 + job_id） */
export const batchClassifyApi = (data) => request.post('/batch_classify', data, { timeout: 30000 });

/** 异步分类任务状态 */
export const getBatchClassifyStatusApi = (jobId) =>
  request.get(`/api/batch_classify/status/${encodeURIComponent(jobId)}`, { timeout: 60000 });

/** 批量拉取多一级下的二级白名单（减少 N+1） */
export const taxonomyOptionsBatchApi = (data) =>
  request.post('/api/v3/taxonomy_options_batch', data, { timeout: 45000 });

/** 确认回流至金标 JSON */
export const commitGoldFeedbackApi = (data) => request.post('/commit_gold_feedback', data);

/** 金标二级白名单与三级聚类候选 */
export const getTaxonomyOptionsApi = (params) => request.get('/api/v3/taxonomy_options', { params });

/** 按一级拉取二级白名单（复核表一级→二级联动，与 taxonomy_options 同源） */
export const getL2ByL1Api = (params) =>
  request.get('/get_l2_by_l1', { params: params || {}, timeout: 45000 });

/** 导出复核数据 CSV */
export const exportReviewsCsvApi = (params) =>
  `${API_BASE_URL}/export_reviews_csv?` + new URLSearchParams(params || {}).toString()

/** 综合平台：实时看板与报表扩展 */
export const dashboardKpiApi = () => request.get('/api/dashboard/kpi', { timeout: 15000 });

export const dashboardAnomaliesApi = (days = 7) =>
  request.get('/api/dashboard/anomalies', { params: { days }, timeout: 15000 });

export const dashboardTrendApi = (days = 7) =>
  request.get('/api/dashboard/trend', { params: { days }, timeout: 15000 });

export const reportIssueStatusApi = (uploadBatch = '') =>
  request.get('/api/report/issue_status', { params: { upload_batch: uploadBatch }, timeout: 15000 });

export const reportThemesApi = (params) =>
  request.get('/api/report/themes', { params, timeout: 120000 });

export const reportThemeDetailApi = (themeId, params) =>
  request.get(`/api/report/themes/${encodeURIComponent(themeId)}`, { params, timeout: 120000 });

export const reportThemeOpinionsApi = (themeId, params) =>
  request.get(`/api/report/themes/${encodeURIComponent(themeId)}/opinions`, {
    params,
    timeout: 120000
  });

export const reportThemeExportCsvUrl = (themeId, params = {}) => {
  const q = new URLSearchParams(params).toString()
  return `${API_BASE_URL}/api/report/themes/${encodeURIComponent(themeId)}/export.csv${q ? `?${q}` : ''}`
};

export const reportWeeklyReportsApi = () => request.get('/api/report/weekly_reports', { timeout: 15000 });

export const reportWeeklyReportApi = (week) =>
  request.get('/api/report/weekly_report', { params: { week }, timeout: 15000 });

export const reportGenerateWeeklyApi = (data) =>
  request.post('/api/report/generate_weekly', data, { timeout: 300000 });
