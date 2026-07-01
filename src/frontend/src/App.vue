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
        <Overview ref="overviewRef" @drill="drillToWorkbench" />
      </el-tab-pane>

      <el-tab-pane label="复核工作台" name="workbench">
        <keep-alive>
          <OpinionReview ref="reviewRef" @refresh="refreshOverview" />
        </keep-alive>
      </el-tab-pane>

      <el-tab-pane label="数据汇报 / Report" name="report" lazy>
        <DataReport />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import OpinionReview from './components/OpinionReview.vue'
import DataReport from './views/DataReport.vue'
import Overview from './views/Overview.vue'
import { ElMessage } from 'element-plus'

const activeTab = ref('overview')
const reviewRef = ref(null)
const overviewRef = ref(null)

const refreshOverview = () => {
  overviewRef.value?.refreshDashboard?.()
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
      refreshOverview()
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
