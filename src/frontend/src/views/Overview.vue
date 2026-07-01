<template>
  <div class="overview-wrap" v-loading="dashLoading">
    <div class="overview-actions">
      <el-button :loading="summaryLoading" @click="loadDashboardSummary">生成汇报文案</el-button>
      <el-button type="primary" :loading="dashLoading" @click="refreshDashboard">刷新看板</el-button>
    </div>

    <el-alert
      v-if="dashError"
      type="warning"
      show-icon
      closable
      class="overview-alert"
      @close="dashError = ''"
    >
      {{ dashError }}
    </el-alert>

    <el-alert
      v-if="anomalies.length"
      type="warning"
      show-icon
      class="anomaly-banner"
      :closable="false"
    >
      <template #title>
        <div v-for="a in anomalies" :key="a.l2" class="anomaly-item">
          「{{ a.l2 }}」{{ a.current }} 条（近4周均值 {{ a.avg_4w }}），增幅 {{ a.pct_change }}%
          <template v-if="a.l3_anomalies && a.l3_anomalies.length">
            · 其中：
            <span v-for="l3 in a.l3_anomalies" :key="l3.phrase" class="anomaly-l3">
              「{{ l3.phrase }}」{{ l3.count }} 条
            </span>
          </template>
        </div>
      </template>
    </el-alert>

    <div v-if="summaryData" class="dashboard-summary">
      <div class="dashboard-summary-title">近 7 天汇报摘要</div>
      <p>{{ summaryData.summary }}</p>
      <div v-if="summaryData.ppt_text" class="dashboard-summary-ppt">{{ summaryData.ppt_text }}</div>
    </div>

    <div class="stat-grid">
      <div class="stat-card stat-total">
        <div class="stat-num">{{ dash.total }}</div>
        <div class="stat-txt">总反馈量 / Total Feedback</div>
      </div>
      <div class="stat-card stat-ok">
        <div class="stat-num">{{ dash.reviewed_count }}</div>
        <div class="stat-txt">已复核数量 / Reviewed</div>
      </div>
      <div class="stat-card stat-pending">
        <div class="stat-num">{{ dash.pending_review }}</div>
        <div class="stat-txt">待复核数量 / Pending</div>
      </div>
      <div class="stat-card stat-archive">
        <div class="stat-num">{{ dash.archived_count }}</div>
        <div class="stat-txt">已归档年度 / Archived</div>
      </div>
      <div class="stat-card stat-new">
        <div class="stat-num">{{ dash.new_7d }}</div>
        <div class="stat-txt">近7日新增 / Last 7 Days</div>
      </div>
    </div>

    <div class="accuracy-grid">
      <div class="accuracy-card">
        <div>
          <div class="accuracy-label">一级标签分类准确率 / L1 Accuracy</div>
          <div class="accuracy-sub">基于已人工复核数据 · model_l1 vs review_l1</div>
        </div>
        <div class="accuracy-value">{{ fmtAcc(dash.l1_accuracy) }}</div>
      </div>
      <div class="accuracy-card">
        <div>
          <div class="accuracy-label">二级标签分类准确率 / L2 Accuracy</div>
          <div class="accuracy-sub">业务三类且一级一致 · 非问题不参与</div>
        </div>
        <div class="accuracy-value">{{ fmtAcc(dash.l2_accuracy) }}</div>
      </div>
    </div>

    <div class="chart-grid">
      <div class="chart-card">
        <div class="chart-title">问题分类占比 · L1 Distribution</div>
        <div ref="chartPieRef" class="chart-box"></div>
      </div>
      <div class="chart-card chart-wide">
        <div class="chart-head">
          <span class="chart-title">全局趋势 · Recent 30 Days</span>
          <span class="chart-note">按创建/处理时间统计反馈量</span>
        </div>
        <div ref="chartTrendRef" class="chart-box chart-tall"></div>
      </div>
      <div class="chart-card chart-wide top5-card">
        <div class="chart-title">高频问题 TOP 5 · Top Issues</div>
        <div ref="chartBarRef" class="chart-box chart-tall"></div>
      </div>
    </div>

    <div class="trend-panel" v-if="trend.length">
      <div class="trend-title">近7天 L1 准确率趋势</div>
      <div class="trend-items">
        <div v-for="t in trend" :key="t.full_date || t.date" class="trend-day" :title="`${t.date} · ${t.accuracy}% · ${t.reviewed_count} 条`">
          <div class="trend-column">
            <div class="trend-bar-fill" :style="{ height: `${Math.max(2, Number(t.accuracy) || 0)}%` }"></div>
          </div>
          <div class="trend-label">{{ t.date }}</div>
          <div class="trend-acc">{{ Number(t.accuracy || 0).toFixed(0) }}%</div>
        </div>
      </div>
    </div>

    <el-alert type="info" show-icon :closable="false" class="overview-hint">
      点击饼图扇区或柱状条目，将切换到「复核工作台」并带上对应标签筛选。
    </el-alert>
  </div>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  dashboardAnomaliesApi,
  dashboardTrendApi,
  getDashboardStatsApi,
  getOpinionSummaryApi
} from '@/api/review'

const emit = defineEmits(['drill'])

const dashError = ref('')
const dashLoading = ref(false)
const summaryLoading = ref(false)
const anomalies = ref([])
const trend = ref([])
const summaryData = ref(null)
const dash = ref({
  total: 0,
  reviewed_count: 0,
  pending_review: 0,
  archived_count: 0,
  new_7d: 0,
  l1_accuracy: null,
  l2_accuracy: null,
  l1_pie: [],
  l2_top5: [],
  trend_30d: []
})

const chartPieRef = ref(null)
const chartBarRef = ref(null)
const chartTrendRef = ref(null)
let chartPie = null
let chartBar = null
let chartTrend = null

const fmtAcc = (v) => (v === null || v === undefined ? '--' : `${Number(v).toFixed(2)}%`)

const pad2 = (n) => String(n).padStart(2, '0')
const fmtDate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`

const loadDashboardSummary = async () => {
  if (summaryLoading.value) return
  summaryLoading.value = true
  try {
    const today = new Date()
    const from = new Date(today)
    from.setDate(from.getDate() - 6)
    const res = await getOpinionSummaryApi({
      date_from: fmtDate(from),
      date_to: fmtDate(today),
      region: 'all',
      period: 'week',
      limit: 300
    })
    if (res.code !== 200) throw new Error(res.msg || 'summary')
    summaryData.value = res.data || null
    if (!summaryData.value?.summary) {
      ElMessage.warning('近 7 天暂无可汇总的已复核数据')
    } else {
      ElMessage.success('看板汇报文案已生成')
    }
  } catch (e) {
    console.error(e)
    ElMessage.warning(e.message || '汇报文案生成失败')
  } finally {
    summaryLoading.value = false
  }
}

const loadDash = async () => {
  dashError.value = ''
  try {
    const res = await getDashboardStatsApi()
    if (res.code === 200 && res.data) {
      dash.value = { ...dash.value, ...res.data }
      if (res.data.stale_fallback) {
        dashError.value = res.msg || '当前为缓存或降级数据，后台仍在刷新统计'
      }
    } else if (res.code === 503 || res.timeout) {
      dashError.value = res.msg || '统计数据加载超时，请稍后重试'
      ElMessage.warning(dashError.value)
    } else {
      dashError.value = res.msg || '仪表盘统计不可用'
      ElMessage.warning(dashError.value)
    }
    await nextTick()
    renderCharts()
  } catch (e) {
    console.error(e)
    dashError.value = '加载仪表盘统计失败，请检查后端或网络'
    ElMessage.error(dashError.value)
    await nextTick()
    renderCharts()
  }
}

const loadAnomalies = async () => {
  try {
    const res = await dashboardAnomaliesApi(7)
    anomalies.value = Array.isArray(res) ? res : (res.data || [])
  } catch {
    anomalies.value = []
  }
}

const loadTrend = async () => {
  try {
    const res = await dashboardTrendApi(7)
    trend.value = Array.isArray(res) ? res : (res.data || [])
  } catch {
    trend.value = []
  }
}

const refreshDashboard = async () => {
  dashLoading.value = true
  try {
    await Promise.all([loadDash(), loadAnomalies(), loadTrend()])
  } finally {
    dashLoading.value = false
  }
}

const renderCharts = () => {
  const pieEl = chartPieRef.value
  const barEl = chartBarRef.value
  const trEl = chartTrendRef.value
  if (!pieEl || !barEl || !trEl) return
  if (!chartPie) chartPie = echarts.init(pieEl)
  if (!chartBar) chartBar = echarts.init(barEl)
  if (!chartTrend) chartTrend = echarts.init(trEl)

  const pieData = (dash.value.l1_pie || []).map((x) => ({ name: x.name, value: x.value }))
  chartPie.setOption({
    backgroundColor: 'transparent',
    color: ['#EECA1F', '#3B82F6', '#22c55e', '#a855f7'],
    tooltip: { trigger: 'item', backgroundColor: 'rgba(15,20,25,0.94)', borderColor: '#334155', textStyle: { color: '#e2e8f0' } },
    legend: { bottom: 2, textStyle: { color: '#cbd5e1' } },
    series: [{
      type: 'pie',
      radius: ['50%', '72%'],
      center: ['50%', '45%'],
      data: pieData.length ? pieData : [{ name: '暂无', value: 1 }],
      label: { formatter: '{b}\n{d}%', color: '#e5e7eb' }
    }]
  })
  chartPie.off('click')
  chartPie.on('click', (p) => {
    const name = p?.name
    if (name && name !== '暂无') emit('drill', { filterL1: String(name) })
  })

  const barData = dash.value.l2_top5 || dash.value.l2_bar || []
  chartBar.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,20,25,0.94)', borderColor: '#334155', textStyle: { color: '#e2e8f0' } },
    grid: { left: '2%', right: '8%', top: 12, bottom: '3%', containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1f2937' } } },
    yAxis: { type: 'category', data: barData.map((x) => x.name).reverse(), axisLabel: { color: '#cbd5e1' } },
    series: [{
      type: 'bar',
      data: barData.map((x) => x.value).reverse(),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#3B82F6' },
          { offset: 1, color: '#EECA1F' }
        ])
      },
      label: { show: true, position: 'right', color: '#fef08a' }
    }]
  })
  chartBar.off('click')
  chartBar.on('click', (p) => {
    const opt = chartBar.getOption()
    const categories = opt?.yAxis?.[0]?.data || []
    let name = p?.name
    if ((name === undefined || name === '') && typeof p?.dataIndex === 'number' && categories[p.dataIndex]) {
      name = categories[p.dataIndex]
    }
    if (name) emit('drill', { filterL2: String(name) })
  })

  const src = dash.value.trend_30d || []
  chartTrend.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,20,25,0.94)', borderColor: '#334155', textStyle: { color: '#e2e8f0' } },
    grid: { left: '3%', right: '4%', top: 24, bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: src.map((x) => x.date), axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: '#475569' } } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1f2937' } } },
    series: [{
      type: 'line',
      smooth: true,
      symbolSize: 7,
      areaStyle: { color: 'rgba(59, 130, 246, 0.16)' },
      lineStyle: { color: '#3B82F6', width: 3 },
      itemStyle: { color: '#EECA1F' },
      label: { show: true, color: '#cbd5e1', fontSize: 9 },
      data: src.map((x) => x.count)
    }]
  })
}

function resizeCharts () {
  chartPie?.resize()
  chartBar?.resize()
  chartTrend?.resize()
}

onMounted(() => {
  refreshDashboard()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  chartPie?.dispose()
  chartBar?.dispose()
  chartTrend?.dispose()
})

defineExpose({ refreshDashboard })
</script>

<style scoped>
.overview-wrap {
  padding: 4px 0 8px;
}
.overview-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 10px;
}
.overview-alert,
.anomaly-banner {
  margin-bottom: 12px;
}
.anomaly-item {
  line-height: 1.6;
}
.anomaly-l3 {
  margin-right: 8px;
  white-space: nowrap;
}
.dashboard-summary {
  margin-bottom: 12px;
  padding: 12px 14px;
  border-left: 3px solid #3B82F6;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.68);
  color: #dbeafe;
}
.dashboard-summary-title {
  margin-bottom: 6px;
  color: #f8fafc;
  font-size: 14px;
  font-weight: 700;
}
.dashboard-summary p {
  margin: 0;
  line-height: 1.7;
}
.dashboard-summary-ppt {
  margin-top: 8px;
  color: #bfdbfe;
  line-height: 1.6;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
@media (max-width: 1100px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .accuracy-grid {
    grid-template-columns: 1fr;
  }
}
.stat-card {
  border-radius: 10px;
  padding: 16px 18px;
  border: 1px solid #334155;
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.86), rgba(15, 23, 42, 0.82));
}
.stat-num {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
}
.stat-txt {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 6px;
}
.stat-total .stat-num { color: #f8fafc; }
.stat-pending .stat-num { color: #f97316; }
.stat-ok .stat-num { color: #22c55e; }
.stat-archive .stat-num { color: #EECA1F; }
.stat-new .stat-num { color: #3B82F6; }
.accuracy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.accuracy-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 18px;
  border-radius: 10px;
  border: 1px solid #334155;
  background: rgba(30, 41, 59, 0.58);
}
.accuracy-label {
  color: #f1f5f9;
  font-size: 14px;
  font-weight: 600;
}
.accuracy-sub {
  color: #64748b;
  font-size: 12px;
  margin-top: 4px;
}
.accuracy-value {
  min-width: 110px;
  text-align: right;
  color: #EECA1F;
  font-size: 28px;
  font-weight: 800;
}
.chart-grid {
  display: grid;
  grid-template-columns: 0.9fr 1.6fr;
  gap: 12px;
}
@media (max-width: 1000px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
  .chart-wide {
    grid-column: span 1;
  }
}
.chart-card {
  background: rgba(15, 23, 42, 0.66);
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 12px 14px;
}
.chart-wide {
  grid-column: span 2;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: #f1f5f9;
  margin-bottom: 8px;
}
.chart-note {
  color: #64748b;
  font-size: 12px;
}
.chart-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.chart-box {
  width: 100%;
  height: 250px;
}
.chart-tall {
  height: 290px;
}
.trend-panel {
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid #334155;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.62);
}
.trend-title {
  margin-bottom: 10px;
  color: #f1f5f9;
  font-size: 14px;
  font-weight: 600;
}
.trend-items {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  min-height: 110px;
}
.trend-day {
  width: 44px;
  text-align: center;
  color: #94a3b8;
  font-size: 11px;
}
.trend-column {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  height: 78px;
  border-radius: 6px;
  background: rgba(30, 41, 59, 0.8);
  overflow: hidden;
}
.trend-bar-fill {
  width: 100%;
  min-height: 2px;
  background: linear-gradient(180deg, #EECA1F, #3B82F6);
}
.trend-label {
  margin-top: 6px;
}
.trend-acc {
  color: #e2e8f0;
}
.overview-hint {
  margin-top: 16px;
}
</style>
