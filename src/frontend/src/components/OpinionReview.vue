<template>
  <div class="workbench">
    <!-- 流程步骤 -->
    <el-steps :active="stepActive" finish-status="success" align-center class="process-steps">
      <el-step title="上传数据" description="导入 CSV" />
      <el-step title="智能分类" description="规则/聚类/可选 LLM" />
      <el-step title="人工复核" description="可校正一二级·三级关键词自动" />
      <el-step title="归档回流" description="年度归档 / 金标更新" />
    </el-steps>

    <!-- 固定操作条 -->
    <div class="action-bar">
      <div class="action-primary">
        <el-button
          type="primary"
          size="large"
          class="btn-upload"
          :loading="uploadState === 'uploading'"
          :disabled="uploadState === 'uploading'"
          @click="triggerFile"
        >
          <el-icon class="mr6"><UploadFilled /></el-icon>
          {{ uploadState === 'uploading' ? '上传中…' : '上传 CSV 数据' }}
        </el-button>
        <input ref="fileInputRef" type="file" accept=".csv" class="hidden-file" @change="onNativeFile" />

        <el-button
          type="primary"
          size="large"
          :disabled="!selectedBatch || classifyRunning"
          :loading="classifyRunning"
          @click="runClassify(true)"
        >
          <el-icon class="mr6"><Cpu /></el-icon>
          14B 智能分类（推荐）
        </el-button>
        <el-button
          type="info"
          plain
          size="large"
          :disabled="!selectedBatch || classifyRunning"
          :loading="classifyRunning"
          @click="runClassify(false)"
          title="仅做关键词规则匹配；速度快但准确率约 58%，建议大批量初筛或断网时再使用"
        >
          <el-icon class="mr6"><Operation /></el-icon>
          快速规则分类
        </el-button>
      </div>
      <div class="action-status">
        <el-tag v-if="uploadState === 'uploading'" type="warning" effect="dark">上传中…</el-tag>
        <el-tag v-else-if="uploadState === 'done'" type="success">上传完成</el-tag>
        <el-tag v-else-if="uploadState === 'error'" type="danger">上传异常</el-tag>
        <span v-if="lastUploadSummary" class="upload-summary">
          最近导入：文件共 {{ lastUploadSummary.total }} 行 · 有效编号 {{ lastUploadSummary.validN }} 条 · 批次
          <strong>{{ lastUploadSummary.batchLabel || '—' }}</strong> · 新建 {{ lastUploadSummary.inserted }} · 同号更新
          {{ lastUploadSummary.updated }} · 合计写入 {{ lastUploadSummary.imported }} 条
          <template v-if="lastUploadSummary.invalid"> · 无效编号 {{ lastUploadSummary.invalid }}</template>
        </span>
        <span v-if="classifyRunning" class="classify-running-wrap">
          <el-tag type="warning" effect="dark">分类中…</el-tag>
          <span v-if="classifyProgressText" class="classify-progress">{{ classifyProgressText }}</span>
        </span>
        <el-tag v-else-if="classifyState === 'done'" type="success">分类完成</el-tag>
        <el-tag v-else-if="classifyState === 'error'" type="danger">分类异常</el-tag>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="searchKey"
          placeholder="搜索原文 / 舆情ID / 来源 / 关键词（模糊）"
          clearable
          class="filter-search"
          @clear="scheduleSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select
          v-model="selectedBatch"
          placeholder="导入批次"
          clearable
          class="filter-item"
          @change="onBatchChange"
        >
          <el-option
            v-for="batch in batchList"
            :key="batch.upload_batch"
            :label="`${batch.upload_batch}（${batch.count}条）`"
            :value="batch.upload_batch"
          />
        </el-select>
        <el-select
          v-model="reviewStatus"
          placeholder="复核状态"
          clearable
          class="filter-item sm"
          @change="debouncedReloadReviewList"
        >
          <el-option label="全部" value="" />
          <el-option label="已复核" :value="1" />
          <el-option label="未复核" :value="0" />
          <el-option label="待复核/存疑" :value="2" />
        </el-select>
        <el-switch v-model="pendingOnly" active-text="仅待复核/低置信" @change="debouncedReloadReviewList" class="filter-switch" />
        <el-select
          v-model="mismatchFilter"
          placeholder="错分筛选"
          clearable
          class="filter-item sm"
          @change="debouncedReloadReviewList"
        >
          <el-option label="全部" value="" />
          <el-option label="一级错分（模型≠导入一级）" value="l1_disagree" />
          <el-option label="二级错分（模型≠导入二级）" value="l2_disagree" />
          <el-option label="已复核·改正一级" value="human_fixed_l1" />
          <el-option label="已复核·改正二级" value="human_fixed_l2" />
        </el-select>
        <el-input v-model="reviewerName" placeholder="复核人（写入审计）" clearable class="filter-item" style="width: 140px" />
      </div>
      <div class="filter-row second">
        <el-select v-model="filterL1" placeholder="一级标签" clearable filterable class="filter-item" @change="onL1FilterChange">
          <el-option v-for="x in l1Options" :key="x" :label="x" :value="x" />
        </el-select>
        <el-select
          v-model="filterL2"
          placeholder="二级标签"
          clearable
          filterable
          class="filter-item"
          @change="debouncedReloadReviewList"
        >
          <el-option v-for="x in filterL2Options" :key="x" :label="x" :value="x" />
        </el-select>
        <el-input v-model="filterL3" placeholder="三级（模糊）" clearable class="filter-item" @keyup.enter="runReviewQueryNow" />
        <el-select
          v-model="matchFamily"
          placeholder="匹配方式"
          clearable
          class="filter-item sm"
          @change="debouncedReloadReviewList"
        >
          <el-option label="规则/聚类" value="rule" />
          <el-option label="清洗数据" value="clean" />
          <el-option label="LLM" value="llm" />
        </el-select>
        <el-input v-model="filterSource" placeholder="来源" clearable class="filter-item sm" @keyup.enter="runReviewQueryNow" />
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          class="filter-dates"
          @change="onDateRangeChange"
        />
        <el-select v-model="filterYear" placeholder="年度" clearable class="filter-item xs" @change="debouncedReloadReviewList">
          <el-option v-for="y in yearOptions" :key="y" :label="y + '年'" :value="y" />
        </el-select>
        <div class="conf-range">
          <span class="conf-label">置信度</span>
          <el-slider v-model="confRange" range :min="0" :max="1" :step="0.01" @change="debouncedReloadReviewList" />
        </div>
        <el-button type="primary" @click="runReviewQueryNow">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>
    </div>

    <!-- 批量工具条 -->
    <div class="batch-toolbar">
      <el-button
        type="primary"
        size="large"
        class="btn-confirm-archive"
        :disabled="!selectedBatch"
        :loading="batchConfirmLoading"
        @click="confirmWholeBatch"
      >
        确认整批 + 归档年度数据
      </el-button>
      <el-button
        plain
        :disabled="!selectedRows.length"
        :loading="batchConfirmLoading"
        @click="batchConfirmReview"
      >
        仅确认所选（不写年度CSV）({{ selectedRows.length }})
      </el-button>
      <el-button
        type="info"
        plain
        :loading="draftSaveLoading"
        :disabled="!reviewList.length"
        @click="saveDraftReviews"
      >
        暂存复核结果
      </el-button>
      <el-button plain :disabled="!selectedRows.length" @click="batchSaveReviews">仅批量保存（不回流）</el-button>
      <el-button type="warning" plain :disabled="!selectedRows.length" @click="batchMarkPending">批量标为待复核</el-button>
      <el-button plain :disabled="!selectedRows.length || classifyRunning" :loading="classifyRunning" @click="reclassifySelected">
        批量重新分类
      </el-button>
      <el-button :disabled="!selectedBatch" @click="exportCsv">导出当前批次 CSV</el-button>
      <span class="batch-hint"
        >「确认整批 + 归档年度数据」将确认本导入批次内<strong>全部</strong>已分类/已填标签的反馈（不受当前页 20 条限制），回流清洗库并即时写入年度 CSV；批次全部复核完成后自动升级归档标记。「仅确认所选」只处理勾选行且不写年度 CSV。</span
      >
    </div>

    <el-alert
      v-if="batchUnreviewedHint"
      type="error"
      show-icon
      closable
      class="list-hint-alert batch-unreviewed-alert"
      @close="clearBatchUnreviewedFilter"
    >
      <div>{{ batchUnreviewedHint }}</div>
      <div v-if="batchUnreviewedItems.length" class="batch-unreviewed-detail">
        <div v-for="item in batchUnreviewedItems.slice(0, 8)" :key="item.opinion_id" class="batch-unreviewed-line">
          <strong>{{ item.opinion_id }}</strong> · {{ item.reason_text }}
          <span v-if="item.original_text_preview" class="muted"> — {{ item.original_text_preview }}</span>
        </div>
        <div v-if="batchUnreviewedItems.length > 8" class="muted">
          另有 {{ batchUnreviewedItems.length - 8 }} 条未列出，已在下方表格筛选显示。
        </div>
      </div>
      <el-button link type="primary" size="small" class="mt4" @click="clearBatchUnreviewedFilter">清除筛选</el-button>
    </el-alert>

    <el-alert
      v-if="listErrorHint"
      type="warning"
      show-icon
      closable
      class="list-hint-alert"
      @close="listErrorHint = ''"
    >
      {{ listErrorHint }}
    </el-alert>

    <!-- 表格 -->
    <el-table
      :data="reviewList"
      border
      stripe
      class="data-table"
      row-key="id"
      :max-height="620"
      v-loading="loading"
      @selection-change="handleSelectionChange"
      :row-class-name="tableRowClassName"
      :default-sort="{ prop: 'create_time', order: 'descending' }"
    >
      <el-table-column type="selection" width="48" fixed="left" />
      <el-table-column prop="opinion_id" label="舆情ID" width="108" fixed="left" show-overflow-tooltip />
      <el-table-column prop="source" label="来源" width="100" show-overflow-tooltip />
      <el-table-column prop="create_time" label="时间" width="158" sortable />
      <el-table-column prop="upload_batch" label="批次" width="150" show-overflow-tooltip />
      <el-table-column label="原文" min-width="300">
        <template #default="scope">
          <div class="text-cell">
            <el-tooltip :content="String(scope.row.original_text || '')" placement="top" :show-after="500">
              <span class="text-snippet" v-html="cellOriginalHtml(scope.row)"></span>
            </el-tooltip>
            <el-button link type="primary" size="small" @click="openDrawer(scope.row)">复核</el-button>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="模型分类（V3）" min-width="300">
        <template #default="scope">
          <div v-if="parseV3Meta(scope.row)" class="v3-block">
            <div class="v3-line">
              <span class="v3-k">一级·模型</span>
              <el-tag size="small" type="info" effect="plain">{{ v3L1(scope.row) }}</el-tag>
              <el-tag v-if="parseV3Meta(scope.row).clean_hit" size="small" type="warning">清洗</el-tag>
            </div>
            <div class="v3-line">
              <span class="v3-k">二级·模型</span>
              <el-tag size="small" type="primary" effect="plain">{{ v3L2(scope.row) }}</el-tag>
            </div>
            <div class="v3-line">
              <span class="v3-k">三级·模型</span>
              <el-tag size="small" :type="v3L3TagType(scope.row)" :class="{ 'tag-cluster': isClusterL3(scope.row) }">{{ v3L3(scope.row) }}</el-tag>
            </div>
            <div class="v3-meta">
              <span>{{ matchTypeLabel(parseV3Meta(scope.row).match_type) }}</span>
              <span v-if="parseV3Meta(scope.row).confidence != null"> · 置信 {{ Number(parseV3Meta(scope.row).confidence).toFixed(2) }}</span>
            </div>
          </div>
          <div v-else class="legacy-model">
            <el-tag size="small">{{ scope.row.model_class || '未分类' }}</el-tag>
            <span class="muted">{{ scope.row.model_keyword || '—' }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="人工一级" width="130" fixed="right">
        <template #default="scope">
          <el-select
            v-model="scope.row.review_l1"
            filterable
            clearable
            size="small"
            placeholder="一级"
            style="width: 100%"
            @change="onTableReviewL1Change(scope.row)"
          >
            <el-option v-for="x in l1Options" :key="x" :label="x" :value="x" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="人工二级" width="150" fixed="right">
        <template #default="scope">
          <el-select
            v-model="scope.row.review_l2"
            filterable
            allow-create
            default-first-option
            size="small"
            :placeholder="l2PlaceholderForRow(scope.row)"
            style="width: 100%"
            :disabled="isNonIssueL1(rowEffectiveL1(scope.row))"
            :filter-method="rowL2FilterMethod(scope.row)"
            @visible-change="(v) => onL2SelectVisible(scope.row, v)"
            @change="() => scheduleRowAutoSave(scope.row)"
          >
            <el-option v-for="x in scope.row._l2opts || []" :key="x" :label="x" :value="x">
              <span v-html="highlightL2Option(x, scope.row._l2FilterQ)"></span>
            </el-option>
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="人工复核" width="200" fixed="right">
        <template #default="scope">
          <el-select
            v-model="scope.row.review_status"
            placeholder="状态"
            size="small"
            style="width: 100px"
            @change="() => scheduleRowAutoSave(scope.row)"
          >
            <el-option label="未复核" :value="0" />
            <el-option label="已复核" :value="1" />
            <el-option label="存疑" :value="2" />
          </el-select>
          <el-input
            v-model="scope.row.review_note"
            placeholder="备注"
            size="small"
            style="margin-top: 6px"
            clearable
            @input="() => scheduleRowAutoSave(scope.row)"
            @blur="() => scheduleRowAutoSave(scope.row, 0)"
          />
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      class="pager"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
      :current-page="page"
      :page-sizes="[10, 20]"
      :page-size="size"
      layout="total, sizes, prev, pager, next, jumper"
      :total="total"
    />

    <!-- 年度归档 -->
    <div class="year-section">
      <div class="year-head">
        <h3>年度数据归档</h3>
        <div class="year-actions">
          <el-select v-model="summaryYearFilter" placeholder="按年筛选" clearable style="width: 110px" @change="getYearlySummary">
            <el-option v-for="y in yearOptions" :key="y" :label="y + '年'" :value="y" />
          </el-select>
          <el-date-picker
            v-model="summaryMonthPicker"
            type="month"
            placeholder="按月筛选"
            value-format="YYYY-MM"
            style="width: 140px; margin-left: 8px"
            @change="onSummaryMonthChange"
          />
        </div>
      </div>
      <p class="year-tip">
        年度归档已与上方「确认复核 + 归档年度数据」合并为一键操作：在本导入批次<strong>全部</strong>已复核时，将自动写入年度
        CSV、清洗库回流，并对未归档条目更新关键词/金标（reflow_synced=2
        的条目自动跳过重复加权）。下方为按年/月汇总的查阅表，非独立归档入口。
      </p>
      <el-table :data="yearlySummaryList" border stripe v-loading="summaryLoading" size="small">
        <el-table-column prop="year" label="年度" width="90" />
        <el-table-column prop="year_month" label="年月" width="100" />
        <el-table-column prop="review_status" label="状态" width="100">
          <template #default="scope">
            <el-tag size="small">{{ scope.row.review_status == 1 ? '已复核' : '其他' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="review_note" label="备注" width="120" />
        <el-table-column prop="count" label="条数" width="90" />
        <el-table-column prop="latest_time" label="最新时间" min-width="160" />
      </el-table>
    </div>

    <!-- 单条复核抽屉 -->
    <el-drawer v-model="drawerVisible" title="人工复核" size="440px" destroy-on-close @closed="onDrawerClosed">
      <template v-if="drawerRow">
        <div class="drawer-field">
          <label>原文</label>
          <div class="drawer-text">{{ drawerRow.original_text }}</div>
        </div>
        <div class="drawer-field l1-field">
          <div class="drawer-label-row">
            <label>一级标签</label>
            <el-button link type="primary" size="small" @click="l1Unlocked = !l1Unlocked">
              {{ l1Unlocked ? '重新锁定' : '点击解锁编辑' }}
            </el-button>
          </div>
          <div v-if="!l1Unlocked" class="l1-locked">
            <el-tag :type="l1Edited ? 'primary' : 'success'" effect="dark" size="large">{{ drawerL1 }}</el-tag>
            <span class="hint">{{ l1Edited ? '已人工调整（蓝色）' : '与模型一致（绿色）' }}</span>
          </div>
          <el-select v-else v-model="drawerL1" filterable placeholder="选择一级" style="width: 100%">
            <el-option v-for="x in l1Options" :key="x" :label="x" :value="x" />
          </el-select>
        </div>
        <div class="drawer-field">
          <label>二级标签{{ isNonIssueL1(drawerL1) ? '（非问题无需填写）' : '（白名单内切换，可输入新业务二级并随回流写入白名单）' }}</label>
          <el-select
            v-model="drawerL2"
            filterable
            allow-create
            default-first-option
            :placeholder="isNonIssueL1(drawerL1) ? '非问题无需二级' : '选择或输入二级（可搜汉字/拼音）'"
            style="width: 100%"
            :disabled="isNonIssueL1(drawerL1)"
            :filter-method="drawerL2FilterMethod"
          >
            <el-option v-for="opt in drawerL2List" :key="opt" :label="opt" :value="opt">
              <span v-html="highlightL2Option(opt, drawerL2FilterQ)"></span>
            </el-option>
          </el-select>
        </div>
        <div class="drawer-field">
          <label>三级 · 系统关键词（自动提取，不可编辑）</label>
          <el-alert type="info" :closable="false" show-icon class="mb8">以下为系统从原文提取的关键词，用于归档与回流，人工不可改。</el-alert>
          <div class="drawer-kw">{{ drawerKwJoined || '加载中…' }}</div>
          <el-button size="small" link type="primary" @click="refreshDrawerKeywords">重新提取</el-button>
        </div>
        <div class="drawer-field">
          <label>备注</label>
          <el-input v-model="drawerNote" type="textarea" :rows="2" placeholder="修正原因等" />
        </div>
        <div class="drawer-actions">
          <el-button type="primary" :loading="drawerSaving" @click="confirmDrawerReview">确认复核并回流</el-button>
          <el-button @click="drawerVisible = false">关闭</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, computed, defineExpose, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { Search, UploadFilled, Cpu, Operation } from '@element-plus/icons-vue'
import { highlightL2Option, rowL2FilterMethod, matchL2Label } from '@/utils/l2Search'
import {
  getReviewListApi,
  getOpinionDetailApi,
  uploadCsvApi,
  getYearlySummaryApi,
  getUploadBatchesApi,
  saveReviewApi,
  batchSaveReviewApi,
  draftSaveReviewsApi,
  batchClassifyApi,
  getBatchClassifyStatusApi,
  taxonomyOptionsBatchApi,
  getTaxonomyOptionsApi,
  exportReviewsCsvApi,
  confirmReviewApi,
  confirmReviewBatchApi,
  confirmReviewBatchPreviewApi,
  previewKeywordsApi,
  listAnnualCsvApi,
  getL2ByL1Api
} from '@/api/review'

const emit = defineEmits(['refresh'])

const TABLE_PAGE_CAP = 20
const L1_FILTER_DEBOUNCE_MS = 380
const HIGHLIGHT_MAX_CHARS = 900
/** 筛选联动防抖，避免短时间重复拉取列表 */
const REVIEW_LIST_DEBOUNCE_MS = 300
let reviewListAbort = null
let reviewListDebounceTimer = null

/** 图表钻取等非标准一级（销售服务类）与下拉标准名对齐 */
const normalizeWorkbenchL1 = (raw) => {
  const t = (raw || '').trim()
  if (!t) return ''
  if (t === '销售服务类') return '服务类'
  return t
}

const page = ref(1)
const size = ref(20)
const total = ref(0)
const searchKey = ref('')
const selectedBatch = ref(null)
const reviewStatus = ref('')
const filterL1 = ref('')
const filterL2 = ref('')
const filterL3 = ref('')
const matchFamily = ref('')
const filterSource = ref('')
const dateRange = ref(null)
const filterYear = ref('')
const pendingOnly = ref(false)
const mismatchFilter = ref('')
const reviewerName = ref(typeof localStorage !== 'undefined' ? localStorage.getItem('voc_reviewer') || '' : '')
const confRange = ref([0, 1])
const yearOptions = ref(['2024', '2025', '2026'])
const summaryYearFilter = ref('')
const summaryMonthPicker = ref('')
const batchConfirmLoading = ref(false)
const draftSaveLoading = ref(false)
const classifyProgressText = ref('')
/** 后台分类任务：非阻塞轮询（避免 await 长链占用 async 调用栈） */
let classifyPollTimer = null
let classifyPollIter = 0
let classifyJobStartedAt = 0
const CLASSIFY_POLL_INTERVAL_MS = 900
const CLASSIFY_POLL_MAX = 7200

const formatEtaSeconds = (s) => {
  if (!Number.isFinite(s) || s <= 0) return ''
  if (s < 60) return `${Math.ceil(s)} 秒`
  const m = Math.floor(s / 60)
  const r = Math.ceil(s - m * 60)
  return r > 0 && r < 60 ? `${m} 分 ${r} 秒` : `${m} 分钟`
}

const stopClassifyJobPoll = () => {
  if (classifyPollTimer) {
    clearTimeout(classifyPollTimer)
    classifyPollTimer = null
  }
  classifyPollIter = 0
  classifyJobStartedAt = 0
}

const startClassifyJobPoll = (jobId) => {
  stopClassifyJobPoll()
  classifyJobStartedAt = Date.now()
  const tick = async () => {
    classifyPollIter++
    if (classifyPollIter > CLASSIFY_POLL_MAX) {
      stopClassifyJobPoll()
      classifyRunning.value = false
      classifyState.value = 'error'
      classifyProgressText.value = ''
      ElMessage.error('分类任务等待超时，请稍后刷新列表查看进度')
      return
    }
    try {
      const st = await getBatchClassifyStatusApi(jobId)
      if (st.code === 404 || st.status === 'unknown') {
        stopClassifyJobPoll()
        classifyRunning.value = false
        classifyState.value = 'error'
        classifyProgressText.value = ''
        ElMessage.error(st.msg || '分类任务不存在或已过期')
        return
      }
      if (st.status === 'done') {
        stopClassifyJobPoll()
        classifyRunning.value = false
        const final = st.result || { code: 200, msg: '完成' }
        if (final.code === 200) {
          ElMessage.success(final.msg || '分类完成')
          classifyState.value = 'done'
          classifyProgressText.value = ''
          getReviewList()
          emit('refresh')
        } else {
          ElMessage.error(final.msg || '失败')
          classifyState.value = 'error'
          classifyProgressText.value = ''
        }
        return
      }
      if (st.status === 'error') {
        stopClassifyJobPoll()
        classifyRunning.value = false
        classifyState.value = 'error'
        classifyProgressText.value = ''
        ElMessage.error(st.msg || '分类失败')
        return
      }
      if (st.status === 'running') {
        const done = Number(st.processed || 0)
        const total = Number(st.total || 0)
        const detail = (st.msg && String(st.msg).trim()) || ''
        const elapsedSec =
          classifyJobStartedAt > 0
            ? Math.max(0.5, (Date.now() - classifyJobStartedAt) / 1000)
            : 0
        const rate = elapsedSec > 0 && done > 0 ? done / elapsedSec : 0
        const etaSec = rate > 0 && total > done ? (total - done) / rate : 0
        const speedTip =
          rate > 0
            ? `（${rate.toFixed(2)} 条/秒${etaSec > 0 ? '，预计还需 ' + formatEtaSeconds(etaSec) : ''}）`
            : ''
        classifyProgressText.value =
          detail || (total > 0 ? `正在分类 ${done}/${total} 条${speedTip}` : '正在分类…')
      }
      classifyPollTimer = setTimeout(tick, CLASSIFY_POLL_INTERVAL_MS)
    } catch (_) {
      classifyPollTimer = setTimeout(tick, CLASSIFY_POLL_INTERVAL_MS)
    }
  }
  classifyPollTimer = setTimeout(tick, CLASSIFY_POLL_INTERVAL_MS)
}

const reviewList = ref([])
const loading = ref(false)
const listErrorHint = ref('')
/** 整批确认拦截：未就绪条目提示与列表筛选 */
const batchUnreviewedHint = ref('')
const batchUnreviewedItems = ref([])
const batchUnreviewedIdSet = ref(new Set())
const selectedRows = ref([])
const batchList = ref([])
const yearlySummaryList = ref([])
const summaryLoading = ref(false)

/** 分页/筛选前落库：防抖自动保存人工编辑 */
const autoSaveTimers = new Map()
const pendingAutoSaveRows = new Map()
/** 一级→二级选项内存缓存，避免每次选 L1 都请求后端全表聚合 */
const l2OptionsCache = new Map()

// 全量后备二级列表（当金标/体系/API 全部不可用时使用）
const _FALLBACK_L2_LIST = [
  '车端充电问题', 'LFC问题', '家充问题', '公共充电问题', '闪充问题',
  '故障-通用', '故障告警', '车机问题', '座舱问题', 'OTA问题',
  '智能驾驶', 'AD4问题', '泊车问题', '安全性故障',
  '销售服务问题', '交付问题', '售后服务问题', '客服问题', '门店服务问题',
  '功能建议', '体验优化', '性能提升', 'UI/UX建议', '配置诉求',
  '咨询与表扬', '其他非问题', '无标签'
]
const _FALLBACK_L2_BY_L1 = {
  产品质量类: ['车端充电问题', 'LFC问题', '家充问题', '公共充电问题', '闪充问题', '故障-通用', '故障告警', '车机问题', '座舱问题', 'OTA问题', '智能驾驶', 'AD4问题', '泊车问题', '安全性故障'],
  服务类: ['销售服务问题', '交付问题', '售后服务问题', '客服问题', '门店服务问题'],
  体验需求类: ['功能建议', '体验优化', '性能提升', 'UI/UX建议', '配置诉求'],
  非问题: ['咨询与表扬', '其他非问题']
}
const _FALLBACK_L2_FOR_L1 = (l1) => {
  const key = (l1 || '').trim()
  return _FALLBACK_L2_BY_L1[key] || _FALLBACK_L2_LIST
}

const isNonIssueL1 = (l1) => (l1 || '').trim() === '非问题'
const isL2RequiredForL1 = (l1) => !isNonIssueL1(l1)
const rowEffectiveL1 = (row) => (row?.review_l1 || '').trim() || (row?.canonical_l1 || '').trim()
const l2PlaceholderForRow = (row) =>
  isNonIssueL1(rowEffectiveL1(row)) ? '非问题无需二级' : '二级（可搜汉字/拼音）'

const isoReviewTimestamp = () => new Date().toISOString().slice(0, 19)

const buildAutoSavePayload = (row) => {
  const p = {
    opinion_id: row.opinion_id,
    review_status: row.review_status,
    review_note: row.review_note,
    review_l1: row.review_l1,
    review_l2: row.review_l2,
    reviewer: reviewerName.value || undefined
  }
  if (Number(row.review_status) === 1) {
    p.reviewed_at = row.reviewed_at || isoReviewTimestamp()
  }
  return p
}

const execRowAutoSave = async (row) => {
  if (!row?.opinion_id) return
  try {
    const res = await saveReviewApi(buildAutoSavePayload(row))
    if (res.code !== 200) {
      console.error('自动保存失败', res.msg)
      return
    }
    if (res.queued_reflow) {
      ElMessage.info({ message: '已在后台加入回流队列', grouping: true, duration: 2000 })
    }
    if (Number(row.review_status) === 1) {
      row.reviewed_at = row.reviewed_at || isoReviewTimestamp()
    }
  } catch (e) {
    console.error('自动保存请求异常', e)
  } finally {
    pendingAutoSaveRows.delete(row.opinion_id)
  }
}

const scheduleRowAutoSave = (row, delayMs = 800) => {
  if (!row?.opinion_id) return
  const oid = row.opinion_id
  pendingAutoSaveRows.set(oid, row)
  const prev = autoSaveTimers.get(oid)
  if (prev) clearTimeout(prev)
  if (delayMs <= 0) {
    if (prev) clearTimeout(prev)
    autoSaveTimers.delete(oid)
    execRowAutoSave(row)
    return
  }
  const tid = setTimeout(() => {
    autoSaveTimers.delete(oid)
    const latest = pendingAutoSaveRows.get(oid) || row
    execRowAutoSave(latest)
  }, delayMs)
  autoSaveTimers.set(oid, tid)
}

const flushPendingRowSaves = async () => {
  for (const t of autoSaveTimers.values()) clearTimeout(t)
  autoSaveTimers.clear()
  const rows = [...pendingAutoSaveRows.values()]
  pendingAutoSaveRows.clear()
  for (const r of rows) {
    await execRowAutoSave(r)
  }
}

const uploadState = ref('idle')
const classifyState = ref('idle')
const classifyRunning = ref(false)
const fileInputRef = ref(null)
/** 最近一次上传去重统计（供界面展示） */
const lastUploadSummary = ref(null)

const l1Options = ref([])
const filterL2Options = ref([])

const drawerVisible = ref(false)
const drawerRow = ref(null)
const drawerL1 = ref('')
const drawerL1Orig = ref('')
const drawerL2 = ref('')
const drawerL2List = ref([])
const drawerNote = ref('')
const drawerKwJoined = ref('')
const l1Unlocked = ref(false)
const drawerSaving = ref(false)
const drawerL2FilterQ = ref('')

const l1Edited = computed(() => {
  const a = (drawerL1.value || '').trim()
  const b = (drawerL1Orig.value || '').trim()
  return a && b && a !== b
})

watch(reviewerName, (v) => {
  try {
    localStorage.setItem('voc_reviewer', v || '')
  } catch (_) {}
})

watch(drawerL1, (nv, ov) => {
  if (!drawerVisible.value || !drawerRow.value || !l1Unlocked.value) return
  if ((nv || '') === (ov || '')) return
  drawerL2.value = ''
  drawerL2FilterQ.value = ''
  if (isNonIssueL1(nv)) {
    drawerL2List.value = []
    return
  }
  reloadDrawerL2List()
})

let searchTimer = null
let l1FilterTimer = null
let hydrateReq = 0

const stepActive = computed(() => {
  let s = 0
  if (uploadState.value === 'done' || selectedBatch.value) s = 1
  if (classifyState.value === 'done') s = 2
  if (reviewList.value.some((r) => r.review_status === 1)) s = 3
  return s
})

const triggerFile = () => {
  if (uploadState.value === 'uploading') return
  fileInputRef.value?.click()
}

const onNativeFile = async (e) => {
  const f = e.target.files?.[0]
  e.target.value = ''
  if (!f) return
  if (uploadState.value === 'uploading') return
  await doUpload(f)
}

const doUpload = async (file) => {
  if (!file.name.endsWith('.csv')) {
    ElMessage.error('请上传 CSV')
    return
  }
  if (uploadState.value === 'uploading') return
  uploadState.value = 'uploading'
  try {
    const res = await uploadCsvApi(file, () => {})
    if (res.code === 200) {
      lastUploadSummary.value = {
        total: res.total_rows ?? 0,
        validN: (res.inserted_count ?? 0) + (res.updated_count ?? 0),
        batchLabel: res.batch_id || '',
        inserted: res.inserted_count ?? 0,
        updated: res.updated_count ?? 0,
        imported: res.imported_count ?? 0,
        invalid: res.skipped_invalid_id ?? 0
      }
      const importedN = Number(res.imported_count ?? 0)
      const estimatedMin = importedN > 0 ? Math.max(1, Math.ceil((importedN * 3.5) / 60)) : 0
      const tipHtml =
        importedN > 0
          ? `${res.msg || '上传成功'}<br/><strong style="color:#409EFF;">建议点击「14B 智能分类（推荐）」开始分类（预计 ${estimatedMin} 分钟，准确率 90%+）</strong>`
          : (res.msg || '上传成功')
      ElNotification({
        title: '导入完成',
        message: tipHtml,
        type: 'success',
        duration: 8500,
        dangerouslyUseHTMLString: true
      })
      uploadState.value = 'done'
      classifyState.value = 'idle'
      await getBatchList()
      if ((res.imported_count ?? 0) > 0 && res.batch_id) {
        selectedBatch.value = res.batch_id
        page.value = 1
      }
      await getReviewList()
      await getYearlySummary()
      emit('refresh')
    } else if (res.code === 201) {
      ElMessage.warning(res.msg)
      uploadState.value = 'done'
      await getBatchList()
      await getReviewList()
    } else if (res.code === 429) {
      ElMessage.warning(res.msg || '上传过于频繁，请稍后再试')
      uploadState.value = 'error'
    } else {
      ElMessage.error(res.msg || '上传失败')
      uploadState.value = 'error'
    }
  } catch (err) {
    ElMessage.error('上传异常')
    uploadState.value = 'error'
  }
}

const submitBatchClassify = async ({ upload_batch, opinion_ids, use_llm }) => {
  classifyRunning.value = true
  classifyState.value = 'idle'
  classifyProgressText.value = ''
  let asyncJobStarted = false
  const expectedTotal = opinion_ids?.length || 0
  try {
    const payload = { use_llm, async: !!use_llm }
    if (opinion_ids?.length) payload.opinion_ids = opinion_ids
    else payload.upload_batch = upload_batch
    const res = await batchClassifyApi(payload)
    if (res.code === 202 && res.job_id) {
      ElMessage.info(res.msg || '已提交后台分类，请稍候…')
      classifyProgressText.value = `正在分类 0/${res.total || expectedTotal} 条`
      startClassifyJobPoll(res.job_id)
      asyncJobStarted = true
      return
    }
    if (res.code === 200) {
      ElMessage.success(res.msg || '分类完成')
      classifyState.value = 'done'
      classifyProgressText.value = ''
      getReviewList()
      emit('refresh')
    } else {
      ElMessage.error(res.msg || '分类失败')
      classifyState.value = 'error'
    }
  } catch {
    ElMessage.error('分类请求失败')
    classifyState.value = 'error'
  } finally {
    if (!asyncJobStarted) classifyRunning.value = false
  }
}

const reclassifySelected = async () => {
  if (!selectedRows.value.length) return
  if (classifyRunning.value) {
    ElMessage.warning('批量分类进行中，请稍候')
    return
  }
  await submitBatchClassify({
    opinion_ids: selectedRows.value.map((r) => r.opinion_id),
    use_llm: true
  })
}

const runClassify = async (useLlm) => {
  if (!selectedBatch.value) {
    ElMessage.warning('请先选择批次')
    return
  }
  if (classifyRunning.value) {
    ElMessage.warning('批量分类进行中，请稍候')
    return
  }

  // 14B + 已有勾选：弹窗确认范围，避免误对整批跑 14B
  if (useLlm && selectedRows.value.length > 0) {
    const selN = selectedRows.value.length
    const batchRec = batchList.value.find((b) => b.upload_batch === selectedBatch.value)
    const batchN = batchRec?.count ?? total.value ?? selN
    try {
      await ElMessageBox.confirm(
        `当前已勾选 ${selN} 条。是否仅对这 ${selN} 条运行 14B 智能分类？\n` +
          `若选择「整批运行」，将对批次「${selectedBatch.value}」全部 ${batchN} 条执行 14B（耗时更长）。`,
        '14B 智能分类范围',
        {
          confirmButtonText: `仅所选 ${selN} 条`,
          cancelButtonText: '整批运行',
          distinguishCancelAndClose: true,
          type: 'warning'
        }
      )
      await submitBatchClassify({
        opinion_ids: selectedRows.value.map((r) => r.opinion_id),
        use_llm: true
      })
    } catch (action) {
      if (action === 'cancel') {
        await submitBatchClassify({
          upload_batch: selectedBatch.value,
          use_llm: true
        })
      }
    }
    return
  }

  await submitBatchClassify({
    upload_batch: selectedBatch.value,
    use_llm: useLlm
  })
}

const loadL1Options = async () => {
  try {
    const res = await getTaxonomyOptionsApi({})
    if (res.code === 200 && res.data?.l1_list) {
      l1Options.value = res.data.l1_list
    }
  } catch (_) {}
}

const onL1FilterChange = () => {
  filterL2.value = ''
  filterL2Options.value = []
  if (l1FilterTimer) clearTimeout(l1FilterTimer)
  l1FilterTimer = setTimeout(async () => {
    l1FilterTimer = null
    if (filterL1.value) {
      try {
        const res = await getTaxonomyOptionsApi({ l1: filterL1.value })
        let list = res.code === 200 && Array.isArray(res.data?.l2_whitelist) ? res.data.l2_whitelist : []
        if (!list.length) {
          const r2 = await getL2ByL1Api({ l1: filterL1.value })
          if (r2.code === 200 && Array.isArray(r2.data?.l2_whitelist)) list = r2.data.l2_whitelist
        }
        filterL2Options.value = list
      } catch (_) {
        filterL2Options.value = []
      }
    }
    page.value = 1
    getReviewList()
  }, L1_FILTER_DEBOUNCE_MS)
}

const onDateRangeChange = () => {
  debouncedReloadReviewList()
}

const scheduleSearch = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    getReviewList()
  }, 320)
}

watch(searchKey, () => scheduleSearch())

const pickDefaultBatchIfNeeded = () => {
  if (selectedBatch.value || !batchList.value.length) return
  selectedBatch.value = batchList.value[0].upload_batch
}

const needsBatchScope = () =>
  Boolean(
    pendingOnly.value ||
      mismatchFilter.value ||
      filterL1.value ||
      filterL2.value ||
      filterL3.value ||
      matchFamily.value ||
      confRange.value[0] > 0 ||
      confRange.value[1] < 1
  )

const ensureBatchScopeForQuery = () => {
  if (!needsBatchScope()) return true
  pickDefaultBatchIfNeeded()
  if (selectedBatch.value) return true
  listErrorHint.value = '请先选择导入批次后再使用复杂筛选（可缩小全表 JSON 扫描范围）'
  ElMessage.warning(listErrorHint.value)
  return false
}

const buildParams = () => {
  const p = {
    page: page.value,
    size: size.value,
    searchKey: searchKey.value || undefined,
    uploadBatch: selectedBatch.value || undefined,
    reviewStatus: reviewStatus.value === '' || reviewStatus.value === null ? undefined : reviewStatus.value,
    filterL1: filterL1.value || undefined,
    filterL2: filterL2.value || undefined,
    filterL3: filterL3.value || undefined,
    matchFamily: matchFamily.value || undefined,
    filterSource: filterSource.value || undefined,
    year: filterYear.value || undefined,
    pendingOnly: pendingOnly.value ? true : undefined,
    mismatchFilter: mismatchFilter.value || undefined,
    confidenceMin: confRange.value[0] > 0 ? confRange.value[0] : undefined,
    confidenceMax: confRange.value[1] < 1 ? confRange.value[1] : undefined
  }
  if (page.value > 1 && total.value > 0) {
    p.skipTotal = true
  }
  if (dateRange.value?.length === 2) {
    p.dateFrom = dateRange.value[0]
    p.dateTo = dateRange.value[1]
  }
  if (batchUnreviewedIdSet.value.size) {
    p.opinionIds = [...batchUnreviewedIdSet.value].join(',')
  }
  Object.keys(p).forEach((k) => {
    if (p[k] === undefined || p[k] === '') delete p[k]
  })
  return p
}

const hydratePageL2Options = async () => {
  const my = ++hydrateReq
  await nextTick()
  if (my !== hydrateReq) return
  const byL1 = new Map()
  for (const row of reviewList.value) {
    const l1 = effectiveL1ForRow(row)
    if (!l1) continue
    if (!byL1.has(l1)) byL1.set(l1, [])
    byL1.get(l1).push(row)
  }
  const keys = [...byL1.keys()]
  if (!keys.length) return
  try {
    const res = await taxonomyOptionsBatchApi({ l1_list: keys })
    if (my !== hydrateReq) return
    if (res.code === 200 && res.data?.by_l1) {
      const map = res.data.by_l1
      for (const [l1, rows] of byL1) {
        const base = map[l1] || map[normalizeWorkbenchL1(l1)] || []
        if (base.length > 0) {
          l2OptionsCache.set(l1, base)
        }
        for (const row of rows) {
          row._l2opts = mergeL2OptionsList(base, row)
        }
      }
      return
    }
  } catch (_) {}
  if (my !== hydrateReq) return
  for (const [, rows] of byL1) {
    for (const row of rows) {
      row._l2opts = mergeL2OptionsList([], row)
    }
  }
}

const debouncedReloadReviewList = () => {
  if (reviewListDebounceTimer) clearTimeout(reviewListDebounceTimer)
  reviewListDebounceTimer = setTimeout(() => {
    reviewListDebounceTimer = null
    page.value = 1
    getReviewList()
  }, REVIEW_LIST_DEBOUNCE_MS)
}

const runReviewQueryNow = () => {
  if (reviewListDebounceTimer) {
    clearTimeout(reviewListDebounceTimer)
    reviewListDebounceTimer = null
  }
  page.value = 1
  getReviewList()
}

/** 切换批次：先清空表格与统计，取消进行中的请求，避免新旧数据交错与重复请求 */
const onBatchChange = () => {
  if (reviewListAbort) {
    try {
      reviewListAbort.abort()
    } catch (_) {}
    reviewListAbort = null
  }
  if (reviewListDebounceTimer) {
    clearTimeout(reviewListDebounceTimer)
    reviewListDebounceTimer = null
  }
  listErrorHint.value = ''
  reviewList.value = []
  total.value = 0
  selectedRows.value = []
  page.value = 1
  getReviewList()
}

const getReviewList = async () => {
  await flushPendingRowSaves()
  if (!ensureBatchScopeForQuery()) {
    reviewList.value = []
    total.value = 0
    return
  }
  pickDefaultBatchIfNeeded()
  if (reviewListAbort) {
    try {
      reviewListAbort.abort()
    } catch (_) {}
  }
  reviewListAbort = new AbortController()
  const signal = reviewListAbort.signal
  loading.value = true
  listErrorHint.value = ''
  try {
    const res = await getReviewListApi(buildParams(), { signal })
    if (signal.aborted) return
    if (res.code === 200) {
      listErrorHint.value = ''
      reviewList.value = (Array.isArray(res.data) ? res.data : []).map((item) => {
        const row = {
          ...item,
          textExpand: false,
          _l2opts: [],
          _l2FilterQ: ''
        }
        const v3 = parseV3Meta(row)
        row.review_l1 = item.canonical_l1 || item.review_l1 || v3?.l1 || item.model_class || ''
        row.review_l2 = item.review_l2 || v3?.l2 || ''
        return row
      })
      if (Number(res.total) >= 0) {
        total.value = res.total || 0
      }
      await nextTick()
      setTimeout(() => {
        hydratePageL2Options()
      }, 0)
    } else if (res.code === 503 || res.timeout) {
      const msg = res.msg || '列表查询超时，请缩小筛选条件、指定批次或稍后重试'
      listErrorHint.value = msg
      ElMessage.warning(msg)
      reviewList.value = []
      total.value = 0
    } else {
      const msg = res.msg || '获取列表失败'
      listErrorHint.value = msg
      ElMessage.error(msg)
      reviewList.value = []
      total.value = 0
    }
  } catch (e) {
    if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED' || signal.aborted) {
      return
    }
    console.error(e)
    const msg = '列表请求异常，请检查网络或后端日志'
    listErrorHint.value = msg
    ElMessage.error(msg)
    reviewList.value = []
    total.value = 0
  } finally {
    if (!signal.aborted) {
      loading.value = false
    }
  }
}

const clearBatchUnreviewedState = () => {
  batchUnreviewedHint.value = ''
  batchUnreviewedItems.value = []
  batchUnreviewedIdSet.value = new Set()
}

const clearBatchUnreviewedFilter = () => {
  clearBatchUnreviewedState()
  page.value = 1
  getReviewList()
}

const showBatchUnreviewedInList = (preview) => {
  const items = preview?.unreviewed || []
  batchUnreviewedItems.value = items
  batchUnreviewedIdSet.value = new Set(items.map((x) => x.opinion_id).filter(Boolean))
  const noL1 = items.filter((x) => x.reason === 'no_l1').length
  const missL2 = items.filter((x) => x.reason === 'missing_l2').length
  const parts = []
  if (noL1) parts.push(`${noL1} 条缺一级`)
  if (missL2) parts.push(`${missL2} 条缺二级`)
  batchUnreviewedHint.value = `整批确认已暂停：本批次尚有 ${items.length} 条未就绪（${parts.join('，')}）。请先在下方列表补全标签后再试。`
  page.value = 1
  getReviewList()
}

const resetFilters = () => {
  clearBatchUnreviewedState()
  searchKey.value = ''
  reviewStatus.value = ''
  filterL1.value = ''
  filterL2.value = ''
  filterL3.value = ''
  matchFamily.value = ''
  filterSource.value = ''
  dateRange.value = null
  filterYear.value = ''
  pendingOnly.value = false
  mismatchFilter.value = ''
  confRange.value = [0, 1]
  page.value = 1
  runReviewQueryNow()
}

async function applyFilters (f) {
  if (!f) return
  try {
    if (f.filterL1) {
      const nl = normalizeWorkbenchL1(f.filterL1)
      filterL1.value = nl
      filterL2.value = f.filterL2 || ''
      try {
        const res = await getTaxonomyOptionsApi({ l1: nl })
        let list = res.code === 200 && Array.isArray(res.data?.l2_whitelist) ? res.data.l2_whitelist : []
        if (!list.length) {
          const r2 = await getL2ByL1Api({ l1: nl })
          if (r2.code === 200 && Array.isArray(r2.data?.l2_whitelist)) list = r2.data.l2_whitelist
        }
        filterL2Options.value = list
      } catch (_) {
        filterL2Options.value = []
      }
    } else if (f.filterL2) {
      filterL2.value = f.filterL2
    }
    page.value = 1
    await getReviewList()
  } catch (e) {
    console.error(e)
    ElMessage.error('应用图表筛选失败')
  }
}

defineExpose({ applyFilters })

const getBatchList = async () => {
  try {
    const res = await getUploadBatchesApi()
    if (res.code === 200) {
      batchList.value = Array.isArray(res.data) ? res.data : []
      pickDefaultBatchIfNeeded()
    }
  } catch (_) {
    batchList.value = []
  }
}

const getYearlySummary = async () => {
  summaryLoading.value = true
  try {
    const params = {}
    if (summaryYearFilter.value) params.filterYear = summaryYearFilter.value
    if (summaryMonthPicker.value) params.filterMonth = summaryMonthPicker.value
    const res = await getYearlySummaryApi(params)
    if (res.code === 200) yearlySummaryList.value = res.data || []
  } finally {
    summaryLoading.value = false
  }
}

const onSummaryMonthChange = () => {
  getYearlySummary()
}

const handleSelectionChange = (sel) => {
  selectedRows.value = sel
}

const highlightKeyword = (text, keyword) => {
  if (!text) return ''
  if (!keyword) return text
  const keywordList = String(keyword).split(',').map((k) => k.trim()).filter(Boolean)
  if (!keywordList.length) return text
  const reg = new RegExp(`(${keywordList.join('|')})`, 'g')
  return String(text).replace(reg, '<span class="kw-hl">$1</span>')
}

const cellOriginalHtml = (row) => {
  const t = row?.original_text || ''
  const kw = row?.model_keyword
  if (!t) return ''
  if (t.length > HIGHLIGHT_MAX_CHARS) {
    const cut = t.slice(0, 360) + '…'
    return kw ? highlightKeyword(cut, kw) : cut
  }
  return highlightKeyword(t, kw)
}

const parseV3Meta = (row) => {
  if (!row) return null
  if (Object.prototype.hasOwnProperty.call(row, '_v3Cached')) return row._v3Cached
  const raw = row?.v3_label_meta
  let o = null
  if (raw != null && raw !== '') {
    if (typeof raw === 'object') o = raw
    else {
      try {
        const p = JSON.parse(String(raw))
        if (p && (p.l1 || p.level1 || p.match_type)) o = p
      } catch (_) {}
    }
  }
  row._v3Cached = o
  return o
}

const v3L1 = (row) => {
  const o = parseV3Meta(row)
  return o?.l1 || o?.level1 || '—'
}
const v3L2 = (row) => {
  const o = parseV3Meta(row)
  return o?.l2 || o?.level2 || '—'
}
/** 模型一级/二级用于下拉合并（排除占位符） */
const cleanV3L1 = (row) => {
  const s = (v3L1(row) || '').trim()
  return s && s !== '—' ? s : ''
}
const cleanV3L2 = (row) => {
  const s = (v3L2(row) || '').trim()
  return s && s !== '—' ? s : ''
}
/** 人工当前选中的一级优先，避免 canonical_l1（后端推导）在用户改一级后仍指向旧类 */
const effectiveL1ForRow = (row) => {
  return (row?.review_l1 || '').trim() || (row?.canonical_l1 || '').trim() || cleanV3L1(row)
}
const modelKeywordL2 = (row) => {
  const kw = row?.model_keyword
  if (kw == null || kw === '') return ''
  return String(kw).split(',')[0].trim()
}
/** 金标/体系合并后再并入当前人工值与模型识别值，避免漏项 */
const mergeL2OptionsList = (baseList, row) => {
  let b = Array.isArray(baseList) ? [...baseList] : []
  if (!b.length) {
    const l1 = (row?.canonical_l1 || '').trim() || effectiveL1ForRow(row)
    b = [..._FALLBACK_L2_FOR_L1(l1)]
  }
  const cur = (row?.review_l2 || '').trim()
  const v3 = cleanV3L2(row)
  const mk = modelKeywordL2(row)
  return [...new Set([...b, cur, v3, mk].filter(Boolean))]
}

const onL2SelectVisible = (row, open) => {
  if (!open) {
    row._l2FilterQ = ''
  } else {
    if (!row._l2opts?.length) {
      row._l2opts = mergeL2OptionsList([], row)
    }
    loadRowL2Options(row)
  }
}

const drawerL2FilterMethod = (query, option) => {
  drawerL2FilterQ.value = query ?? ''
  const v = option?.value ?? option?.label ?? ''
  return matchL2Label(v, query)
}
const v3L3 = (row) => {
  const o = parseV3Meta(row)
  return o?.l3 || o?.level3 || '—'
}
const isClusterL3 = (row) => {
  const o = parseV3Meta(row)
  const mt = o?.match_type || ''
  return mt === 'cluster_l3' || o?.l3_source === 'cluster'
}
const v3L3TagType = (row) => (isClusterL3(row) ? 'warning' : 'info')

const matchTypeLabel = (mt) => {
  const m = {
    rule: '规则',
    clean_l1: '清洗一级',
    cluster_l3: '聚类·三级',
    llm_l3: 'LLM·三级',
    llm_l2: 'LLM·二级',
    llm: 'LLM',
    llm_fallback: 'LLM兜底',
    routed: '路由',
    scored: '打分',
    exact: '精确',
    fuzzy: '模糊',
    generic_l3: '通用·三级',
    generic: '通用'
  }
  return m[mt] || mt || '—'
}

const tableRowClassName = ({ row }) => {
  const o = parseV3Meta(row)
  const conf = o?.confidence != null ? Number(o.confidence) : null
  let cls = []
  if (row.review_status === 1) cls.push('row-done')
  else if (row.review_status === 2) cls.push('row-doubt')
  if (row.review_status !== 1 && conf != null && conf < 0.52) cls.push('row-lowconf')
  if (batchUnreviewedIdSet.value.has(row.opinion_id)) cls.push('row-batch-blocked')
  return cls.join(' ')
}

const batchSaveReviews = async () => {
  if (!selectedRows.value.length) return
  const now = new Date().toISOString().slice(0, 19)
  try {
    const reviews = selectedRows.value.map((row) => ({
      opinion_id: row.opinion_id,
      review_status: row.review_status,
      review_note: row.review_note,
      review_l1: row.review_l1,
      review_l2: row.review_l2,
      reviewer: reviewerName.value || undefined,
      reviewed_at: row.review_status === 1 ? now : undefined
    }))
    const res = await batchSaveReviewApi({ reviews })
    if (res.code === 200) {
      ElMessage.success(res.msg || '已保存（未触发回流）')
      getReviewList()
      getYearlySummary()
      emit('refresh')
    } else ElMessage.error(res.msg || '失败')
  } catch {
    ElMessage.error('保存失败')
  }
}

const confirmWholeBatch = async () => {
  if (!selectedBatch.value) {
    ElMessage.warning('请先选择导入批次')
    return
  }
  await flushPendingRowSaves()
  batchConfirmLoading.value = true
  try {
    const preview = await confirmReviewBatchPreviewApi({ upload_batch: selectedBatch.value })
    if (preview.code !== 200) {
      ElMessage.error(preview.msg || '无法预览批次状态')
      return
    }
    if (preview.unreviewed_count > 0) {
      showBatchUnreviewedInList(preview)
      await ElMessageBox.alert(
        `批次「${selectedBatch.value}」尚有 ${preview.unreviewed_count} 条未就绪，无法整批确认。\n\n` +
          `可确认：${preview.confirmable_count} 条 · 已复核：${preview.already_confirmed_count} 条\n\n` +
          '未就绪条目已在下方详情列表筛选显示，请补全一级/二级标签后重试。',
        '存在未复核/未就绪条目',
        { type: 'warning', confirmButtonText: '我知道了' }
      )
      return
    }
    if (preview.all_done) {
      ElMessage.info(preview.msg || '该批次已全部复核完成')
      return
    }
    if (!preview.confirmable_count) {
      ElMessage.warning('该批次没有可确认的条目')
      return
    }
    try {
      await ElMessageBox.confirm(
        `将对批次「${selectedBatch.value}」内 ${preview.confirmable_count} 条待确认反馈执行整批复核、回流清洗库并写入年度 CSV。是否继续？`,
        '确认整批 + 归档',
        { type: 'warning', confirmButtonText: '确认整批', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
    ElMessage.info('正在整批确认、回流并写入年度数据…')
    const res = await confirmReviewBatchApi({
      upload_batch: selectedBatch.value,
      reviewer: reviewerName.value,
      with_reflow: true,
      also_yearly: true
    })
    if (res.code === 200) {
      clearBatchUnreviewedFilter()
      ElNotification({
        title: '整批确认与归档',
        message: res.msg || `已整批确认 ${res.confirmed ?? 0} 条`,
        type: 'success',
        duration: 7000
      })
      selectedRows.value = []
      await getReviewList()
      getYearlySummary()
      emit('refresh')
    } else if (res.code === 409 && res.unreviewed?.length) {
      showBatchUnreviewedInList(res)
      ElMessage.warning(res.msg || '存在未就绪条目，已暂停整批确认')
    } else {
      ElMessage.error(res.msg || '整批确认失败')
    }
  } catch {
    ElMessage.error('请求失败')
  } finally {
    batchConfirmLoading.value = false
  }
}

const batchConfirmReview = async () => {
  if (!selectedRows.value.length) return
  // 先落库所有防抖中的行内编辑，避免确认后又被旧的自动保存覆盖。
  await flushPendingRowSaves()
  const miss = selectedRows.value.filter((r) => !(r.review_l1 || '').trim())
  if (miss.length) {
    ElMessage.warning('勾选行须填写「人工一级」')
    return
  }
  const missL2 = selectedRows.value.filter(
    (r) => isL2RequiredForL1(r.review_l1) && !(r.review_l2 || '').trim()
  )
  if (missL2.length) {
    ElMessage.warning('业务类（非「非问题」）须填写「人工二级」')
    return
  }
  const nConfirm = selectedRows.value.length
  batchConfirmLoading.value = true
  try {
    ElMessage.info('正在确认所选并回流（不写年度 CSV）…')
    const res = await confirmReviewApi({
      reviews: selectedRows.value.map((row) => ({
        opinion_id: row.opinion_id,
        review_l1: (row.review_l1 || '').trim(),
        review_l2: (row.review_l2 || '').trim(),
        review_note: row.review_note
      })),
      reviewer: reviewerName.value,
      with_reflow: true,
      also_yearly: false,
      write_yearly: false
    })
    if (res.code === 200) {
      let detail = res.reflow_async
        ? `成功确认所选 ${nConfirm} 条；清洗库回流已在后台执行。全量保存请点击「确认整批 + 归档年度数据」。`
        : `成功确认所选 ${nConfirm} 条；未写年度 CSV。全量保存请点击「确认整批 + 归档年度数据」。`
      if (res.reflow_async && res.msg) {
        detail = res.msg
      }
      ElNotification({
        title: '确认所选',
        message: detail,
        type: 'success',
        duration: 6500
      })
      selectedRows.value = []
      await getReviewList()
      getYearlySummary()
      emit('refresh')
    } else ElMessage.error(res.msg || '失败')
  } catch {
    ElMessage.error('请求失败')
  } finally {
    batchConfirmLoading.value = false
  }
}

const onTableReviewL1Change = (row) => {
  row.review_l2 = ''
  row._l2FilterQ = ''
  const l1 = (row.review_l1 || '').trim()
  if (!l1) {
    row._l2opts = []
    scheduleRowAutoSave(row)
    return
  }
  row.canonical_l1 = l1
  if (isNonIssueL1(l1)) {
    row._l2opts = []
    scheduleRowAutoSave(row)
    return
  }
  row._l2opts = mergeL2OptionsList([], row)
  loadRowL2Options(row)
  scheduleRowAutoSave(row)
}

const loadRowL2Options = async (row) => {
  const l1 = effectiveL1ForRow(row)
  if (!l1) {
    row._l2opts = []
    return
  }
  if (l2OptionsCache.has(l1)) {
    row._l2opts = mergeL2OptionsList(l2OptionsCache.get(l1), row)
    return
  }
  try {
    const res = await getL2ByL1Api({ l1 })
    const base = res.code === 200 && Array.isArray(res.data?.l2_whitelist) ? res.data.l2_whitelist : []
    if (base.length > 0) {
      l2OptionsCache.set(l1, base)
    }
    row._l2opts = mergeL2OptionsList(base, row)
  } catch (_) {
    row._l2opts = mergeL2OptionsList([], row)
  }
}

const reloadDrawerL2List = async () => {
  if (!drawerRow.value) return
  const l1 = (drawerL1.value || '').trim()
  if (!l1) {
    drawerL2List.value = []
    return
  }
  if (l2OptionsCache.has(l1)) {
    const syn = { ...drawerRow.value, review_l1: l1, review_l2: drawerL2.value }
    drawerL2List.value = mergeL2OptionsList(l2OptionsCache.get(l1), syn)
    return
  }
  try {
    const res = await getL2ByL1Api({ l1 })
    const base = res.code === 200 && Array.isArray(res.data?.l2_whitelist) ? res.data.l2_whitelist : []
    if (base.length > 0) {
      l2OptionsCache.set(l1, base)
    }
    const syn = { ...drawerRow.value, review_l1: l1, review_l2: drawerL2.value }
    drawerL2List.value = mergeL2OptionsList(base, syn)
  } catch (_) {
    const syn = { ...drawerRow.value, review_l1: l1, review_l2: drawerL2.value }
    drawerL2List.value = mergeL2OptionsList([], syn)
  }
}

const saveDraftReviews = async () => {
  if (!reviewList.value.length) {
    ElMessage.warning('当前页无数据')
    return
  }
  draftSaveLoading.value = true
  try {
    const reviews = reviewList.value
      .filter((row) => row.review_status !== 1 && (row.review_l1 || '').trim())
      .map((row) => ({
        opinion_id: row.opinion_id,
        review_l1: (row.review_l1 || '').trim(),
        review_l2: (row.review_l2 || '').trim(),
        review_note: row.review_note
      }))
    if (!reviews.length) {
      ElMessage.warning('请至少为需要暂存的行填写「人工一级」；已复核或未填一级的行不会提交')
      return
    }
    const res = await draftSaveReviewsApi({
      reviews,
      reviewer: reviewerName.value || undefined
    })
    if (res.code === 200) {
      ElMessage.success(res.msg || '已暂存')
      if (!res.reflow_async && Number(res.reflowed || 0) === 0) {
        ElMessage.info(
          '数据尚未进入清洗库回流队列；如需确保进入年度归档，请执行「批量确认复核」并勾选归档年度数据。'
        )
      }
      await getReviewList()
      getYearlySummary()
      emit('refresh')
    } else ElMessage.error(res.msg || '暂存失败')
  } catch (e) {
    console.error(e)
    ElMessage.error('暂存请求失败')
  } finally {
    draftSaveLoading.value = false
  }
}

const batchMarkPending = async () => {
  if (!selectedRows.value.length) return
  try {
    const reviews = selectedRows.value.map((row) => ({
      opinion_id: row.opinion_id,
      review_status: 2,
      review_note: row.review_note
    }))
    const res = await batchSaveReviewApi({ reviews })
    if (res.code === 200) {
      ElMessage.success('已标为待复核/存疑')
      getReviewList()
    } else ElMessage.error(res.msg)
  } catch {
    ElMessage.error('操作失败')
  }
}

const exportCsv = () => {
  if (!selectedBatch.value) return
  const url = exportReviewsCsvApi({ uploadBatch: selectedBatch.value })
  window.open(url, '_blank')
}

const refreshDrawerKeywords = async () => {
  if (!drawerRow.value?.original_text) return
  const res = await previewKeywordsApi({ text: drawerRow.value.original_text })
  if (res.code === 200 && res.data) {
    drawerKwJoined.value = res.data.joined || ''
  }
}

const openDrawer = async (row) => {
  const o = parseV3Meta(row)
  drawerRow.value = { ...row }
  if (row.original_text_truncated) {
    try {
      const res = await getOpinionDetailApi({ opinion_id: row.opinion_id })
      if (res.code === 200 && res.data?.original_text) {
        drawerRow.value = { ...drawerRow.value, original_text: res.data.original_text }
        row.original_text = res.data.original_text
      }
    } catch (_) {
      ElMessage.warning('原文全文加载失败，当前仅显示预览')
    }
  }
  drawerL1Orig.value = o?.l1 || row.model_class || ''
  drawerL1.value = (row.review_l1 || row.canonical_l1 || o?.l1 || row.model_class || '').trim()
  drawerL2.value = (row.review_l2 || o?.l2 || '').trim()
  drawerNote.value = row.review_note || ''
  l1Unlocked.value = false
  drawerVisible.value = true
  drawerL2FilterQ.value = ''
  await reloadDrawerL2List()
  await refreshDrawerKeywords()
}

const onDrawerClosed = () => {
  drawerRow.value = null
  drawerKwJoined.value = ''
  drawerL2FilterQ.value = ''
}

const confirmDrawerReview = async () => {
  if (!drawerRow.value) return
  if (!(drawerL1.value || '').trim()) {
    ElMessage.warning('请填写一级标签')
    return
  }
  if (isL2RequiredForL1(drawerL1.value) && !(drawerL2.value || '').trim()) {
    ElMessage.warning('业务类须填写二级标签')
    return
  }
  drawerSaving.value = true
  try {
    ElMessage.info('复核成功，正在回流数据…')
    const res = await confirmReviewApi({
      reviews: [
        {
          opinion_id: drawerRow.value.opinion_id,
          review_l1: drawerL1.value.trim(),
          review_l2: (drawerL2.value || '').trim(),
          review_note: drawerNote.value
        }
      ],
      reviewer: reviewerName.value,
      with_reflow: true,
      also_yearly: false
    })
    if (res.code === 200) {
      ElNotification({
        title: '完成',
        message: res.msg || '数据已回流，关键词/白名单已更新',
        type: 'success',
        duration: 5000
      })
      drawerVisible.value = false
      getReviewList()
      getYearlySummary()
      emit('refresh')
    } else ElMessage.error(res.msg || '失败')
  } catch {
    ElMessage.error('请求失败')
  } finally {
    drawerSaving.value = false
  }
}

const handleSizeChange = (v) => {
  size.value = Math.min(TABLE_PAGE_CAP, Number(v) || 10)
  getReviewList()
}
const handleCurrentChange = (v) => {
  page.value = v
  getReviewList()
}

const loadAnnualYearOptions = async () => {
  try {
    const res = await listAnnualCsvApi()
    if (res.code === 200 && res.data?.years?.length) {
      yearOptions.value = res.data.years
    }
  } catch (_) {}
}

onMounted(async () => {
  if (size.value > TABLE_PAGE_CAP) size.value = TABLE_PAGE_CAP
  await loadL1Options()
  await loadAnnualYearOptions()
  await getBatchList()
  await nextTick()
  requestAnimationFrame(() => {
    getReviewList()
    getYearlySummary()
  })
})

onUnmounted(() => {
  stopClassifyJobPoll()
  flushPendingRowSaves()
  if (reviewListAbort) {
    try {
      reviewListAbort.abort()
    } catch (_) {}
  }
  if (reviewListDebounceTimer) clearTimeout(reviewListDebounceTimer)
})
</script>

<style scoped>
.workbench {
  width: 100%;
  padding: 0 4px 24px;
}

.process-steps {
  margin-bottom: 20px;
  padding: 12px 8px;
  background: #fafbfc;
  border-radius: 10px;
  border: 1px solid #ebeef5;
}

.action-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  position: sticky;
  top: 0;
  z-index: 15;
  background: linear-gradient(180deg, #fff 70%, rgba(255, 255, 255, 0.92));
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;
}

.action-primary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.btn-upload {
  min-width: 160px;
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(64, 158, 255, 0.35);
}

.action-status {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.classify-running-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  max-width: 100%;
}

.upload-summary {
  font-size: 12px;
  color: #606266;
  line-height: 1.4;
  max-width: 520px;
}

.classify-progress {
  margin-left: 4px;
  font-size: 12px;
  color: #e6a23c;
  font-weight: 500;
  max-width: 420px;
  line-height: 1.35;
}

.hidden-file {
  display: none;
}

.mr6 {
  margin-right: 6px;
}

.filter-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.filter-row.second {
  margin-top: 12px;
}

.filter-search {
  width: min(420px, 100%);
}

.filter-item {
  width: 160px;
}
.filter-item.sm {
  width: 130px;
}
.filter-item.xs {
  width: 100px;
}

.filter-dates {
  width: 260px;
}

.filter-switch {
  margin-left: 4px;
}

.conf-range {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 200px;
  flex: 1;
  max-width: 280px;
}
.conf-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.batch-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.list-hint-alert {
  margin-bottom: 12px;
}

.batch-toolbar .btn-confirm-archive {
  font-weight: 600;
  padding-left: 22px;
  padding-right: 22px;
  letter-spacing: 0.02em;
}
.batch-hint {
  font-size: 13px;
  color: #909399;
  margin-left: 8px;
}

.data-table {
  width: 100%;
  font-size: 13px;
}

/* 大数据量时减轻布局/绘制压力（兼容 Chromium；不影响交互列） */
.data-table :deep(.el-table__body tr) {
  content-visibility: auto;
  contain-intrinsic-size: 52px 100%;
}

.data-table :deep(.cell) {
  line-height: 1.55;
}

.text-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  word-break: break-word;
}

.text-snippet {
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-height: 7.8em;
}

.v3-block {
  font-size: 12px;
  line-height: 1.55;
}
.v3-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.v3-k {
  color: #909399;
  min-width: 72px;
}
.v3-meta {
  color: #909399;
  font-size: 11px;
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px dashed #ebeef5;
}
.tag-cluster {
  font-weight: 600;
}
.legacy-model {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.muted {
  color: #909399;
  font-size: 12px;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.year-section {
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
}
.year-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.year-head h3 {
  margin: 0;
  font-size: 16px;
  color: #303133;
}

.year-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.year-tip {
  font-size: 12px;
  color: #909399;
  margin: 0 0 12px;
  line-height: 1.5;
}

.drawer-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.drawer-label-row label {
  margin: 0;
}

.l1-locked {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.l1-locked .hint {
  font-size: 12px;
  color: #909399;
}

.mb8 {
  margin-bottom: 8px;
}

.drawer-kw {
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  padding: 10px;
  background: #f5f7fa;
  border-radius: 8px;
  word-break: break-all;
  min-height: 48px;
}

.mb12 {
  margin-bottom: 12px;
}
.drawer-field {
  margin-bottom: 16px;
}
.drawer-field label {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.drawer-text {
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  max-height: 200px;
  overflow: auto;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 6px;
}
.drawer-actions {
  margin-top: 20px;
  display: flex;
  gap: 10px;
}

:deep(.row-done) {
  background: #ebeef5 !important;
  color: #909399;
}
:deep(.row-done) .el-select .el-input__wrapper {
  background: #f5f7fa;
}
:deep(.row-doubt) {
  background: #fdf6ec !important;
}
:deep(.row-lowconf) {
  box-shadow: inset 3px 0 0 #e6a23c;
  background: #fffaf0 !important;
}
:deep(.row-batch-blocked) {
  box-shadow: inset 3px 0 0 #f56c6c;
  background: #fef0f0 !important;
}
.batch-unreviewed-alert {
  margin-bottom: 10px;
}
.batch-unreviewed-detail {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.55;
}
.batch-unreviewed-line {
  margin-top: 4px;
}
.batch-unreviewed-line .muted,
.batch-unreviewed-detail .muted {
  color: #909399;
}
.mt4 {
  margin-top: 4px;
}
:deep(.kw-hl) {
  color: #b88230;
  font-weight: 600;
  background: #fff7e6;
  padding: 0 2px;
  border-radius: 2px;
}
:deep(.l2-search-hl) {
  background: #fdf6ec;
  color: #c45656;
  font-weight: 600;
  padding: 0 2px;
  border-radius: 2px;
}
</style>
