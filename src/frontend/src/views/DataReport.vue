<template>
  <div class="lotus-report">
    <header class="lotus-head">
      <div class="lotus-title-block">
        <h1 class="lotus-h1">数据汇报 <span class="lotus-h1-en">Data Report</span></h1>
        <p class="lotus-sub">多条件筛选 · 按月聚合 · 导出图表用于汇报</p>
      </div>
      <div class="lotus-brand">LOTUS</div>
    </header>

    <section class="lotus-filters">
      <el-form :inline="true" class="lotus-form" @submit.prevent>
        <el-form-item label="开始 / Start">
          <el-date-picker v-model="dateFrom" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" />
        </el-form-item>
        <el-form-item label="结束 / End">
          <el-date-picker v-model="dateTo" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" />
        </el-form-item>
        <el-form-item label="区域 / Region">
          <el-select v-model="region" style="width: 220px" @change="scheduleLoad">
            <el-option label="全部区域 / All" value="all" />
            <el-option label="中国 / China" value="cn" />
            <el-option label="中国以外（全球其他）/ Outside China" value="rest" />
          </el-select>
        </el-form-item>
        <el-form-item label="Top N">
          <el-input-number v-model="topN" :min="3" :max="15" size="small" @change="scheduleLoad" />
        </el-form-item>
        <el-form-item label="次级排序 / Sub-tag rank">
          <el-input-number v-model="subRank" :min="1" :max="5" size="small" @change="scheduleLoad" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadAll(true)">刷新 / Refresh</el-button>
          <el-button :loading="summaryLoading" :disabled="summaryLoading" @click="loadOpinionSummary">生成汇报文案</el-button>
        </el-form-item>
      </el-form>
    </section>

    <div class="lotus-chart-grid">
      <div v-if="summaryData" class="lotus-card lotus-summary-card">
        <div class="lotus-card-head">
          <div>
            <div class="lotus-card-title">AI 智能汇总 · Qwen2.5 14B</div>
            <div class="lotus-card-title-en">高频问题、风险归因与 PPT 文案</div>
          </div>
          <el-tag size="small" :type="summaryData.cache_hit ? 'info' : 'success'">
            {{ summaryData.cache_hit ? '缓存' : '本地模型' }}
          </el-tag>
        </div>
        <p class="summary-main">{{ summaryData.summary }}</p>
        <div class="summary-grid">
          <div>
            <div class="summary-subtitle">Top 问题</div>
            <ul>
              <li v-for="x in summaryData.top_issues || []" :key="x.label">{{ x.label }}（{{ x.count }}）：{{ x.analysis }}</li>
            </ul>
          </div>
          <div>
            <div class="summary-subtitle">风险与建议</div>
            <p><strong>风险：</strong>{{ (summaryData.risks || []).join('；') || '—' }}</p>
            <p><strong>建议：</strong>{{ (summaryData.actions || []).join('；') || '—' }}</p>
          </div>
        </div>
        <div class="ppt-text">{{ summaryData.ppt_text }}</div>
      </div>

      <div class="lotus-card">
        <div class="lotus-card-head">
          <div>
            <div class="lotus-card-title">整体趋势 · Grouped Monthly</div>
            <div class="lotus-card-title-en">产品质量类（黄）vs 销售服务类（蓝）</div>
          </div>
          <el-button size="small" @click="exportPng(1)">导出 PNG / Export</el-button>
        </div>
        <div ref="chartRef1" class="lotus-echart" />
      </div>

      <div class="lotus-card">
        <div class="lotus-card-head">
          <div>
            <div class="lotus-card-title">细分趋势 · Sub-tag Lines</div>
            <div class="lotus-card-title-en">各一级下第 {{ subRank }} 高频二级 · 按月</div>
          </div>
          <el-button size="small" @click="exportPng(2)">导出 PNG / Export</el-button>
        </div>
        <div ref="chartRef2" class="lotus-echart" />
      </div>

      <div class="lotus-card lotus-card-wide">
        <div class="lotus-card-head">
          <div>
            <div class="lotus-card-title">
              {{ singleIssueActive ? '精准问题趋势 · Single Issue Trend' : 'Top 二级标签 · Stacked by Month' }}
            </div>
            <div class="lotus-card-title-en">
              {{ singleIssueActive ? 'Selected issue monthly tracking for LOTUS special follow-up' : '高频问题月度结构（总量降序）' }}
            </div>
          </div>
          <el-button size="small" @click="exportPng(3)">导出 PNG / Export</el-button>
        </div>
        <div class="issue-filter-panel">
          <div class="issue-filter-title">
            精准问题筛选 · Precise Issue Filter
            <span>聚焦单个问题月度趋势，不影响上方两张图</span>
          </div>
          <el-form :inline="true" class="issue-filter-form" @submit.prevent>
            <el-form-item label="一级 / L1">
              <el-select v-model="issueL1" clearable placeholder="选择一级" style="width: 190px" @change="onIssueL1Change">
                <el-option label="产品质量类 / Product Quality" value="产品质量类" />
                <el-option label="销售服务类 / Sales Service" value="销售服务类" />
              </el-select>
            </el-form-item>
            <el-form-item label="二级 / L2">
              <el-select
                v-model="issueL2"
                clearable
                filterable
                placeholder="选择二级"
                style="width: 240px"
                :disabled="!issueL1"
              >
                <el-option v-for="x in issueL2Options" :key="x" :label="x" :value="x" />
              </el-select>
            </el-form-item>
            <el-form-item label="三级关键词 / L3 Keyword">
              <el-input
                v-model="issueKeyword"
                clearable
                placeholder="如：闪充站问题、充电故障"
                style="width: 260px"
                @keyup.enter="applySingleIssueFilter"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="issueLoading" @click="applySingleIssueFilter">确认查询 / Search</el-button>
              <el-button @click="resetSingleIssueFilter">清空重置 / Reset</el-button>
            </el-form-item>
          </el-form>
        </div>
        <div ref="chartRef3" class="lotus-echart lotus-echart-tall" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  getMonthlyOverviewApi,
  getMonthlySubtagTrendApi,
  getTopSubtagMonthlyApi,
  getSingleIssueTrendApi,
  getOpinionSummaryApi,
  getL2ByL1Api
} from '@/api/review'

const chartRef1 = ref(null)
const chartRef2 = ref(null)
const chartRef3 = ref(null)
let c1 = null
let c2 = null
let c3 = null

const pad2 = (n) => String(n).padStart(2, '0')
const today = new Date()
const defaultTo = `${today.getFullYear()}-${pad2(today.getMonth() + 1)}-${pad2(today.getDate())}`
const sixAgo = new Date(today)
sixAgo.setMonth(sixAgo.getMonth() - 5)
const defaultFrom = `${sixAgo.getFullYear()}-${pad2(sixAgo.getMonth() + 1)}-01`

const dateFrom = ref(defaultFrom)
const dateTo = ref(defaultTo)
const region = ref('all')
const topN = ref(8)
const subRank = ref(1)
const summaryLoading = ref(false)
const summaryData = ref(null)
const issueL1 = ref('')
const issueL2 = ref('')
const issueKeyword = ref('')
const issueL2Options = ref([])
const issueLoading = ref(false)
const singleIssueActive = ref(false)
const latestTopData = ref(null)

let loadTimer = null
const scheduleLoad = () => {
  if (loadTimer) clearTimeout(loadTimer)
  loadTimer = setTimeout(() => {
    summaryData.value = null
    loadAll()
  }, 320)
}

const regionLabelZh = () =>
  ({ all: '全部区域', cn: '中国', rest: '中国以外（全球其他）' }[region.value] || '全部区域')

const baseDark = () => ({
  backgroundColor: 'transparent',
  textStyle: { color: '#e2e8f0' },
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(15,20,25,0.92)',
    borderColor: '#334155',
    textStyle: { color: '#e2e8f0' }
  }
})

function clampReportInts () {
  let n = Number(topN.value)
  if (!Number.isFinite(n)) n = 8
  n = Math.max(3, Math.min(15, Math.round(n)))
  topN.value = n
  let r = Number(subRank.value)
  if (!Number.isFinite(r)) r = 1
  r = Math.max(1, Math.min(5, Math.round(r)))
  subRank.value = r
  return { topN: n, subRank: r }
}

async function loadAll (forceRefresh = false) {
  if (typeof document !== 'undefined') {
    document.activeElement?.blur?.()
  }
  await nextTick()
  if (!dateFrom.value || !dateTo.value) {
    ElMessage.warning('请选择起止日期 / Pick date range')
    return
  }
  const { topN: topNInt, subRank: subRankInt } = clampReportInts()
  summaryData.value = null
  const params = {
    date_from: dateFrom.value,
    date_to: dateTo.value,
    region: region.value
  }
  if (forceRefresh) params.nocache = Date.now()
  try {
    const [o, s, t] = await Promise.all([
      getMonthlyOverviewApi(params),
      getMonthlySubtagTrendApi({
        ...params,
        top_secondary: subRankInt,
        sub_tag_rank: subRankInt
      }),
      getTopSubtagMonthlyApi({ ...params, top_n: topNInt })
    ])
    if (o.code !== 200) throw new Error(o.msg || 'overview')
    if (s.code !== 200) throw new Error(s.msg || 'subtag')
    if (t.code !== 200) throw new Error(t.msg || 'top')
    await nextTick()
    renderOverview(o.data)
    renderSubTrend(s.data)
    latestTopData.value = t.data
    renderTopOrIssue(t.data)
  } catch (e) {
    console.error(e)
    ElMessage.error(e.message || '加载失败 / Load failed')
  }
}

const issueL2Map = {
  产品质量类: [
    '车机及车控问题',
    '辅助驾驶及智能驾驶问题',
    '三电及充电问题',
    '外观及车身问题',
    '内饰及附件问题',
    '底盘及行驶问题',
    '空调及舒适性问题',
    '异响及噪音问题'
  ],
  销售服务类: [
    '销售服务问题',
    '交付服务问题',
    '售后服务问题',
    '维修保养问题',
    '门店体验问题',
    '费用及权益问题',
    '客服响应问题'
  ]
}

async function onIssueL1Change () {
  issueL2.value = ''
  issueL2Options.value = issueL2Map[issueL1.value] || []
  if (!issueL1.value) return
  try {
    const res = await getL2ByL1Api({ l1: issueL1.value === '销售服务类' ? '服务类' : issueL1.value })
    const remote = res.code === 200 && Array.isArray(res.data?.l2_whitelist) ? res.data.l2_whitelist : []
    if (remote.length) issueL2Options.value = remote
  } catch (_) {}
}

function hasIssueFilter () {
  return Boolean((issueL1.value || '').trim() || (issueL2.value || '').trim() || (issueKeyword.value || '').trim())
}

async function applySingleIssueFilter () {
  if (!hasIssueFilter()) {
    resetSingleIssueFilter()
    return
  }
  summaryData.value = null
  issueLoading.value = true
  try {
    const res = await getSingleIssueTrendApi({
      date_from: dateFrom.value,
      date_to: dateTo.value,
      region: region.value,
      l1: issueL1.value,
      l2: issueL2.value,
      keyword: issueKeyword.value
    })
    if (res.code !== 200) throw new Error(res.msg || 'single issue')
    singleIssueActive.value = true
    renderSingleIssueTrend(res.data || {})
  } catch (e) {
    console.error(e)
    ElMessage.error(e.message || '精准问题趋势加载失败')
  } finally {
    issueLoading.value = false
  }
}

function resetSingleIssueFilter () {
  summaryData.value = null
  issueL1.value = ''
  issueL2.value = ''
  issueKeyword.value = ''
  issueL2Options.value = []
  singleIssueActive.value = false
  if (latestTopData.value) renderTopStack(latestTopData.value)
}

function renderTopOrIssue (topData) {
  if (singleIssueActive.value && hasIssueFilter()) {
    applySingleIssueFilter()
  } else {
    renderTopStack(topData)
  }
}

async function loadOpinionSummary () {
  if (!dateFrom.value || !dateTo.value) return
  if (summaryLoading.value) return
  summaryLoading.value = true
  try {
    const res = await getOpinionSummaryApi({
      date_from: dateFrom.value,
      date_to: dateTo.value,
      region: region.value,
      period: 'month',
      limit: 220
    })
    if (res.code !== 200) throw new Error(res.msg || 'summary')
    summaryData.value = res.data || null
  } catch (e) {
    console.error(e)
    ElMessage.warning('AI 汇报文案暂不可用，图表数据不受影响')
  } finally {
    summaryLoading.value = false
  }
}

function renderOverview (data) {
  const el = chartRef1.value
  if (!el) return
  if (!c1) c1 = echarts.init(el)
  const months = data.months || []
  const s = data.series || {}
  const prod = '产品质量类'
  const sale = '销售服务类'
  const period = `${dateFrom.value} — ${dateTo.value}`
  const title = `${period}  区域-${regionLabelZh()}  每月舆情走向`
  const titleEn = `${period}  Region: ${region.value}  Monthly VOC Trend`
  c1.setOption({
    ...baseDark(),
    title: [
      { text: title, left: 'center', top: 8, textStyle: { color: '#f8fafc', fontSize: 15 } },
      { text: titleEn, left: 'center', top: 30, textStyle: { color: '#94a3b8', fontSize: 12 } }
    ],
    grid: { left: 48, right: 24, top: 72, bottom: 40 },
    legend: { data: [prod, sale], top: 52, textStyle: { color: '#cbd5e1' } },
    xAxis: {
      type: 'category',
      data: months,
      axisLine: { lineStyle: { color: '#475569' } },
      axisLabel: { color: '#94a3b8' }
    },
    yAxis: {
      type: 'value',
      name: '客诉量 / Count',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#94a3b8' }
    },
    series: [
      {
        name: prod,
        type: 'bar',
        data: s[prod] || [],
        itemStyle: { color: '#EECA1F' },
        label: { show: true, position: 'top', color: '#fef3c7', fontSize: 10 }
      },
      {
        name: sale,
        type: 'bar',
        data: s[sale] || [],
        itemStyle: { color: '#3B82F6' },
        label: { show: true, position: 'top', color: '#bfdbfe', fontSize: 10 }
      }
    ],
    graphic: [
      {
        type: 'text',
        right: 16,
        top: 10,
        style: { text: 'LOTUS', fill: '#EECA1F', font: 'bold 14px sans-serif' }
      }
    ]
  }, { notMerge: true })
}

function renderSubTrend (data) {
  const el = chartRef2.value
  if (!el) return
  if (!c2) c2 = echarts.init(el)
  const months = data.months || []
  const lines = data.lines || []
  const period = `${dateFrom.value} — ${dateTo.value}`
  c2.setOption({
    ...baseDark(),
    title: [
      {
        text: `${period}  细分问题走势（二级）`,
        left: 'center',
        top: 6,
        textStyle: { color: '#f8fafc', fontSize: 14 }
      },
      {
        text: 'Sub-tag trend (high-frequency L2 per category)',
        left: 'center',
        top: 28,
        textStyle: { color: '#94a3b8', fontSize: 11 }
      }
    ],
    legend: { top: 48, textStyle: { color: '#cbd5e1' } },
    grid: { left: 48, right: 24, top: 88, bottom: 40 },
    xAxis: {
      type: 'category',
      data: months,
      axisLine: { lineStyle: { color: '#475569' } },
      axisLabel: { color: '#94a3b8' }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#94a3b8' }
    },
    series: lines.map((ln) => ({
      name: `${ln.l1_bucket} · ${ln.l2_tag}`,
      type: 'line',
      smooth: true,
      data: ln.values || [],
      itemStyle: { color: ln.color },
      lineStyle: { width: 2, color: ln.color },
      label: { show: true, color: '#e2e8f0', fontSize: 10 },
      symbolSize: 8
    })),
    graphic: [
      {
        type: 'text',
        right: 16,
        top: 8,
        style: { text: 'LOTUS', fill: '#3B82F6', font: 'bold 14px sans-serif' }
      }
    ]
  }, { notMerge: true })
}

function renderTopStack (data) {
  const el = chartRef3.value
  if (!el) return
  if (!c3) c3 = echarts.init(el)
  const months = data.months || []
  const series = data.series || []
  const maxTotal = Math.max(0, ...series.map((x) => x.total || 0))
  const palette = ['#EECA1F', '#3B82F6', '#22c55e', '#a855f7', '#f97316', '#14b8a6', '#ec4899', '#64748b']
  c3.setOption({
    ...baseDark(),
    title: [
      {
        text: 'Top 二级标签 · 按月结构',
        left: 'center',
        top: 4,
        textStyle: { color: '#f8fafc', fontSize: 14 }
      },
      {
        text: 'Top L2 tags stacked by month (sorted by total volume)',
        left: 'center',
        top: 26,
        textStyle: { color: '#94a3b8', fontSize: 11 }
      }
    ],
    legend: { type: 'scroll', top: 46, textStyle: { color: '#cbd5e1' } },
    grid: { left: 44, right: 20, top: 96, bottom: 36 },
    xAxis: {
      type: 'category',
      data: months,
      axisLine: { lineStyle: { color: '#475569' } },
      axisLabel: { color: '#94a3b8' }
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#94a3b8' }
    },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      stack: 'total',
      emphasis: {
        focus: 'series',
        itemStyle: {
          shadowBlur: 10,
          shadowColor: (s.total || 0) === maxTotal ? 'rgba(238,202,31,0.45)' : 'rgba(59,130,246,0.35)'
        }
      },
      data: (s.values || []).map((v) => ({
        value: v,
        itemStyle: {
          borderColor: (s.total || 0) === maxTotal && maxTotal > 0 ? '#fbbf24' : undefined,
          borderWidth: (s.total || 0) === maxTotal && maxTotal > 0 ? 1 : 0
        }
      })),
      itemStyle: { color: palette[i % palette.length] },
      label: { show: Number(s.total) === maxTotal && maxTotal > 0, position: 'top', color: '#fef08a', fontSize: 9 }
    })),
    graphic: [
      {
        type: 'text',
        right: 12,
        top: 6,
        style: { text: 'LOTUS', fill: '#EECA1F', font: 'bold 14px sans-serif' }
      }
    ]
  }, { notMerge: true })
}

function renderSingleIssueTrend (data) {
  const el = chartRef3.value
  if (!el) return
  if (!c3) c3 = echarts.init(el)
  const months = data.months || []
  const values = data.values || []
  const f = data.filters || {}
  const nameParts = [f.l1, f.l2, f.keyword].filter(Boolean)
  const displayName = nameParts.length ? nameParts.join(' · ') : '精准问题'
  c3.setOption({
    ...baseDark(),
    title: [
      {
        text: `精准问题月度趋势 · ${displayName}`,
        left: 'center',
        top: 4,
        textStyle: { color: '#f8fafc', fontSize: 14 }
      },
      {
        text: `Single issue monthly trend (${dateFrom.value} — ${dateTo.value}, ${regionLabelZh()})`,
        left: 'center',
        top: 26,
        textStyle: { color: '#94a3b8', fontSize: 11 }
      }
    ],
    legend: { data: [displayName], top: 48, textStyle: { color: '#cbd5e1' } },
    grid: { left: 46, right: 22, top: 92, bottom: 42 },
    xAxis: {
      type: 'category',
      data: months,
      axisLine: { lineStyle: { color: '#475569' } },
      axisLabel: { color: '#94a3b8' }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#94a3b8' }
    },
    series: [
      {
        name: displayName,
        type: 'line',
        smooth: true,
        symbolSize: 9,
        data: values,
        itemStyle: { color: '#EECA1F' },
        lineStyle: { width: 3, color: '#EECA1F' },
        areaStyle: { color: 'rgba(238,202,31,0.13)' },
        label: { show: true, color: '#fef08a', fontSize: 10 }
      }
    ],
    graphic: [
      {
        type: 'text',
        right: 12,
        top: 6,
        style: { text: 'LOTUS', fill: '#EECA1F', font: 'bold 14px sans-serif' }
      }
    ]
  }, { notMerge: true })
}

function exportPng (which) {
  const names = { 1: 'voc-overview', 2: 'voc-subtrend', 3: 'voc-top-l2' }
  const name = names[which] || 'voc-chart'
  const inst = which === 1 ? c1 : which === 2 ? c2 : c3
  if (!inst) return
  const url = inst.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#0f1419' })
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}-${dateFrom.value}_${dateTo.value}.png`
  a.click()
}

function onResize () {
  c1?.resize()
  c2?.resize()
  c3?.resize()
}

watch([dateFrom, dateTo], () => scheduleLoad())

onMounted(() => {
  window.addEventListener('resize', onResize)
  nextTick(() => loadAll())
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  c1?.dispose()
  c2?.dispose()
  c3?.dispose()
  c1 = c2 = c3 = null
})
</script>

<style scoped>
.lotus-report {
  min-height: 100%;
  background: radial-gradient(ellipse at top, #1a2332 0%, #0f1419 55%);
  color: #e2e8f0;
  padding: 16px 20px 32px;
  box-sizing: border-box;
}
.lotus-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #334155;
}
.lotus-h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #f8fafc;
}
.lotus-h1-en {
  font-size: 16px;
  font-weight: 500;
  color: #94a3b8;
  margin-left: 10px;
}
.lotus-sub {
  margin: 8px 0 0;
  font-size: 13px;
  color: #94a3b8;
}
.lotus-brand {
  font-weight: 800;
  font-size: 20px;
  color: #eecc1f;
  letter-spacing: 0.2em;
  padding: 8px 12px;
  border: 1px solid #475569;
  border-radius: 8px;
  background: rgba(30, 41, 59, 0.5);
}
.lotus-filters {
  background: rgba(30, 41, 59, 0.55);
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 12px 16px 4px;
  margin-bottom: 18px;
}
.lotus-form :deep(.el-form-item__label) {
  color: #cbd5e1;
}
.lotus-chart-grid {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.lotus-card {
  background: rgba(15, 23, 42, 0.65);
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 12px 14px 8px;
}
.lotus-card-wide {
  width: 100%;
}
.lotus-card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 6px;
}
.lotus-card-title {
  font-size: 15px;
  font-weight: 600;
  color: #f1f5f9;
}
.lotus-card-title-en {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.lotus-echart {
  width: 100%;
  height: 320px;
}
.lotus-echart-tall {
  height: 400px;
}
.issue-filter-panel {
  margin: 8px 0 12px;
  padding: 12px 14px 4px;
  border: 1px solid rgba(71, 85, 105, 0.8);
  border-radius: 10px;
  background: rgba(30, 41, 59, 0.46);
}
.issue-filter-title {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  margin-bottom: 8px;
  color: #f8fafc;
  font-size: 14px;
  font-weight: 700;
}
.issue-filter-title span {
  color: #94a3b8;
  font-size: 12px;
  font-weight: 400;
}
.issue-filter-form :deep(.el-form-item__label) {
  color: #cbd5e1;
}
.lotus-summary-card {
  border-color: rgba(238, 202, 31, 0.38);
}
.summary-main {
  margin: 8px 0 12px;
  color: #e5e7eb;
  line-height: 1.7;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  color: #cbd5e1;
  font-size: 13px;
}
.summary-grid ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.summary-grid li {
  margin-bottom: 5px;
}
.summary-subtitle {
  color: #facc15;
  font-weight: 600;
}
.ppt-text {
  margin-top: 12px;
  padding: 10px 12px;
  border-left: 3px solid #3b82f6;
  background: rgba(30, 41, 59, 0.55);
  color: #dbeafe;
  line-height: 1.65;
}
</style>
