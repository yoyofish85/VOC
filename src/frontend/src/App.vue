<template>
  <div class="voc-shell">
    <header class="voc-header">
      <div class="voc-brand">
        <span class="voc-logo">VOC</span>
        <div>
          <h1>舆情复核工作台</h1>
          <p class="voc-sub">企业级舆情 · 三级标签 · 人机协同</p>
        </div>
      </div>
      <div class="voc-header-actions">
        <el-tag type="success" effect="plain" size="small">一级锁定</el-tag>
        <el-tag type="primary" effect="plain" size="small">系统推荐</el-tag>
        <el-tag type="warning" effect="plain" size="small">待复核</el-tag>
        <el-tag type="danger" effect="plain" size="small">异常</el-tag>
        <el-tag round size="small" type="info">V1.5</el-tag>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="voc-tabs" @tab-change="onTabChange">
      <el-tab-pane label="数据概览" name="overview">
        <div class="overview-wrap" v-loading="dashLoading">
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
              <div id="chartL1Pie" class="chart-box"></div>
            </div>
            <div class="chart-card chart-wide">
              <div class="chart-head">
                <span class="chart-title">全局趋势 · Recent 30 Days</span>
                <span class="chart-note">按创建/处理时间统计反馈量</span>
              </div>
              <div id="chartTrend" class="chart-box chart-tall"></div>
            </div>
            <div class="chart-card chart-wide top5-card">
              <div class="chart-title">高频问题 TOP 5 · Top Issues</div>
              <div id="chartL2Bar" class="chart-box chart-tall"></div>
            </div>
          </div>

          <el-alert type="info" show-icon :closable="false" class="overview-hint">
            点击饼图扇区或柱状条目，将切换到「复核工作台」并带上对应一级标签筛选。
          </el-alert>
        </div>
      </el-tab-pane>

      <el-tab-pane label="复核工作台" name="workbench">
        <keep-alive>
          <OpinionReview ref="reviewRef" @refresh="loadDash" />
        </keep-alive>
      </el-tab-pane>

      <el-tab-pane label="数据汇报 / Report" name="report" lazy>
        <DataReport />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import OpinionReview from './components/OpinionReview.vue'
import DataReport from './views/DataReport.vue'
import {
  getDashboardStatsApi,
} from '@/api/review'
import { ElMessage } from 'element-plus'

const activeTab = ref('overview')
const dashError = ref('')
const dashLoading = ref(false)
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
const reviewRef = ref(null)

let chartPie = null
let chartBar = null
let chartTrend = null

const loadDash = async () => {
  dashLoading.value = true
  dashError.value = ''
  try {
    const res = await getDashboardStatsApi()
    if (res.code === 200 && res.data) {
      dash.value = { ...dash.value, ...res.data }
      if (res.data.stale_fallback) {
        dashError.value = res.msg || '当前为缓存或降级数据，后台仍在刷新统计'
      }
    } else if (res.code === 503 || res.timeout) {
      dashError.value = res.msg || '统计数据加载超时，请稍后点击「数据概览」重试'
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
  } finally {
    dashLoading.value = false
  }
}

const fmtAcc = (v) => (v === null || v === undefined ? '--' : `${Number(v).toFixed(2)}%`)

const renderCharts = () => {
  const pieEl = document.getElementById('chartL1Pie')
  const barEl = document.getElementById('chartL2Bar')
  const trEl = document.getElementById('chartTrend')
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
    try {
      const name = p?.name
      if (name && name !== '暂无') drillToWorkbench({ filterL1: String(name) })
    } catch (e) {
      console.error(e)
      ElMessage.error('钻取失败，请从工作台手动筛选')
    }
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
    try {
      const opt = chartBar.getOption()
      const categories = opt?.yAxis?.[0]?.data || []
      let name = p?.name
      if ((name === undefined || name === '') && typeof p?.dataIndex === 'number' && categories[p.dataIndex]) {
        name = categories[p.dataIndex]
      }
      if (name) drillToWorkbench({ filterL2: String(name) })
    } catch (e) {
      console.error(e)
      ElMessage.error('钻取失败，请从工作台手动筛选')
    }
  })

  renderTrend()
}

const renderTrend = () => {
  if (!chartTrend) return
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

const drillToWorkbench = (filters) => {
  activeTab.value = 'workbench'
  nextTick(() => {
    try {
      reviewRef.value?.applyFilters?.(filters)
    } catch (e) {
      console.error(e)
      ElMessage.error('切换工作台筛选失败')
    }
  })
}

const onTabChange = (name) => {
  if (name === 'overview') {
    nextTick(() => {
      loadDash()
    })
  }
  if (name === 'report') {
    nextTick(() => {
      try {
        window.dispatchEvent(new Event('resize'))
      } catch (e) {
        console.error(e)
      }
    })
  }
}

onMounted(async () => {
  await loadDash()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  chartPie?.dispose()
  chartBar?.dispose()
  chartTrend?.dispose()
})

function resizeCharts () {
  chartPie?.resize()
  chartBar?.resize()
  chartTrend?.resize()
}
</script>

<style scoped>
.voc-shell {
  min-height: 100vh;
  background: radial-gradient(ellipse at top, #1a2332 0%, #0f1419 58%);
  padding: 0 20px 28px;
  box-sizing: border-box;
  color: #e2e8f0;
}

.voc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 4px 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.voc-brand {
  display: flex;
  align-items: center;
  gap: 16px;
}

.voc-logo {
  width: 48px;
  height: 48px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid #475569;
  color: #EECA1F;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.voc-brand h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: #f8fafc;
  letter-spacing: 0.02em;
}

.voc-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: #94a3b8;
}

.voc-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.voc-tabs {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 8px 16px 20px;
  box-shadow: 0 18px 45px rgba(0, 0, 0, 0.28);
}

.voc-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

.voc-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  font-weight: 500;
  color: #94a3b8;
}

.voc-tabs :deep(.el-tabs__item.is-active) {
  color: #EECA1F;
}

.voc-tabs :deep(.el-tabs__active-bar) {
  background-color: #EECA1F;
}

.overview-wrap {
  padding: 4px 0 8px;
}

.overview-alert {
  margin-bottom: 12px;
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

@media (max-width: 1000px) {
  .chart-wide {
    grid-column: span 1;
  }
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

.overview-hint {
  margin-top: 16px;
}
</style>
